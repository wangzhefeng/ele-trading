from __future__ import annotations

import cvxpy as cp
import numpy as np
import pandas as pd
from cvxpy.error import SolverError

from ..interfaces import (
    DistributedBESSDispatchInput,
    DistributedBESSDispatchPolicy,
    DistributedBESSDispatchResult,
)


def _clean_matrix(values: np.ndarray | None, *, tol: float = 1e-6) -> list[list[float]]:
    if values is None:
        return []
    arr = np.asarray(values, dtype=float)
    arr[np.abs(arr) <= tol] = 0.0
    return np.round(arr, 6).tolist()


def _clean_vector(values: np.ndarray | None, *, tol: float = 1e-6) -> list[float]:
    if values is None:
        return []
    arr = np.asarray(values, dtype=float)
    arr[np.abs(arr) <= tol] = 0.0
    return np.round(arr, 6).tolist()


def _sum_or_zero(expressions: list[cp.Expression], column: int) -> cp.Expression:
    if not expressions:
        return cp.Constant(np.zeros(column))
    total = expressions[0]
    for expression in expressions[1:]:
        total = total + expression
    return total


class DistributedBESSDispatcher:
    def __init__(self, dispatch_input: DistributedBESSDispatchInput) -> None:
        self.dispatch_input = dispatch_input
        self.last_problem_status: str | None = None
        self.last_solver_name: str | None = None

    def solve(self) -> DistributedBESSDispatchResult:
        self._validate_input()
        if self.dispatch_input.solver == "rule":
            return self._solve_rule()
        return self._solve_lp()

    def _validate_input(self) -> None:
        dispatch_input = self.dispatch_input
        length = len(dispatch_input.timestamps)
        if length == 0:
            raise ValueError("dispatch horizon must not be empty")
        if not (
            len(dispatch_input.system_load_forecast)
            == len(dispatch_input.buy_price)
            == len(dispatch_input.price_type)
            == length
        ):
            raise ValueError(
                "timestamps, system_load_forecast, buy_price, and price_type must have the same length"
            )
        if len(dispatch_input.nodes) == 0:
            raise ValueError("nodes must not be empty")
        if len(dispatch_input.local_load_forecast) != len(dispatch_input.nodes):
            raise ValueError("local_load_forecast row count must match nodes length")
        if len(dispatch_input.initial_soc_kwh) != len(dispatch_input.nodes):
            raise ValueError("initial_soc_kwh length must match nodes length")
        for node, local_load, initial_soc in zip(
            dispatch_input.nodes,
            dispatch_input.local_load_forecast,
            dispatch_input.initial_soc_kwh,
        ):
            if len(local_load) != length:
                raise ValueError("each local_load_forecast row must match timestamps length")
            if node.bess_capacity_kwh <= 0:
                raise ValueError("node bess_capacity_kwh must be positive")
            if node.bess_power_kw < 0:
                raise ValueError("node bess_power_kw must be non-negative")
            if not 0 <= node.soc_min_kwh <= node.soc_max_kwh <= node.bess_capacity_kwh:
                raise ValueError("node SOC bounds must be within bess capacity")
            if not node.soc_min_kwh <= initial_soc <= node.soc_max_kwh:
                raise ValueError("initial_soc_kwh must be within node SOC bounds")
        if dispatch_input.step_hours <= 0:
            raise ValueError("step_hours must be positive")
        if dispatch_input.demand_charge_rate < 0:
            raise ValueError("demand_charge_rate must be non-negative")
        if dispatch_input.demand_charge.mode not in {"point_max", "sliding_window"}:
            raise ValueError("demand_charge.mode must be point_max or sliding_window")
        if dispatch_input.solver not in {"lp", "rule"}:
            raise ValueError("solver must be lp or rule")

    @staticmethod
    def _price_type_discharge_mask(
        timestamps: list[pd.Timestamp], ele_types: list[str]
    ) -> np.ndarray:
        time_range = pd.to_datetime(timestamps)
        ele_types_s = pd.Series(ele_types, index=time_range).astype(str).str.strip()
        allowed = pd.Series(False, index=time_range)
        high_types = {"高", "峰"}
        sharp_types = {"尖", "尖峰"}
        morning_mask = (time_range.hour >= 6) & (time_range.hour < 12)
        allowed.loc[morning_mask] = True
        for _, day_types in ele_types_s.groupby(ele_types_s.index.normalize()):
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

    def _build_discharge_mask(self) -> np.ndarray:
        policy = self.dispatch_input.policy or DistributedBESSDispatchPolicy()
        timestamps = pd.to_datetime(self.dispatch_input.timestamps)
        if policy.discharge_allowed_hours is not None:
            return np.isin(timestamps.hour, policy.discharge_allowed_hours)
        if policy.discharge_mask_mode == "fixed_window":
            return (
                ((timestamps.hour >= 6) & (timestamps.hour < 12))
                | ((timestamps.hour >= 16) & (timestamps.hour < 24))
            )
        return self._price_type_discharge_mask(self.dispatch_input.timestamps, self.dispatch_input.price_type)

    def _build_charge_mask(self) -> np.ndarray:
        policy = self.dispatch_input.policy or DistributedBESSDispatchPolicy()
        timestamps = pd.to_datetime(self.dispatch_input.timestamps)
        if policy.charge_allowed_hours is not None:
            return np.isin(timestamps.hour, policy.charge_allowed_hours)
        return ((timestamps.hour >= 0) & (timestamps.hour < 6)) | (
            (timestamps.hour >= 12) & (timestamps.hour < 14)
        )

    def _build_demand_expression(
        self, grid_import_total: cp.Expression, column: int
    ) -> cp.Expression:
        demand_charge = self.dispatch_input.demand_charge
        if demand_charge.mode == "point_max":
            return cp.max(grid_import_total)
        step_minutes = self.dispatch_input.step_hours * 60
        if step_minutes <= 0:
            raise ValueError("step_hours must be positive")
        window_steps = max(1, int(round(demand_charge.window_minutes / step_minutes)))
        if window_steps <= 1:
            return cp.max(grid_import_total)
        windows = []
        for end_idx in range(column):
            start_idx = max(0, end_idx - window_steps + 1)
            span = end_idx - start_idx + 1
            windows.append(cp.sum(grid_import_total[start_idx : end_idx + 1]) / span)
        return cp.max(cp.hstack(windows))

    def _build_daily_soc_target_indices(self, column: int) -> tuple[list[int], list[int]]:
        charge_target_indices: list[int] = []
        discharge_target_indices: list[int] = []
        indexed_times = pd.Series(range(column), index=pd.to_datetime(self.dispatch_input.timestamps))
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

    def _solve_lp(self) -> DistributedBESSDispatchResult:
        dispatch_input = self.dispatch_input
        nodes = dispatch_input.nodes
        row = len(nodes)
        column = len(dispatch_input.timestamps)
        time_ratio = dispatch_input.step_hours
        local_d = np.array(dispatch_input.local_load_forecast, dtype=float)
        system_d = np.array(dispatch_input.system_load_forecast, dtype=float)
        price = np.array(dispatch_input.buy_price, dtype=float)
        current_soc = np.array(dispatch_input.initial_soc_kwh, dtype=float)
        charge_eff = np.array([node.charge_efficiency for node in nodes], dtype=float).reshape((row, 1))
        discharge_eff = np.array([node.discharge_efficiency for node in nodes], dtype=float).reshape((row, 1))
        power_max = np.array([node.bess_power_kw for node in nodes], dtype=float).reshape((row, 1))
        soc_min = np.array([node.soc_min_kwh for node in nodes], dtype=float).reshape((row, 1))
        soc_max = np.array([node.soc_max_kwh for node in nodes], dtype=float).reshape((row, 1))
        transformer_capacity = np.array(
            [node.transformer_capacity_kw for node in nodes], dtype=float
        ).reshape((row, 1))
        policy = dispatch_input.policy or DistributedBESSDispatchPolicy()

        charge = cp.Variable((row, column), nonneg=True)
        discharge = cp.Variable((row, column), nonneg=True)
        soc = cp.Variable((row, column))
        grid_to_load = cp.Variable((row, column), nonneg=True)
        allocation_by_source = [
            cp.Variable((row, column), nonneg=True) for _ in range(row)
        ]

        if dispatch_input.grid_import_formula == "sum_load":
            system_grid_import = cp.sum(grid_to_load, axis=0) + cp.sum(charge, axis=0)
        else:
            system_grid_import = system_d + cp.sum(charge, axis=0) - cp.sum(discharge, axis=0)

        constraints: list[cp.Constraint] = []
        if dispatch_input.grid_import_nonneg:
            constraints += [system_grid_import >= 0]

        charge_cumsum = cp.cumsum(charge, axis=1)
        discharge_cumsum = cp.cumsum(discharge, axis=1)
        constraints += [
            soc
            == current_soc.reshape((row, 1))
            + cp.multiply(charge_cumsum, time_ratio * charge_eff)
            - cp.multiply(discharge_cumsum, time_ratio / discharge_eff)
        ]

        cross_flow_terms = []
        for source_i in range(row):
            constraints += [cp.sum(allocation_by_source[source_i], axis=0) == discharge[source_i, :]]
            if not policy.cross_transformer_support:
                for target_j in range(row):
                    if source_i != target_j:
                        constraints += [allocation_by_source[source_i][target_j, :] == 0]
        for target_j in range(row):
            supplied_by_bess = _sum_or_zero(
                [allocation_by_source[s][target_j, :] for s in range(row)],
                column,
            )
            cross_in = _sum_or_zero(
                [allocation_by_source[s][target_j, :] for s in range(row) if s != target_j],
                column,
            )
            constraints += [grid_to_load[target_j, :] + supplied_by_bess == local_d[target_j, :]]
            constraints += [
                grid_to_load[target_j, :] + cross_in + charge[target_j, :]
                <= transformer_capacity[target_j, 0]
            ]
        for source_i in range(row):
            cross_out = _sum_or_zero(
                [allocation_by_source[source_i][t, :] for t in range(row) if t != source_i],
                column,
            )
            cross_flow_terms.append(cross_out)
            constraints += [cross_out <= transformer_capacity[source_i, 0]]

        constraints += [charge <= power_max, discharge <= power_max]
        constraints += [soc >= soc_min, soc <= soc_max]

        discharge_mask = self._build_discharge_mask()
        charge_mask = self._build_charge_mask()
        for idx in range(column):
            if charge_mask[idx]:
                constraints += [discharge[:, idx] == 0]
            elif discharge_mask[idx]:
                constraints += [charge[:, idx] == 0]
            else:
                constraints += [charge[:, idx] == 0, discharge[:, idx] == 0]

        smooth_cost_expr: cp.Expression = cp.Constant(0.0)
        net_power = discharge - charge
        if policy.smooth_penalty_weight > 0 and column > 1:
            smooth_delta = cp.Variable((row, column - 1), nonneg=True)
            net_delta = net_power[:, 1:] - net_power[:, :-1]
            constraints += [smooth_delta >= net_delta, smooth_delta >= -net_delta]
            smooth_cost_expr = policy.smooth_penalty_weight * cp.sum(smooth_delta)

        if policy.ramp_rate_fraction_per_step is not None and column > 1:
            ramp_limit = np.repeat(
                policy.ramp_rate_fraction_per_step * power_max,
                column - 1,
                axis=1,
            )
            net_delta = net_power[:, 1:] - net_power[:, :-1]
            constraints += [net_delta <= ramp_limit, net_delta >= -ramp_limit]

        soc_target_cost_expr: cp.Expression = cp.Constant(0.0)
        charge_target_indices, discharge_target_indices = self._build_daily_soc_target_indices(column)
        if policy.charge_target_penalty_weight > 0 and charge_target_indices:
            shortfall = cp.Variable((row, len(charge_target_indices)), nonneg=True)
            for k, idx in enumerate(charge_target_indices):
                constraints += [soc[:, idx] + shortfall[:, k] >= soc_max[:, 0]]
            soc_target_cost_expr += policy.charge_target_penalty_weight * cp.sum(shortfall)
        if policy.discharge_target_penalty_weight > 0 and discharge_target_indices:
            surplus = cp.Variable((row, len(discharge_target_indices)), nonneg=True)
            for k, idx in enumerate(discharge_target_indices):
                constraints += [soc[:, idx] - surplus[:, k] <= soc_min[:, 0]]
            soc_target_cost_expr += policy.discharge_target_penalty_weight * cp.sum(surplus)
        if policy.terminal_soc_target_kwh is not None:
            target = np.array(policy.terminal_soc_target_kwh, dtype=float).reshape((row, 1))
            if policy.terminal_soc_penalty_weight > 0:
                delta = cp.Variable((row, 1), nonneg=True)
                constraints += [delta >= soc[:, -1].reshape((row, 1)) - target]
                constraints += [delta >= target - soc[:, -1].reshape((row, 1))]
                soc_target_cost_expr += policy.terminal_soc_penalty_weight * cp.sum(delta)
            else:
                constraints += [soc[:, -1] == target[:, 0]]

        cross_flow_total = _sum_or_zero(cross_flow_terms, column)
        energy_cost_expr = time_ratio * (system_grid_import @ price)
        max_demand_expr = self._build_demand_expression(system_grid_import, column)
        demand_cost_expr = dispatch_input.demand_charge_rate * max_demand_expr
        cross_flow_cost_expr = policy.cross_flow_penalty_rate * cp.sum(cross_flow_total)
        total_cost = (
            energy_cost_expr
            + demand_cost_expr
            + cross_flow_cost_expr
            + smooth_cost_expr
            + soc_target_cost_expr
        )
        problem = cp.Problem(cp.Minimize(total_cost), constraints)

        result = None
        errors = []
        for solver in (cp.HIGHS, cp.CLARABEL, cp.SCS):
            try:
                result = problem.solve(verbose=False, solver=solver)
            except SolverError as exc:
                errors.append(f"{solver}: {exc}")
                continue
            if problem.status in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
                self.last_solver_name = str(solver)
                break
            errors.append(f"{solver}: status={problem.status}")
        self.last_problem_status = str(problem.status)
        if problem.status not in {cp.OPTIMAL, cp.OPTIMAL_INACCURATE}:
            raise ValueError(f"distributed dispatch optimization failed: {problem.status}; attempts: {'; '.join(errors)}")

        allocation_values = [allocation.value for allocation in allocation_by_source]
        transformer_import = np.zeros((row, column))
        transformer_export = np.zeros((row, column))
        grid_to_load_value = np.asarray(grid_to_load.value, dtype=float)
        charge_value = np.asarray(charge.value, dtype=float)
        discharge_value = np.asarray(discharge.value, dtype=float)
        for target_j in range(row):
            cross_in = sum(allocation_values[source_i][target_j, :] for source_i in range(row) if source_i != target_j)
            transformer_import[target_j, :] = grid_to_load_value[target_j, :] + cross_in + charge_value[target_j, :]
        for source_i in range(row):
            transformer_export[source_i, :] = sum(allocation_values[source_i][target_j, :] for target_j in range(row) if target_j != source_i)

        max_demand_kw = float(max(_clean_vector(np.asarray(system_grid_import.value))))
        if dispatch_input.demand_charge.mode == "sliding_window":
            clean_grid = _clean_vector(np.asarray(system_grid_import.value))
            step_minutes = dispatch_input.step_hours * 60
            window_steps = max(1, int(round(dispatch_input.demand_charge.window_minutes / step_minutes)))
            series = pd.Series(clean_grid)
            max_demand_kw = float(series.rolling(window_steps, min_periods=1).mean().max())

        return DistributedBESSDispatchResult(
            charge_power_by_node=_clean_matrix(charge_value),
            discharge_power_by_node=_clean_matrix(discharge_value),
            net_bess_power_by_node=_clean_matrix(discharge_value - charge_value),
            soc_by_node=_clean_matrix(soc.value),
            grid_to_load_by_node=_clean_matrix(grid_to_load_value),
            grid_import_total=_clean_vector(np.asarray(system_grid_import.value)),
            transformer_import_by_node=_clean_matrix(transformer_import),
            transformer_export_by_node=_clean_matrix(transformer_export),
            allocation_by_source_target=[
                _clean_matrix(allocation_value) for allocation_value in allocation_values
            ],
            max_demand_kw=max_demand_kw,
            energy_cost=float(energy_cost_expr.value),
            demand_cost=float(dispatch_input.demand_charge_rate * max_demand_kw),
            cross_flow_cost=float(cross_flow_cost_expr.value),
            smooth_cost=float(smooth_cost_expr.value),
            soc_target_cost=float(soc_target_cost_expr.value),
            total_cost=float(result if result is not None else total_cost.value),
            solver_status=str(problem.status),
            solver_name=self.last_solver_name or "",
            constraint_violations={},
        )

    def _solve_rule(self) -> DistributedBESSDispatchResult:
        dispatch_input = self.dispatch_input
        nodes = dispatch_input.nodes
        row = len(nodes)
        column = len(dispatch_input.timestamps)
        local_d = np.array(dispatch_input.local_load_forecast, dtype=float)
        system_d = np.array(dispatch_input.system_load_forecast, dtype=float)
        policy = dispatch_input.policy or DistributedBESSDispatchPolicy()
        time_ratio = dispatch_input.step_hours
        discharge_mask = self._build_discharge_mask()
        charge_mask = self._build_charge_mask()

        charge = np.zeros((row, column))
        discharge = np.zeros((row, column))
        soc = np.zeros((row, column))
        grid_to_load = np.zeros((row, column))
        transformer_import = np.zeros((row, column))
        transformer_export = np.zeros((row, column))
        allocation_values = [np.zeros((row, column)) for _ in range(row)]
        soc_now = np.array(dispatch_input.initial_soc_kwh, dtype=float)
        charge_eff = np.array([node.charge_efficiency for node in nodes], dtype=float)
        discharge_eff = np.array([node.discharge_efficiency for node in nodes], dtype=float)
        power_max = np.array([node.bess_power_kw for node in nodes], dtype=float)
        soc_min = np.array([node.soc_min_kwh for node in nodes], dtype=float)
        soc_max = np.array([node.soc_max_kwh for node in nodes], dtype=float)
        transformer_capacity = np.array([node.transformer_capacity_kw for node in nodes], dtype=float)

        for t in range(column):
            local_load = np.maximum(local_d[:, t], 0.0)
            if charge_mask[t]:
                for i in range(row):
                    soc_room = max((soc_max[i] - soc_now[i]) / max(charge_eff[i] * time_ratio, 1e-9), 0.0)
                    tx_room = max(transformer_capacity[i] - local_load[i], 0.0)
                    charge_power = min(power_max[i], soc_room, tx_room)
                    charge[i, t] = charge_power
                    soc_now[i] += charge_power * charge_eff[i] * time_ratio
                grid_to_load[:, t] = local_load
                transformer_import[:, t] = local_load + charge[:, t]
            elif discharge_mask[t]:
                remaining_load = local_load.copy()
                remaining_system = max(float(system_d[t]), 0.0)
                for source_i in range(row):
                    discharge_power = min(
                        power_max[source_i],
                        max((soc_now[source_i] - soc_min[source_i]) * discharge_eff[source_i] / max(time_ratio, 1e-9), 0.0),
                        remaining_system,
                    )
                    if discharge_power <= 0:
                        continue
                    local_take = min(discharge_power, remaining_load[source_i], remaining_system)
                    allocation_values[source_i][source_i, t] = local_take
                    remaining_load[source_i] -= local_take
                    remaining_system -= local_take
                    discharge_power -= local_take
                    if policy.cross_transformer_support:
                        for target_j in range(row):
                            if target_j == source_i or discharge_power <= 0 or remaining_system <= 0:
                                continue
                            cross_take = min(
                                discharge_power,
                                remaining_load[target_j],
                                remaining_system,
                                transformer_capacity[source_i],
                            )
                            if cross_take <= 0:
                                continue
                            allocation_values[source_i][target_j, t] = cross_take
                            remaining_load[target_j] -= cross_take
                            remaining_system -= cross_take
                            discharge_power -= cross_take
                    discharge[source_i, t] = float(allocation_values[source_i][:, t].sum())
                    soc_now[source_i] -= discharge[source_i, t] / discharge_eff[source_i] * time_ratio
                supplied = np.sum(np.stack([allocation[:, t] for allocation in allocation_values], axis=0), axis=0)
                grid_to_load[:, t] = np.maximum(local_load - supplied, 0.0)
                for target_j in range(row):
                    cross_in = sum(allocation_values[source_i][target_j, t] for source_i in range(row) if source_i != target_j)
                    transformer_import[target_j, t] = grid_to_load[target_j, t] + cross_in
                for source_i in range(row):
                    transformer_export[source_i, t] = sum(allocation_values[source_i][target_j, t] for target_j in range(row) if target_j != source_i)
            else:
                grid_to_load[:, t] = local_load
                transformer_import[:, t] = local_load
            soc_now = np.clip(soc_now, soc_min, soc_max)
            soc[:, t] = soc_now

        if dispatch_input.grid_import_formula == "sum_load":
            grid_import_total = np.sum(grid_to_load, axis=0) + np.sum(charge, axis=0)
        else:
            grid_import_total = np.maximum(system_d + np.sum(charge, axis=0) - np.sum(discharge, axis=0), 0.0)
        max_demand_kw = float(np.max(grid_import_total))
        if dispatch_input.demand_charge.mode == "sliding_window":
            step_minutes = dispatch_input.step_hours * 60
            window_steps = max(1, int(round(dispatch_input.demand_charge.window_minutes / step_minutes)))
            max_demand_kw = float(pd.Series(grid_import_total).rolling(window_steps, min_periods=1).mean().max())
        energy_cost = float(np.sum(grid_import_total * np.array(dispatch_input.buy_price, dtype=float) * time_ratio))
        demand_cost = float(dispatch_input.demand_charge_rate * max_demand_kw)
        cross_flow_cost = float(
            policy.cross_flow_penalty_rate
            * sum(allocation_values[source_i][target_j, :].sum() for source_i in range(row) for target_j in range(row) if source_i != target_j)
        )
        total_cost = energy_cost + demand_cost + cross_flow_cost
        self.last_problem_status = "RULE_BASED"
        self.last_solver_name = "rule"
        return DistributedBESSDispatchResult(
            charge_power_by_node=_clean_matrix(charge),
            discharge_power_by_node=_clean_matrix(discharge),
            net_bess_power_by_node=_clean_matrix(discharge - charge),
            soc_by_node=_clean_matrix(soc),
            grid_to_load_by_node=_clean_matrix(grid_to_load),
            grid_import_total=_clean_vector(grid_import_total),
            transformer_import_by_node=_clean_matrix(transformer_import),
            transformer_export_by_node=_clean_matrix(transformer_export),
            allocation_by_source_target=[_clean_matrix(allocation) for allocation in allocation_values],
            max_demand_kw=max_demand_kw,
            energy_cost=energy_cost,
            demand_cost=demand_cost,
            cross_flow_cost=cross_flow_cost,
            smooth_cost=0.0,
            soc_target_cost=0.0,
            total_cost=total_cost,
            solver_status="RULE_BASED",
            solver_name="rule",
            constraint_violations={},
        )


def run_distributed_bess_dispatch(
    dispatch_input: DistributedBESSDispatchInput,
) -> DistributedBESSDispatchResult:
    return DistributedBESSDispatcher(dispatch_input).solve()
