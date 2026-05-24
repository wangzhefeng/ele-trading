from typing import Dict, List

import cvxpy as cp
import numpy as np
import pandas as pd
from cvxpy.error import SolverError


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

        # 输入维度必须在建模前失败，否则 cvxpy 里的广播错误会很难定位。
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
        """按电价类型构造允许放电窗口。

        上午 06:00~12:00 固定允许放电；晚间若存在尖峰，则只允许尖峰和最后一个
        尖峰之后的高/峰时段放电，否则允许晚间高/峰时段放电。
        """

        time_range = pd.to_datetime(schedule_time_range)
        ele_types = pd.Series(ele_types, index=time_range).astype(str).str.strip()
        allowed = pd.Series(False, index=time_range)
        high_types = {"高", "峰"}
        sharp_types = {"尖", "尖峰"}

        morning_mask = (time_range.hour >= 6) & (time_range.hour < 12)
        allowed.loc[morning_mask] = True

        for _, day_types in ele_types.groupby(ele_types.index.normalize()):
            evening_types = day_types[(day_types.index.hour >= 16) & (day_types.index.hour < 24)]
            if evening_types.empty:
                continue

            sharp_times = evening_types[evening_types.isin(sharp_types)].index
            if len(sharp_times) == 0:
                allowed.loc[evening_types[evening_types.isin(high_types)].index] = True
                continue

            last_sharp_time = sharp_times.max()
            allowed.loc[sharp_times] = True
            post_sharp_high = evening_types[
                (evening_types.index > last_sharp_time)
                & evening_types.isin(high_types)
            ]
            allowed.loc[post_sharp_high.index] = True

        return allowed.to_numpy(dtype=bool)

    @staticmethod
    def _sum_or_zero(expressions, column: int):
        if not expressions:
            return cp.Constant(np.zeros(column))
        total = expressions[0]
        for expression in expressions[1:]:
            total = total + expression
        return total

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
        # row 是系统内变压器/储能数量，column 是当前优化窗口的时间点数量。
        row = self.devices_num
        column = self.schedule_time_length

        # 设备参数按列向量组织，便于和 (row, column) 的变量矩阵广播约束。
        c_l_in_vec = np.array(self.charge_loss_list).reshape((row, 1))
        c_l_out_vec = np.array(self.discharge_loss_list).reshape((row, 1))
        e_c_max_vec = np.array(self.es_charge_max_list).reshape((row, 1))
        e_c_min_vec = np.array(self.es_discharge_max_list).reshape((row, 1))
        e_s_max_vec = np.array(self.es_capacity_max_list).reshape((row, 1))
        e_s_min_vec = np.array(self.es_capacity_min_list).reshape((row, 1))
        transform_capacity_vec = np.array(self.transform_capacity_list).reshape((row, 1))

        time_ratio = self.freq_minutes / 60
        local_d = np.array(self.local_demand_load_matrix)
        p = np.array(self.ele_prices)
        e_r_vec = np.array(self.current_soc_list)

        # e_c_in_matrix <= 0 表示充电，e_c_out_matrix >= 0 表示放电。
        e_c_in_matrix = cp.Variable((row, column))
        e_c_out_matrix = cp.Variable((row, column))
        soc_matrix = cp.Variable((row, column))
        grid_to_load_matrix = cp.Variable((row, column), nonneg=True)
        # allocation_by_source[i][j, t] 表示储能 i 在 t 时刻供给变压器 j 后负荷的放电功率。
        allocation_by_source = [
            cp.Variable((row, column), nonneg=True)
            for _ in range(row)
        ]
        charge_power_matrix = -e_c_in_matrix

        # 系统从上级电网购电 = 直接供各变压器负荷的电网功率 + 各储能充电功率。
        system_grid_import = cp.sum(grid_to_load_matrix, axis=0) + cp.sum(charge_power_matrix, axis=0)
        net_power_matrix = e_c_in_matrix + e_c_out_matrix
        cross_flow_terms = []
        constraints = []

        # SOC 轨迹约束。这里保留 e_c_in 为负的符号约定，因此充电会增加 SOC。
        charge_cumsum = cp.cumsum(e_c_in_matrix, axis=1)
        discharge_cumsum = cp.cumsum(e_c_out_matrix, axis=1)
        constraints += [
            soc_matrix == e_r_vec.reshape((row, 1))
            - cp.multiply(charge_cumsum, time_ratio * c_l_in_vec)
            - cp.multiply(discharge_cumsum, time_ratio / c_l_out_vec)
        ]

        for source_i in range(row):
            # 每台储能的全部放电必须被分配给本系统内各变压器后的负荷。
            constraints += [cp.sum(allocation_by_source[source_i], axis=0) == e_c_out_matrix[source_i, :]]

        for target_j in range(row):
            supplied_by_storage = self._sum_or_zero(
                [allocation_by_source[source_i][target_j, :] for source_i in range(row)],
                column,
            )
            cross_in = self._sum_or_zero(
                [allocation_by_source[source_i][target_j, :] for source_i in range(row) if source_i != target_j],
                column,
            )
            # 变压器后负荷由电网经母线供电和系统内储能放电共同满足。
            constraints += [grid_to_load_matrix[target_j, :] + supplied_by_storage == local_d[target_j, :]]
            # 下行容量：电网供本地负荷、其他变压器储能反送后再下行的电，以及本地储能充电。
            constraints += [
                grid_to_load_matrix[target_j, :] + cross_in + charge_power_matrix[target_j, :]
                <= transform_capacity_vec[target_j, 0]
            ]

        for source_i in range(row):
            cross_out = self._sum_or_zero(
                [allocation_by_source[source_i][target_j, :] for target_j in range(row) if target_j != source_i],
                column,
            )
            cross_flow_terms.append(cross_out)
            # 反送容量：储能 i 跨变压器供给其他负荷时，需要经过自身变压器反送到母线。
            constraints += [cross_out <= transform_capacity_vec[source_i, 0]]

        # 储能系统每个时段的充放电功率限制。
        constraints += [e_c_out_matrix <= e_c_max_vec]
        constraints += [e_c_out_matrix >= 0]
        constraints += [e_c_in_matrix <= 0]
        constraints += [e_c_in_matrix >= e_c_min_vec]

        # 储能器容量限制。
        constraints += [soc_matrix >= e_s_min_vec]
        constraints += [soc_matrix <= e_s_max_vec]

        # 分时段充放电硬约束：按设备逐一限制，避免聚合功率抵消掩盖单台设备的违规充放电。
        discharge_allowed_mask = self._build_discharge_allowed_mask(self.schedule_time_range, self.ele_types)
        for j, ts in enumerate(pd.to_datetime(self.schedule_time_range)):
            if self._charge_allowed(ts):
                constraints += [e_c_out_matrix[:, j] == 0]
            elif discharge_allowed_mask[j]:
                constraints += [e_c_in_matrix[:, j] == 0]
            else:
                constraints += [
                    e_c_in_matrix[:, j] == 0,
                    e_c_out_matrix[:, j] == 0,
                ]

        smooth_penalty = 0.0
        if self.smooth_penalty_weight > 0 and column > 1:
            smooth_delta = cp.Variable((row, column - 1), nonneg=True)
            net_power_step_delta = net_power_matrix[:, 1:] - net_power_matrix[:, :-1]
            constraints += [
                smooth_delta >= net_power_step_delta,
                smooth_delta >= -net_power_step_delta,
            ]
            smooth_penalty = self.smooth_penalty_weight * cp.sum(smooth_delta)

        if self.ramp_rate_fraction_per_step is not None and column > 1:
            ramp_limit = np.repeat(self.ramp_rate_fraction_per_step * e_c_max_vec, column - 1, axis=1)
            net_power_step_delta = net_power_matrix[:, 1:] - net_power_matrix[:, :-1]
            constraints += [
                net_power_step_delta <= ramp_limit,
                net_power_step_delta >= -ramp_limit,
            ]

        charge_target_indices, discharge_target_indices = self._build_daily_soc_target_indices()
        soc_target_penalty = 0.0
        if self.charge_target_penalty_weight > 0 and charge_target_indices:
            charge_shortfall = cp.Variable((row, len(charge_target_indices)), nonneg=True)
            for k, target_idx in enumerate(charge_target_indices):
                constraints += [soc_matrix[:, target_idx] + charge_shortfall[:, k] >= e_s_max_vec[:, 0]]
            soc_target_penalty += self.charge_target_penalty_weight * cp.sum(charge_shortfall)
        if self.discharge_target_penalty_weight > 0 and discharge_target_indices:
            discharge_surplus = cp.Variable((row, len(discharge_target_indices)), nonneg=True)
            for k, target_idx in enumerate(discharge_target_indices):
                constraints += [soc_matrix[:, target_idx] - discharge_surplus[:, k] <= e_s_min_vec[:, 0]]
            soc_target_penalty += self.discharge_target_penalty_weight * cp.sum(discharge_surplus)

        cross_flow_total = self._sum_or_zero(cross_flow_terms, column)
        energy_cost = time_ratio * system_grid_import @ p
        max_demand_cost = self.max_demand_price * cp.max(system_grid_import)
        cross_flow_penalty = self.cross_flow_penalty * cp.sum(cross_flow_total)
        obj = cp.Minimize(
            energy_cost
            + max_demand_cost
            + cross_flow_penalty
            + smooth_penalty
            + soc_target_penalty
        )

        prob = cp.Problem(obj, constraints)
        solver_errors = []
        result = None
        for solver in (cp.HIGHS, cp.CLARABEL, cp.SCS):
            try:
                result = prob.solve(verbose=False, solver=solver)
            except SolverError as exc:
                solver_errors.append(f"{solver}: {exc}")
                continue
            if prob.status in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
                self.last_solver = solver
                break
            solver_errors.append(f"{solver}: status={prob.status}")

        self.last_problem_status = prob.status
        self.last_objective_value = result
        if prob.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
            raise ValueError(
                f"optimization failed with status: {prob.status}; "
                f"solver attempts: {'; '.join(solver_errors)}"
            )

        charge_array = e_c_in_matrix.value
        discharge_array = e_c_out_matrix.value
        if charge_array is None or discharge_array is None:
            raise ValueError(f"optimization returned empty solution with status: {prob.status}")
        if np.isnan(charge_array).any() or np.isnan(discharge_array).any():
            raise ValueError(f"optimization returned NaN solution with status: {prob.status}")

        allocation_values = [allocation.value for allocation in allocation_by_source]
        grid_to_load_array = grid_to_load_matrix.value
        charge_power_array = -charge_array
        transformer_import = np.zeros((row, column))
        transformer_export = np.zeros((row, column))
        for target_j in range(row):
            cross_in_array = sum(
                allocation_values[source_i][target_j, :]
                for source_i in range(row)
                if source_i != target_j
            )
            transformer_import[target_j, :] = (
                grid_to_load_array[target_j, :] + cross_in_array + charge_power_array[target_j, :]
            )
        for source_i in range(row):
            transformer_export[source_i, :] = sum(
                allocation_values[source_i][target_j, :]
                for target_j in range(row)
                if target_j != source_i
            )

        self.last_solution = {
            "allocation_by_source": allocation_values,
            "grid_to_load": grid_to_load_array,
            "grid_import_total": np.sum(grid_to_load_array, axis=0) + np.sum(charge_power_array, axis=0),
            "transformer_import": transformer_import,
            "transformer_export": transformer_export,
            "soc": soc_matrix.value,
        }
        return result, charge_array, discharge_array

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
