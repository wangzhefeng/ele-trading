from typing import Dict, List

import numpy as np
import pandas as pd


class EsArbitraryRangeScheduler_withMaxDemand:
    """多变压器公共母线下的分布式储能调度模型。

    模型把一个系统内的多台变压器看成连接在同一段上级母线：
    - 每台变压器后有自己的负荷和一套可选储能；
    - 储能放电可以先反送到母线，再分配给同系统其他变压器后的负荷；
    - 储能充电仍发生在自身变压器后侧，并计入该变压器下行容量。
    """

    def __init__(
        self,
        schedule_time_range: List,
        system_demand_load: List,
        local_demand_load_matrix: List[List],
        ele_prices: List,
        ele_types: List,
        devices_info: List[Dict],
        current_soc_list: List[float],
        max_demand_price: float,
        freq_minutes: int,
        park_transform_capacity: float | None = None,
        cross_flow_penalty: float = 1e-6,
        smooth_penalty_weight: float = 1e-4,
        ramp_rate_fraction_per_step: float | None = 0.5,
        charge_target_penalty_weight: float = 0.0,
        discharge_target_penalty_weight: float = 0.0,
    ):
        self.schedule_time_range = schedule_time_range
        self.schedule_time_length = len(self.schedule_time_range)
        self.system_demand_load = system_demand_load
        self.local_demand_load_matrix = local_demand_load_matrix
        self.ele_prices = ele_prices
        self.ele_types = ele_types
        self.devices_num = len(devices_info)
        self.current_soc_list = current_soc_list
        self.transform_capacity_list = [i["transform_capacity"] for i in devices_info]
        self.charge_loss_list = [i["charge_loss"] for i in devices_info]
        self.discharge_loss_list = [i["discharge_loss"] for i in devices_info]
        self.es_charge_max_list = [i["es_charge_max"] for i in devices_info]
        self.es_discharge_max_list = [i["es_charge_min"] for i in devices_info]
        self.es_capacity_max_list = [i["es_capacity_max"] * i["usable_depth"] for i in devices_info]
        self.es_capacity_min_list = [i["es_capacity_min"] for i in devices_info]
        self.max_demand_price = max_demand_price
        self.freq_minutes = freq_minutes
        # 兼容旧调用参数。新模型已经取消园区总变压器容量约束，因此不使用该字段。
        self.park_transform_capacity = park_transform_capacity
        self.cross_flow_penalty = cross_flow_penalty
        self.smooth_penalty_weight = smooth_penalty_weight
        self.ramp_rate_fraction_per_step = ramp_rate_fraction_per_step
        self.charge_target_penalty_weight = charge_target_penalty_weight
        self.discharge_target_penalty_weight = discharge_target_penalty_weight
        self.last_problem_status = None
        self.last_objective_value = None
        self.last_solver = None
        self.last_solution: dict[str, np.ndarray | list[np.ndarray]] = {}

        # 输入维度必须在建模前失败，否则规则递推里的数组错位会很难定位。
        if len(self.system_demand_load) != self.schedule_time_length:
            raise ValueError("system_demand_load length must match schedule_time_range length.")
        if len(self.local_demand_load_matrix) != self.devices_num:
            raise ValueError("local_demand_load_matrix row count must match devices_info length.")
        for local_load in self.local_demand_load_matrix:
            if len(local_load) != self.schedule_time_length:
                raise ValueError("each local_demand_load_matrix row must match schedule_time_range length.")
        if len(self.current_soc_list) != self.devices_num:
            raise ValueError("current_soc_list length must match devices_info length.")
        if self.ramp_rate_fraction_per_step is not None and self.ramp_rate_fraction_per_step < 0:
            raise ValueError("ramp_rate_fraction_per_step must be >= 0 or None.")

    @staticmethod
    def _charge_allowed(ts: pd.Timestamp) -> bool:
        """两充两放策略中的固定充电窗口。"""

        return (0 <= ts.hour < 6) or (12 <= ts.hour < 14)

    @staticmethod
    def _build_discharge_allowed_mask(schedule_time_range: List, ele_types: List) -> np.ndarray:
        """按 v5 固定两放窗口构造允许放电时段。"""

        time_range = pd.to_datetime(schedule_time_range)
        return (
            ((time_range.hour >= 6) & (time_range.hour < 12))
            | ((time_range.hour >= 16) & (time_range.hour < 24))
        )

    def _build_daily_soc_target_indices(self) -> tuple[list[int], list[int]]:
        """返回每日充电窗口和放电窗口结束点，用于可选 SOC 软目标。"""

        charge_target_indices = []
        discharge_target_indices = []
        indexed_times = pd.Series(range(self.schedule_time_length), index=pd.to_datetime(self.schedule_time_range))

        for _, day_indices in indexed_times.groupby(indexed_times.index.normalize()):
            day_times = day_indices.index
            for start_hour, end_hour in ((0, 6), (12, 14)):
                mask = (day_times.hour >= start_hour) & (day_times.hour < end_hour)
                if mask.any():
                    charge_target_indices.append(int(day_indices.loc[mask].iloc[-1]))
            for start_hour, end_hour in ((6, 12), (16, 24)):
                mask = (day_times.hour >= start_hour) & (day_times.hour < end_hour)
                if mask.any():
                    discharge_target_indices.append(int(day_indices.loc[mask].iloc[-1]))

        return charge_target_indices, discharge_target_indices

    def modeling2solving(self):
        """按固定规则递推生成调度，不再调用优化求解器。"""

        row = self.devices_num
        column = self.schedule_time_length
        time_ratio = self.freq_minutes / 60
        local_d = np.array(self.local_demand_load_matrix)
        park_d = np.array(self.system_demand_load)
        discharge_allowed_mask = self._build_discharge_allowed_mask(self.schedule_time_range, self.ele_types)

        charge_array = np.zeros((row, column))
        discharge_array = np.zeros((row, column))
        soc_matrix = np.zeros((row, column))
        grid_to_load_array = np.zeros((row, column))
        transformer_import = np.zeros((row, column))
        transformer_export = np.zeros((row, column))
        allocation_values = [np.zeros((row, column)) for _ in range(row)]
        soc = np.array(self.current_soc_list, dtype=float)
        charge_eff = np.array(self.charge_loss_list, dtype=float)
        discharge_eff = np.array(self.discharge_loss_list, dtype=float)
        charge_limit = np.array(self.es_charge_max_list, dtype=float)
        discharge_limit = np.array(self.es_charge_max_list, dtype=float)
        soc_max = np.array(self.es_capacity_max_list, dtype=float)
        soc_min = np.array(self.es_capacity_min_list, dtype=float)
        transform_capacity = np.array(self.transform_capacity_list, dtype=float)

        for t, ts in enumerate(pd.to_datetime(self.schedule_time_range)):
            local_load = np.maximum(local_d[:, t], 0.0)
            charge_power = np.zeros(row)

            if self._charge_allowed(ts):
                for i in range(row):
                    soc_room_power = max((soc_max[i] - soc[i]) / (charge_eff[i] * time_ratio), 0.0)
                    transformer_room_power = max(transform_capacity[i] - local_load[i], 0.0)
                    charge_power[i] = min(charge_limit[i], soc_room_power, transformer_room_power)
                    soc[i] += charge_power[i] * charge_eff[i] * time_ratio
                charge_array[:, t] = -charge_power
                grid_to_load_array[:, t] = local_load
                transformer_import[:, t] = local_load + charge_power

            elif discharge_allowed_mask[t]:
                remaining_load = local_load.copy()
                remaining_park_discharge = max(float(park_d[t]), 0.0)
                for source_i in range(row):
                    source_power = min(
                        discharge_limit[source_i],
                        max((soc[source_i] - soc_min[source_i]) * discharge_eff[source_i] / time_ratio, 0.0),
                    )
                    source_power = min(source_power, remaining_park_discharge)
                    if source_power <= 0:
                        continue

                    local_take = min(source_power, remaining_load[source_i], remaining_park_discharge)
                    allocation_values[source_i][source_i, t] = local_take
                    remaining_load[source_i] -= local_take
                    remaining_park_discharge -= local_take
                    source_power -= local_take

                    cross_export_room = transform_capacity[source_i]
                    for target_j in range(row):
                        if target_j == source_i or source_power <= 0 or remaining_park_discharge <= 0:
                            continue
                        cross_take = min(
                            source_power,
                            remaining_load[target_j],
                            remaining_park_discharge,
                            cross_export_room,
                        )
                        if cross_take <= 0:
                            continue
                        allocation_values[source_i][target_j, t] = cross_take
                        remaining_load[target_j] -= cross_take
                        remaining_park_discharge -= cross_take
                        source_power -= cross_take
                        cross_export_room -= cross_take

                    discharge_power = float(allocation_values[source_i][:, t].sum())
                    discharge_array[source_i, t] = discharge_power
                    soc[source_i] -= discharge_power / discharge_eff[source_i] * time_ratio

                supplied_by_storage = np.sum(np.stack([a[:, t] for a in allocation_values], axis=0), axis=0)
                grid_to_load_array[:, t] = np.maximum(local_load - supplied_by_storage, 0.0)
                for target_j in range(row):
                    cross_in = sum(allocation_values[source_i][target_j, t] for source_i in range(row) if source_i != target_j)
                    transformer_import[target_j, t] = grid_to_load_array[target_j, t] + cross_in
                for source_i in range(row):
                    transformer_export[source_i, t] = sum(
                        allocation_values[source_i][target_j, t]
                        for target_j in range(row)
                        if target_j != source_i
                    )

            else:
                grid_to_load_array[:, t] = local_load
                transformer_import[:, t] = local_load

            soc = np.clip(soc, soc_min, soc_max)
            soc_matrix[:, t] = soc

        self.last_solution = {
            "allocation_by_source": allocation_values,
            "grid_to_load": grid_to_load_array,
            "grid_import_total": np.maximum(
                np.asarray(park_d + np.sum(-charge_array, axis=0) - np.sum(discharge_array, axis=0)),
                0.0,
            ),
            "transformer_import": transformer_import,
            "transformer_export": transformer_export,
            "soc": soc_matrix,
        }
        self.last_solver = "rule_based"
        self.last_problem_status = "RULE_BASED"
        self.last_objective_value = 0.0
        return 0.0, charge_array, discharge_array

    def schedule_generate(self, charge_array, discharge_array):
        """把求解器矩阵结果拆回每套储能的时间序列策略。"""

        schedule_list = []
        for device_i in range(self.devices_num):
            power_array_i = np.around(charge_array[device_i] + discharge_array[device_i], decimals=3)
            power_array_i = np.asarray(list(map(lambda x: 0.0 if abs(x) < 0.1 else x, power_array_i.tolist())))
            schedule_i_df = pd.DataFrame({
                "value": power_array_i
            }, index=self.schedule_time_range)
            schedule_list.append(schedule_i_df)

        return schedule_list

    def run(self):
        _, charge_array, discharge_array = self.modeling2solving()
        schedule_list = self.schedule_generate(charge_array, discharge_array)

        return schedule_list
