from __future__ import annotations

from typing import Dict, List

import cvxpy as cp
import numpy as np
import pandas as pd
from cvxpy.error import SolverError

from .config import GridImportFormula, SchedulerConfig, SolverType


class EsDistributionScheduler:
    """多变压器公共母线下的分布式储能调度模型（统一版本）。

    通过 SchedulerConfig 参数控制行为差异，合并原 v1-v5 所有变体：
    - solver=LP: CVXPY 线性规划（v1-v4）
    - solver=RULE_BASED: 固定规则递推（v5）
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
        config: SchedulerConfig,
        park_transform_capacity: float | None = None,
        cross_flow_penalty: float = 1e-6,
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
        self.park_transform_capacity = park_transform_capacity
        self.cross_flow_penalty = cross_flow_penalty
        self.config = config
        self.last_problem_status = None
        self.last_objective_value = None
        self.last_solver = None
        self.last_solution: dict[str, np.ndarray | list[np.ndarray]] = {}

        if len(self.system_demand_load) != self.schedule_time_length:
            raise ValueError("system_demand_load length must match schedule_time_range length.")
        if len(self.local_demand_load_matrix) != self.devices_num:
            raise ValueError("local_demand_load_matrix row count must match devices_info length.")
        for local_load in self.local_demand_load_matrix:
            if len(local_load) != self.schedule_time_length:
                raise ValueError("each local_demand_load_matrix row must match schedule_time_range length.")
        if len(self.current_soc_list) != self.devices_num:
            raise ValueError("current_soc_list length must match devices_info length.")
        if config.ramp_rate_fraction_per_step is not None and config.ramp_rate_fraction_per_step < 0:
            raise ValueError("ramp_rate_fraction_per_step must be >= 0 or None.")

    @staticmethod
    def _charge_allowed(ts: pd.Timestamp) -> bool:
        return (0 <= ts.hour < 6) or (12 <= ts.hour < 14)

    @staticmethod
    def _build_discharge_mask_price_type(schedule_time_range: List, ele_types: List) -> np.ndarray:
        """v1-v4: 按电价类型构造允许放电窗口。"""
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
                (evening_types.index > last_sharp_time) & evening_types.isin(high_types)
            ]
            allowed.loc[post_sharp_high.index] = True

        return allowed.to_numpy(dtype=bool)

    @staticmethod
    def _build_discharge_mask_fixed_window(schedule_time_range: List, ele_types: List) -> np.ndarray:
        """v5: 固定两放窗口 06-12, 16-24。"""
        time_range = pd.to_datetime(schedule_time_range)
        return (
            ((time_range.hour >= 6) & (time_range.hour < 12))
            | ((time_range.hour >= 16) & (time_range.hour < 24))
        )

    def _build_discharge_allowed_mask(self) -> np.ndarray:
        if self.config.discharge_mask_mode == "fixed_window":
            return self._build_discharge_mask_fixed_window(self.schedule_time_range, self.ele_types)
        return self._build_discharge_mask_price_type(self.schedule_time_range, self.ele_types)

    @staticmethod
    def _sum_or_zero(expressions, column: int):
        if not expressions:
            return cp.Constant(np.zeros(column))
        total = expressions[0]
        for expression in expressions[1:]:
            total = total + expression
        return total

    def _build_daily_soc_target_indices(self) -> tuple[list[int], list[int]]:
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

    # ── 主入口 ─────────────────────────────────────────────────────────────

    def modeling2solving(self):
        if self.config.solver == SolverType.LP:
            return self._solve_lp()
        return self._solve_rule()

    # ── LP 求解 (v1-v4) ───────────────────────────────────────────────────

    def _solve_lp(self):
        row = self.devices_num
        column = self.schedule_time_length

        c_l_in_vec = np.array(self.charge_loss_list).reshape((row, 1))
        c_l_out_vec = np.array(self.discharge_loss_list).reshape((row, 1))
        e_c_max_vec = np.array(self.es_charge_max_list).reshape((row, 1))
        e_c_min_vec = np.array(self.es_discharge_max_list).reshape((row, 1))
        e_s_max_vec = np.array(self.es_capacity_max_list).reshape((row, 1))
        e_s_min_vec = np.array(self.es_capacity_min_list).reshape((row, 1))
        transform_capacity_vec = np.array(self.transform_capacity_list).reshape((row, 1))

        time_ratio = self.freq_minutes / 60
        local_d = np.array(self.local_demand_load_matrix)
        park_d = np.array(self.system_demand_load)
        p = np.array(self.ele_prices)
        e_r_vec = np.array(self.current_soc_list)

        e_c_in_matrix = cp.Variable((row, column))
        e_c_out_matrix = cp.Variable((row, column))
        soc_matrix = cp.Variable((row, column))
        grid_to_load_matrix = cp.Variable((row, column), nonneg=True)
        allocation_by_source = [
            cp.Variable((row, column), nonneg=True) for _ in range(row)
        ]
        charge_power_matrix = -e_c_in_matrix

        # grid_import 公式：v1-v3 用 sum(grid_to_load)+charge，v4 用 park_d+charge-discharge
        if self.config.grid_import_formula == GridImportFormula.SUM_LOAD:
            system_grid_import = cp.sum(grid_to_load_matrix, axis=0) + cp.sum(charge_power_matrix, axis=0)
        else:
            system_grid_import = park_d + cp.sum(charge_power_matrix, axis=0) - cp.sum(e_c_out_matrix, axis=0)

        net_power_matrix = e_c_in_matrix + e_c_out_matrix
        cross_flow_terms = []
        constraints = []

        if self.config.grid_import_nonneg:
            constraints += [system_grid_import >= 0]

        # SOC 轨迹
        charge_cumsum = cp.cumsum(e_c_in_matrix, axis=1)
        discharge_cumsum = cp.cumsum(e_c_out_matrix, axis=1)
        constraints += [
            soc_matrix == e_r_vec.reshape((row, 1))
            - cp.multiply(charge_cumsum, time_ratio * c_l_in_vec)
            - cp.multiply(discharge_cumsum, time_ratio / c_l_out_vec)
        ]

        for source_i in range(row):
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
            constraints += [grid_to_load_matrix[target_j, :] + supplied_by_storage == local_d[target_j, :]]
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
            constraints += [cross_out <= transform_capacity_vec[source_i, 0]]

        constraints += [e_c_out_matrix <= e_c_max_vec]
        constraints += [e_c_out_matrix >= 0]
        constraints += [e_c_in_matrix <= 0]
        constraints += [e_c_in_matrix >= e_c_min_vec]
        constraints += [soc_matrix >= e_s_min_vec]
        constraints += [soc_matrix <= e_s_max_vec]

        # 分时段充放电硬约束
        discharge_allowed_mask = self._build_discharge_allowed_mask()
        for j, ts in enumerate(pd.to_datetime(self.schedule_time_range)):
            if self._charge_allowed(ts):
                constraints += [e_c_out_matrix[:, j] == 0]
            elif discharge_allowed_mask[j]:
                constraints += [e_c_in_matrix[:, j] == 0]
            else:
                constraints += [e_c_in_matrix[:, j] == 0, e_c_out_matrix[:, j] == 0]

        # 平滑惩罚 (v2+)
        smooth_penalty = 0.0
        if self.config.smooth_penalty_weight > 0 and column > 1:
            smooth_delta = cp.Variable((row, column - 1), nonneg=True)
            net_power_step_delta = net_power_matrix[:, 1:] - net_power_matrix[:, :-1]
            constraints += [
                smooth_delta >= net_power_step_delta,
                smooth_delta >= -net_power_step_delta,
            ]
            smooth_penalty = self.config.smooth_penalty_weight * cp.sum(smooth_delta)

        # 爬坡约束 (v2+)
        if self.config.ramp_rate_fraction_per_step is not None and column > 1:
            ramp_limit = np.repeat(self.config.ramp_rate_fraction_per_step * e_c_max_vec, column - 1, axis=1)
            net_power_step_delta = net_power_matrix[:, 1:] - net_power_matrix[:, :-1]
            constraints += [
                net_power_step_delta <= ramp_limit,
                net_power_step_delta >= -ramp_limit,
            ]

        # SOC 软目标 (v2+)
        charge_target_indices, discharge_target_indices = self._build_daily_soc_target_indices()
        soc_target_penalty = 0.0
        if self.config.charge_target_penalty_weight > 0 and charge_target_indices:
            charge_shortfall = cp.Variable((row, len(charge_target_indices)), nonneg=True)
            for k, target_idx in enumerate(charge_target_indices):
                constraints += [soc_matrix[:, target_idx] + charge_shortfall[:, k] >= e_s_max_vec[:, 0]]
            soc_target_penalty += self.config.charge_target_penalty_weight * cp.sum(charge_shortfall)
        if self.config.discharge_target_penalty_weight > 0 and discharge_target_indices:
            discharge_surplus = cp.Variable((row, len(discharge_target_indices)), nonneg=True)
            for k, target_idx in enumerate(discharge_target_indices):
                constraints += [soc_matrix[:, target_idx] - discharge_surplus[:, k] <= e_s_min_vec[:, 0]]
            soc_target_penalty += self.config.discharge_target_penalty_weight * cp.sum(discharge_surplus)

        # 目标函数
        cross_flow_total = self._sum_or_zero(cross_flow_terms, column)
        energy_cost = time_ratio * system_grid_import @ p
        max_demand_cost = self.max_demand_price * cp.max(system_grid_import)
        cross_flow_penalty = self.cross_flow_penalty * cp.sum(cross_flow_total)
        obj = cp.Minimize(
            energy_cost + max_demand_cost + cross_flow_penalty + smooth_penalty + soc_target_penalty
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
                for source_i in range(row) if source_i != target_j
            )
            transformer_import[target_j, :] = (
                grid_to_load_array[target_j, :] + cross_in_array + charge_power_array[target_j, :]
            )
        for source_i in range(row):
            transformer_export[source_i, :] = sum(
                allocation_values[source_i][target_j, :]
                for target_j in range(row) if target_j != source_i
            )

        # grid_import_total 使用与 system_grid_import 相同的公式
        if self.config.grid_import_formula == GridImportFormula.SUM_LOAD:
            grid_import_total = np.sum(grid_to_load_array, axis=0) + np.sum(charge_power_array, axis=0)
        else:
            grid_import_total = np.asarray(
                park_d + np.sum(charge_power_array, axis=0) - np.sum(discharge_array, axis=0)
            )

        self.last_solution = {
            "allocation_by_source": allocation_values,
            "grid_to_load": grid_to_load_array,
            "grid_import_total": grid_import_total,
            "transformer_import": transformer_import,
            "transformer_export": transformer_export,
            "soc": soc_matrix.value,
        }
        return result, charge_array, discharge_array

    # ── 规则求解 (v5) ─────────────────────────────────────────────────────

    def _solve_rule(self):
        row = self.devices_num
        column = self.schedule_time_length
        time_ratio = self.freq_minutes / 60
        local_d = np.array(self.local_demand_load_matrix)
        park_d = np.array(self.system_demand_load)
        discharge_allowed_mask = self._build_discharge_allowed_mask()

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
                            source_power, remaining_load[target_j],
                            remaining_park_discharge, cross_export_room,
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
                    cross_in = sum(
                        allocation_values[source_i][target_j, t]
                        for source_i in range(row) if source_i != target_j
                    )
                    transformer_import[target_j, t] = grid_to_load_array[target_j, t] + cross_in
                for source_i in range(row):
                    transformer_export[source_i, t] = sum(
                        allocation_values[source_i][target_j, t]
                        for target_j in range(row) if target_j != source_i
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

    # ── 共享方法 ──────────────────────────────────────────────────────────

    def schedule_generate(self, charge_array, discharge_array):
        schedule_list = []
        for device_i in range(self.devices_num):
            power_array_i = np.around(charge_array[device_i] + discharge_array[device_i], decimals=3)
            power_array_i = np.asarray(list(map(lambda x: 0.0 if abs(x) < 0.1 else x, power_array_i.tolist())))
            schedule_i_df = pd.DataFrame({"value": power_array_i}, index=self.schedule_time_range)
            schedule_list.append(schedule_i_df)
        return schedule_list

    def run(self):
        _, charge_array, discharge_array = self.modeling2solving()
        return self.schedule_generate(charge_array, discharge_array)
