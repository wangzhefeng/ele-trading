from __future__ import annotations

import cvxpy as cp
import numpy as np
import pandas as pd
from cvxpy.error import SolverError

from .interfaces import (
    DistributedBESSDemandChargeConfig,
    DistributedRenewableBESSDispatchInput,
    DistributedRenewableBESSDispatchPolicy,
    DistributedRenewableBESSDispatchResult,
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


class DistributedRenewableBESSDispatcher:
    def __init__(self, dispatch_input: DistributedRenewableBESSDispatchInput) -> None:
        self.dispatch_input = dispatch_input
        self.last_problem_status: str | None = None
        self.last_solver_name: str | None = None

    def solve(self) -> DistributedRenewableBESSDispatchResult:
        self._validate_input()
        if self.dispatch_input.solver != "lp":
            raise ValueError("distributed renewable+bess dispatch only supports solver='lp'")
        return self._solve_lp()

    def _validate_input(self) -> None:
        dispatch_input = self.dispatch_input
        length = len(dispatch_input.timestamps)
        if length == 0:
            raise ValueError("dispatch horizon must not be empty")
        if len(dispatch_input.nodes) == 0:
            raise ValueError("nodes must not be empty")
        if not (
            len(dispatch_input.buy_price) == len(dispatch_input.price_type) == length
        ):
            raise ValueError("timestamps, buy_price, and price_type must have the same length")
        if len(dispatch_input.initial_soc_kwh) != len(dispatch_input.nodes):
            raise ValueError("initial_soc_kwh length must match nodes length")
        if dispatch_input.step_hours <= 0:
            raise ValueError("step_hours must be positive")
        if dispatch_input.demand_charge_rate < 0:
            raise ValueError("demand_charge_rate must be non-negative")
        if dispatch_input.cycle_cost_rate < 0:
            raise ValueError("cycle_cost_rate must be non-negative")
        if dispatch_input.demand_charge.mode not in {"point_max", "sliding_window"}:
            raise ValueError("demand_charge.mode must be point_max or sliding_window")

        export = dispatch_input.export
        if export.sell_price_list is not None and len(export.sell_price_list) != length:
            raise ValueError("export.sell_price_list length must match timestamps")
        if export.export_limit is not None and export.export_limit < 0:
            raise ValueError("export.export_limit must be non-negative")
        if export.curtailment_cost_rate < 0:
            raise ValueError("export.curtailment_cost_rate must be non-negative")

        policy = dispatch_input.policy or DistributedRenewableBESSDispatchPolicy()
        self._validate_policy(policy)

        for node, initial_soc in zip(dispatch_input.nodes, dispatch_input.initial_soc_kwh):
            if len(node.load_forecast) != length or len(node.renewable_forecast) != length:
                raise ValueError("each node forecast must match timestamps length")
            if any(load < 0 for load in node.load_forecast):
                raise ValueError("node load_forecast must be non-negative")
            if any(value < 0 for value in node.renewable_forecast):
                raise ValueError("node renewable_forecast must be non-negative")
            if node.bess_capacity_kwh <= 0:
                raise ValueError("node bess_capacity_kwh must be positive")
            if node.bess_power_kw < 0:
                raise ValueError("node bess_power_kw must be non-negative")
            if node.charge_efficiency <= 0 or node.discharge_efficiency <= 0:
                raise ValueError("node efficiencies must be positive")
            if node.transformer_capacity_kw < 0:
                raise ValueError("node transformer_capacity_kw must be non-negative")
            if not 0 <= node.soc_min_kwh <= node.soc_max_kwh <= node.bess_capacity_kwh:
                raise ValueError("node SOC bounds must be within bess capacity")
            if not node.soc_min_kwh <= initial_soc <= node.soc_max_kwh:
                raise ValueError("initial_soc_kwh must be within node SOC bounds")
        if policy.terminal_soc_target_kwh is not None:
            if len(policy.terminal_soc_target_kwh) != len(dispatch_input.nodes):
                raise ValueError("policy.terminal_soc_target_kwh length must match nodes length")
            for node, target in zip(dispatch_input.nodes, policy.terminal_soc_target_kwh):
                if not node.soc_min_kwh <= target <= node.soc_max_kwh:
                    raise ValueError("terminal SOC target must be within node SOC bounds")

    @staticmethod
    def _validate_policy(policy: DistributedRenewableBESSDispatchPolicy) -> None:
        for name, hours in (
            ("charge_allowed_hours", policy.charge_allowed_hours),
            ("discharge_allowed_hours", policy.discharge_allowed_hours),
        ):
            if hours is None:
                continue
            if any(hour < 0 or hour > 23 for hour in hours):
                raise ValueError(f"policy.{name} must contain hours in [0, 23]")
        if policy.renewable_cross_flow_penalty_rate < 0:
            raise ValueError("policy.renewable_cross_flow_penalty_rate must be non-negative")
        if policy.bess_cross_flow_penalty_rate < 0:
            raise ValueError("policy.bess_cross_flow_penalty_rate must be non-negative")
        if policy.smooth_penalty_weight < 0:
            raise ValueError("policy.smooth_penalty_weight must be non-negative")
        if policy.charge_target_penalty_weight < 0:
            raise ValueError("policy.charge_target_penalty_weight must be non-negative")
        if policy.discharge_target_penalty_weight < 0:
            raise ValueError("policy.discharge_target_penalty_weight must be non-negative")
        if policy.terminal_soc_penalty_weight < 0:
            raise ValueError("policy.terminal_soc_penalty_weight must be non-negative")

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
        policy = self.dispatch_input.policy or DistributedRenewableBESSDispatchPolicy()
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
        policy = self.dispatch_input.policy or DistributedRenewableBESSDispatchPolicy()
        timestamps = pd.to_datetime(self.dispatch_input.timestamps)
        if policy.charge_allowed_hours is not None:
            return np.isin(timestamps.hour, policy.charge_allowed_hours)
        return ((timestamps.hour >= 0) & (timestamps.hour < 6)) | (
            (timestamps.hour >= 12) & (timestamps.hour < 14)
        )

    def _build_demand_expression(self, grid_import_total: cp.Expression) -> cp.Expression:
        demand_charge = self.dispatch_input.demand_charge
        if demand_charge.mode == "point_max":
            return cp.max(grid_import_total)
        step_minutes = self.dispatch_input.step_hours * 60
        window_steps = max(1, int(round(demand_charge.window_minutes / step_minutes)))
        if window_steps <= 1:
            return cp.max(grid_import_total)
        windows = []
        for end_idx in range(len(self.dispatch_input.timestamps)):
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

    def _solve_lp(self) -> DistributedRenewableBESSDispatchResult:
        dispatch_input = self.dispatch_input
        nodes = dispatch_input.nodes
        row = len(nodes)
        column = len(dispatch_input.timestamps)
        time_ratio = dispatch_input.step_hours
        load_d = np.array([node.load_forecast for node in nodes], dtype=float)
        renewable_d = np.array([node.renewable_forecast for node in nodes], dtype=float)
        price = np.array(dispatch_input.buy_price, dtype=float)
        sell_price = np.array(
            dispatch_input.export.sell_price_list
            if dispatch_input.export.sell_price_list is not None
            else [dispatch_input.export.sell_price] * column,
            dtype=float,
        )
        current_soc = np.array(dispatch_input.initial_soc_kwh, dtype=float)
        charge_eff = np.array([node.charge_efficiency for node in nodes], dtype=float).reshape((row, 1))
        discharge_eff = np.array([node.discharge_efficiency for node in nodes], dtype=float).reshape((row, 1))
        power_max = np.array([node.bess_power_kw for node in nodes], dtype=float).reshape((row, 1))
        soc_min = np.array([node.soc_min_kwh for node in nodes], dtype=float).reshape((row, 1))
        soc_max = np.array([node.soc_max_kwh for node in nodes], dtype=float).reshape((row, 1))
        transformer_capacity = np.array(
            [node.transformer_capacity_kw for node in nodes], dtype=float
        ).reshape((row, 1))
        policy = dispatch_input.policy or DistributedRenewableBESSDispatchPolicy()

        charge = cp.Variable((row, column), nonneg=True)
        discharge = cp.Variable((row, column), nonneg=True)
        soc = cp.Variable((row, column))
        grid_to_load = cp.Variable((row, column), nonneg=True)
        grid_to_bess = cp.Variable((row, column), nonneg=True)
        renewable_to_grid = cp.Variable((row, column), nonneg=True)
        renewable_curtailment = cp.Variable((row, column), nonneg=True)
        renewable_to_load_by_source = [
            cp.Variable((row, column), nonneg=True) for _ in range(row)
        ]
        renewable_to_bess_by_source = [
            cp.Variable((row, column), nonneg=True) for _ in range(row)
        ]
        bess_allocation_by_source = [
            cp.Variable((row, column), nonneg=True) for _ in range(row)
        ]

        system_grid_import = cp.sum(grid_to_load, axis=0) + cp.sum(grid_to_bess, axis=0)
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

        renewable_cross_flow_terms: list[cp.Expression] = []
        bess_cross_flow_terms: list[cp.Expression] = []
        for source_i in range(row):
            constraints += [
                cp.sum(renewable_to_load_by_source[source_i], axis=0)
                + cp.sum(renewable_to_bess_by_source[source_i], axis=0)
                + renewable_to_grid[source_i, :]
                + renewable_curtailment[source_i, :]
                == renewable_d[source_i, :]
            ]
            constraints += [
                cp.sum(bess_allocation_by_source[source_i], axis=0) == discharge[source_i, :]
            ]
            if not policy.renewable_cross_transformer_support:
                for target_j in range(row):
                    if source_i != target_j:
                        constraints += [
                            renewable_to_load_by_source[source_i][target_j, :] == 0,
                            renewable_to_bess_by_source[source_i][target_j, :] == 0,
                        ]
            if not policy.cross_transformer_support:
                for target_j in range(row):
                    if source_i != target_j:
                        constraints += [bess_allocation_by_source[source_i][target_j, :] == 0]

        for target_j in range(row):
            renewable_to_load_target = _sum_or_zero(
                [renewable_to_load_by_source[s][target_j, :] for s in range(row)],
                column,
            )
            renewable_to_bess_target = _sum_or_zero(
                [renewable_to_bess_by_source[s][target_j, :] for s in range(row)],
                column,
            )
            supplied_by_bess = _sum_or_zero(
                [bess_allocation_by_source[s][target_j, :] for s in range(row)],
                column,
            )
            renewable_cross_in = _sum_or_zero(
                [renewable_to_load_by_source[s][target_j, :] + renewable_to_bess_by_source[s][target_j, :] for s in range(row) if s != target_j],
                column,
            )
            bess_cross_in = _sum_or_zero(
                [bess_allocation_by_source[s][target_j, :] for s in range(row) if s != target_j],
                column,
            )
            constraints += [
                renewable_to_load_target + supplied_by_bess + grid_to_load[target_j, :]
                == load_d[target_j, :]
            ]
            constraints += [charge[target_j, :] == renewable_to_bess_target + grid_to_bess[target_j, :]]
            constraints += [
                grid_to_load[target_j, :]
                + grid_to_bess[target_j, :]
                + renewable_cross_in
                + bess_cross_in
                <= transformer_capacity[target_j, 0]
            ]

        for source_i in range(row):
            renewable_cross_out = _sum_or_zero(
                [
                    renewable_to_load_by_source[source_i][target_j, :]
                    + renewable_to_bess_by_source[source_i][target_j, :]
                    for target_j in range(row)
                    if target_j != source_i
                ],
                column,
            )
            bess_cross_out = _sum_or_zero(
                [
                    bess_allocation_by_source[source_i][target_j, :]
                    for target_j in range(row)
                    if target_j != source_i
                ],
                column,
            )
            renewable_cross_flow_terms.append(renewable_cross_out)
            bess_cross_flow_terms.append(bess_cross_out)
            constraints += [
                renewable_to_grid[source_i, :] + renewable_cross_out + bess_cross_out
                <= transformer_capacity[source_i, 0]
            ]

        constraints += [charge <= power_max, discharge <= power_max]
        constraints += [soc >= soc_min, soc <= soc_max]
        if not dispatch_input.export.allow_export:
            constraints += [renewable_to_grid == 0]
        if dispatch_input.export.export_limit is not None:
            constraints += [renewable_to_grid <= dispatch_input.export.export_limit]

        discharge_mask = self._build_discharge_mask()
        charge_mask = self._build_charge_mask()
        for idx in range(column):
            if charge_mask[idx]:
                constraints += [discharge[:, idx] == 0]
            elif discharge_mask[idx]:
                constraints += [charge[:, idx] == 0]
            else:
                constraints += [charge[:, idx] == 0, discharge[:, idx] == 0]

        net_power = discharge - charge
        smooth_cost_expr: cp.Expression = cp.Constant(0.0)
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

        renewable_cross_flow_total = _sum_or_zero(renewable_cross_flow_terms, column)
        bess_cross_flow_total = _sum_or_zero(bess_cross_flow_terms, column)
        energy_cost_expr = time_ratio * (system_grid_import @ price)
        demand_expr = self._build_demand_expression(system_grid_import)
        demand_cost_expr = dispatch_input.demand_charge_rate * demand_expr
        sell_revenue_expr = time_ratio * cp.sum(cp.multiply(renewable_to_grid, sell_price))
        curtailment_cost_expr = (
            dispatch_input.export.curtailment_cost_rate * time_ratio * cp.sum(renewable_curtailment)
        )
        cycle_cost_expr = (
            dispatch_input.cycle_cost_rate * time_ratio * cp.sum(charge + discharge)
        )
        cross_flow_cost_expr = (
            policy.renewable_cross_flow_penalty_rate * cp.sum(renewable_cross_flow_total)
            + policy.bess_cross_flow_penalty_rate * cp.sum(bess_cross_flow_total)
        )
        total_cost = (
            energy_cost_expr
            + demand_cost_expr
            + curtailment_cost_expr
            + cycle_cost_expr
            + cross_flow_cost_expr
            + smooth_cost_expr
            + soc_target_cost_expr
            - sell_revenue_expr
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
            raise ValueError(
                "distributed renewable+bess optimization failed: "
                f"{problem.status}; attempts: {'; '.join(errors)}"
            )

        renewable_load_values = [
            np.asarray(allocation.value, dtype=float) for allocation in renewable_to_load_by_source
        ]
        renewable_bess_values = [
            np.asarray(allocation.value, dtype=float) for allocation in renewable_to_bess_by_source
        ]
        bess_values = [
            np.asarray(allocation.value, dtype=float) for allocation in bess_allocation_by_source
        ]
        charge_value = np.asarray(charge.value, dtype=float)
        discharge_value = np.asarray(discharge.value, dtype=float)
        grid_to_load_value = np.asarray(grid_to_load.value, dtype=float)
        grid_to_bess_value = np.asarray(grid_to_bess.value, dtype=float)
        renewable_to_grid_value = np.asarray(renewable_to_grid.value, dtype=float)
        renewable_curtailment_value = np.asarray(renewable_curtailment.value, dtype=float)
        soc_value = np.asarray(soc.value, dtype=float)
        system_grid_import_value = np.asarray(system_grid_import.value, dtype=float)

        renewable_to_load_value = np.zeros((row, column))
        renewable_to_bess_value = np.zeros((row, column))
        transformer_import = np.zeros((row, column))
        transformer_export = np.zeros((row, column))
        for target_j in range(row):
            renewable_to_load_value[target_j, :] = sum(
                renewable_load_values[source_i][target_j, :] for source_i in range(row)
            )
            renewable_to_bess_value[target_j, :] = sum(
                renewable_bess_values[source_i][target_j, :] for source_i in range(row)
            )
            renewable_cross_in = sum(
                renewable_load_values[source_i][target_j, :] + renewable_bess_values[source_i][target_j, :]
                for source_i in range(row)
                if source_i != target_j
            )
            bess_cross_in = sum(
                bess_values[source_i][target_j, :] for source_i in range(row) if source_i != target_j
            )
            transformer_import[target_j, :] = (
                grid_to_load_value[target_j, :]
                + grid_to_bess_value[target_j, :]
                + renewable_cross_in
                + bess_cross_in
            )
        for source_i in range(row):
            renewable_cross_out = sum(
                renewable_load_values[source_i][target_j, :] + renewable_bess_values[source_i][target_j, :]
                for target_j in range(row)
                if target_j != source_i
            )
            bess_cross_out = sum(
                bess_values[source_i][target_j, :] for target_j in range(row) if target_j != source_i
            )
            transformer_export[source_i, :] = (
                renewable_to_grid_value[source_i, :] + renewable_cross_out + bess_cross_out
            )

        clean_grid_import = _clean_vector(system_grid_import_value)
        max_demand_kw = float(max(clean_grid_import))
        if dispatch_input.demand_charge.mode == "sliding_window":
            step_minutes = dispatch_input.step_hours * 60
            window_steps = max(1, int(round(dispatch_input.demand_charge.window_minutes / step_minutes)))
            max_demand_kw = float(pd.Series(clean_grid_import).rolling(window_steps, min_periods=1).mean().max())

        energy_cost = float(time_ratio * np.dot(system_grid_import_value, price))
        demand_cost = float(dispatch_input.demand_charge_rate * max_demand_kw)
        sell_revenue = float(time_ratio * np.sum(renewable_to_grid_value * sell_price.reshape((1, column))))
        curtailment_cost = float(
            dispatch_input.export.curtailment_cost_rate * time_ratio * np.sum(renewable_curtailment_value)
        )
        cycle_cost = float(dispatch_input.cycle_cost_rate * time_ratio * np.sum(charge_value + discharge_value))
        cross_flow_cost = float(
            policy.renewable_cross_flow_penalty_rate
            * sum(
                renewable_load_values[source_i][target_j, :].sum()
                + renewable_bess_values[source_i][target_j, :].sum()
                for source_i in range(row)
                for target_j in range(row)
                if source_i != target_j
            )
            + policy.bess_cross_flow_penalty_rate
            * sum(
                bess_values[source_i][target_j, :].sum()
                for source_i in range(row)
                for target_j in range(row)
                if source_i != target_j
            )
        )
        smooth_cost = float(smooth_cost_expr.value)
        soc_target_cost = float(soc_target_cost_expr.value)

        return DistributedRenewableBESSDispatchResult(
            renewable_to_load_by_node=_clean_matrix(renewable_to_load_value),
            renewable_to_bess_by_node=_clean_matrix(renewable_to_bess_value),
            renewable_to_grid_by_node=_clean_matrix(renewable_to_grid_value),
            renewable_curtailment_by_node=_clean_matrix(renewable_curtailment_value),
            grid_to_load_by_node=_clean_matrix(grid_to_load_value),
            grid_to_bess_by_node=_clean_matrix(grid_to_bess_value),
            charge_power_by_node=_clean_matrix(charge_value),
            discharge_power_by_node=_clean_matrix(discharge_value),
            net_bess_power_by_node=_clean_matrix(discharge_value - charge_value),
            soc_by_node=_clean_matrix(soc_value),
            grid_import_total=clean_grid_import,
            transformer_import_by_node=_clean_matrix(transformer_import),
            transformer_export_by_node=_clean_matrix(transformer_export),
            renewable_allocation_by_source_target=[
                _clean_matrix(renewable_load_values[idx] + renewable_bess_values[idx]) for idx in range(row)
            ],
            bess_allocation_by_source_target=[_clean_matrix(values) for values in bess_values],
            max_demand_kw=max_demand_kw,
            energy_cost=energy_cost,
            demand_cost=demand_cost,
            sell_revenue=sell_revenue,
            curtailment_cost=curtailment_cost,
            cross_flow_cost=cross_flow_cost,
            cycle_cost=cycle_cost,
            smooth_cost=smooth_cost,
            soc_target_cost=soc_target_cost,
            total_cost=float(
                energy_cost
                + demand_cost
                + curtailment_cost
                + cycle_cost
                + cross_flow_cost
                + smooth_cost
                + soc_target_cost
                - sell_revenue
            ),
            solver_status=str(problem.status),
            solver_name=self.last_solver_name or "",
            constraint_violations={},
        )


def run_user_side_renewable_bess_distributed_dispatch(
    dispatch_input: DistributedRenewableBESSDispatchInput,
) -> DistributedRenewableBESSDispatchResult:
    return DistributedRenewableBESSDispatcher(dispatch_input).solve()
