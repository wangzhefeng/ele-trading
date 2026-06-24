"""分布式储能测算 — 完整实现。

通过 DistBESSSchedulerConfig 参数控制 v1-v5 行为差异。
"""
from __future__ import annotations

import itertools
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, Iterable, List

for _thread_env_name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_thread_env_name, "1")

import cvxpy as cp
import numpy as np
import pandas as pd
from cvxpy.error import SolverError

from ..utils.data_alignment import read_time_value_csv
from ..utils.demand_charge import monthly_peak_demand_cost
from ..utils.time_splitting import generate_month_ranges
from .interfaces import (
    DIST_BESS_CABINET_CAPACITY_KWH,
    DIST_BESS_CABINET_POWER_KW,
    DIST_BESS_CONSTRAINT_TOLERANCE_KW,
    CabinetEqualityMode,
    DistBESSConfig,
    DistBESSDispatchInput,
    DistBESSDispatchResult,
    DistBESSSchedulerConfig,
    GridImportFormula,
    SolverType,
    TransformerConfig,
)

# 兼容旧名（模块内使用短名）
_CABINET_POWER_KW = DIST_BESS_CABINET_POWER_KW
_CABINET_CAPACITY_KWH = DIST_BESS_CABINET_CAPACITY_KWH
_CONSTRAINT_TOLERANCE_KW = DIST_BESS_CONSTRAINT_TOLERANCE_KW


# ═══════════════════════════════════════════════════════════════════════════════
# 拓扑与预设
# ═══════════════════════════════════════════════════════════════════════════════

TRANSFORMERS = [
    TransformerConfig("338_1", "demand_load_338_1.csv", 2000.0, 13),
    TransformerConfig("338_2", "demand_load_338_2.csv", 1600.0, 10),
    TransformerConfig("338_3", "demand_load_338_3.csv", 1600.0, 10),
    TransformerConfig("342_1", "demand_load_342_1.csv", 1250.0, 8),
    TransformerConfig("342_2", "demand_load_342_2.csv", 1250.0, 8),
]
TRANSFORMER_BY_NAME: dict[str, TransformerConfig] = {cfg.name: cfg for cfg in TRANSFORMERS}

SYSTEMS: dict[str, DistBESSConfig] = {
    "338": DistBESSConfig("338", (
        TRANSFORMER_BY_NAME["338_1"], TRANSFORMER_BY_NAME["338_2"], TRANSFORMER_BY_NAME["338_3"],
    )),
    "342": DistBESSConfig("342", (
        TRANSFORMER_BY_NAME["342_1"], TRANSFORMER_BY_NAME["342_2"],
    )),
    "park": DistBESSConfig("park", (
        TRANSFORMER_BY_NAME["338_1"], TRANSFORMER_BY_NAME["338_2"], TRANSFORMER_BY_NAME["338_3"],
        TRANSFORMER_BY_NAME["342_1"], TRANSFORMER_BY_NAME["342_2"],
    ), cabinet_groups=(("338_1", "338_2", "338_3"), ("342_1", "342_2"))),
}

V1_PRESET = DistBESSSchedulerConfig(
    solver=SolverType.LP, grid_import_formula=GridImportFormula.SUM_LOAD,
    grid_import_nonneg=False, discharge_mask_mode="price_type",
)
V2_PRESET = DistBESSSchedulerConfig(
    solver=SolverType.LP, grid_import_formula=GridImportFormula.SUM_LOAD,
    grid_import_nonneg=False, discharge_mask_mode="price_type",
    smooth_penalty_weight=1e-4, ramp_rate_fraction_per_step=0.5,
)
V3_PRESET = V2_PRESET
V4_PRESET = DistBESSSchedulerConfig(
    solver=SolverType.LP, grid_import_formula=GridImportFormula.PARK_BASELINE,
    grid_import_nonneg=True, discharge_mask_mode="price_type",
    smooth_penalty_weight=1e-4, ramp_rate_fraction_per_step=0.5,
)
V5_PRESET = DistBESSSchedulerConfig(
    solver=SolverType.RULE_BASED, grid_import_formula=GridImportFormula.PARK_BASELINE,
    grid_import_nonneg=True, discharge_mask_mode="fixed_window",
)

PRESETS: dict[str, DistBESSSchedulerConfig] = {
    "v1": V1_PRESET, "v2": V2_PRESET, "v3": V3_PRESET, "v4": V4_PRESET, "v5": V5_PRESET,
}


def get_preset(name: str) -> DistBESSSchedulerConfig:
    if name not in PRESETS:
        raise ValueError(f"Unknown preset: {name}. Choose from {list(PRESETS)}")
    return PRESETS[name]


# ═══════════════════════════════════════════════════════════════════════════════
# BESSDistributionScheduler（来自 scheduler.py）
# ═══════════════════════════════════════════════════════════════════════════════

class BESSDistributionScheduler:
    """多变压器公共母线下的分布式储能调度模型（统一版本）。"""

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
        config: DistBESSSchedulerConfig,
        park_transform_capacity: float | None = None,
        cross_flow_penalty: float = 1e-6,
    ):
        self.schedule_time_range = schedule_time_range
        self.schedule_time_length = len(schedule_time_range)
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
        time_range = pd.to_datetime(schedule_time_range)
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

    @staticmethod
    def _build_discharge_mask_fixed_window(schedule_time_range: List, ele_types: List) -> np.ndarray:
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
        charge_target_indices: list[int] = []
        discharge_target_indices: list[int] = []
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
        if self.config.solver == SolverType.LP:
            return self._solve_lp()
        return self._solve_rule()

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
        allocation_by_source = [cp.Variable((row, column), nonneg=True) for _ in range(row)]
        charge_power_matrix = -e_c_in_matrix

        if self.config.grid_import_formula == GridImportFormula.SUM_LOAD:
            system_grid_import = cp.sum(grid_to_load_matrix, axis=0) + cp.sum(charge_power_matrix, axis=0)
        else:
            system_grid_import = park_d + cp.sum(charge_power_matrix, axis=0) - cp.sum(e_c_out_matrix, axis=0)

        net_power_matrix = e_c_in_matrix + e_c_out_matrix
        cross_flow_terms = []
        constraints: list = []
        if self.config.grid_import_nonneg:
            constraints += [system_grid_import >= 0]

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
            supplied_by_bess = self._sum_or_zero(
                [allocation_by_source[s][target_j, :] for s in range(row)], column,
            )
            cross_in = self._sum_or_zero(
                [allocation_by_source[s][target_j, :] for s in range(row) if s != target_j], column,
            )
            constraints += [grid_to_load_matrix[target_j, :] + supplied_by_bess == local_d[target_j, :]]
            constraints += [
                grid_to_load_matrix[target_j, :] + cross_in + charge_power_matrix[target_j, :]
                <= transform_capacity_vec[target_j, 0]
            ]
        for source_i in range(row):
            cross_out = self._sum_or_zero(
                [allocation_by_source[source_i][t, :] for t in range(row) if t != source_i], column,
            )
            cross_flow_terms.append(cross_out)
            constraints += [cross_out <= transform_capacity_vec[source_i, 0]]

        constraints += [e_c_out_matrix <= e_c_max_vec, e_c_out_matrix >= 0]
        constraints += [e_c_in_matrix <= 0, e_c_in_matrix >= e_c_min_vec]
        constraints += [soc_matrix >= e_s_min_vec, soc_matrix <= e_s_max_vec]

        discharge_allowed_mask = self._build_discharge_allowed_mask()
        for j, ts in enumerate(pd.to_datetime(self.schedule_time_range)):
            if self._charge_allowed(ts):
                constraints += [e_c_out_matrix[:, j] == 0]
            elif discharge_allowed_mask[j]:
                constraints += [e_c_in_matrix[:, j] == 0]
            else:
                constraints += [e_c_in_matrix[:, j] == 0, e_c_out_matrix[:, j] == 0]

        smooth_penalty = 0.0
        if self.config.smooth_penalty_weight > 0 and column > 1:
            smooth_delta = cp.Variable((row, column - 1), nonneg=True)
            net_power_step_delta = net_power_matrix[:, 1:] - net_power_matrix[:, :-1]
            constraints += [smooth_delta >= net_power_step_delta, smooth_delta >= -net_power_step_delta]
            smooth_penalty = self.config.smooth_penalty_weight * cp.sum(smooth_delta)

        if self.config.ramp_rate_fraction_per_step is not None and column > 1:
            ramp_limit = np.repeat(self.config.ramp_rate_fraction_per_step * e_c_max_vec, column - 1, axis=1)
            net_power_step_delta = net_power_matrix[:, 1:] - net_power_matrix[:, :-1]
            constraints += [net_power_step_delta <= ramp_limit, net_power_step_delta >= -ramp_limit]

        charge_target_indices, discharge_target_indices = self._build_daily_soc_target_indices()
        soc_target_penalty = 0.0
        if self.config.charge_target_penalty_weight > 0 and charge_target_indices:
            charge_shortfall = cp.Variable((row, len(charge_target_indices)), nonneg=True)
            for k, idx in enumerate(charge_target_indices):
                constraints += [soc_matrix[:, idx] + charge_shortfall[:, k] >= e_s_max_vec[:, 0]]
            soc_target_penalty += self.config.charge_target_penalty_weight * cp.sum(charge_shortfall)
        if self.config.discharge_target_penalty_weight > 0 and discharge_target_indices:
            discharge_surplus = cp.Variable((row, len(discharge_target_indices)), nonneg=True)
            for k, idx in enumerate(discharge_target_indices):
                constraints += [soc_matrix[:, idx] - discharge_surplus[:, k] <= e_s_min_vec[:, 0]]
            soc_target_penalty += self.config.discharge_target_penalty_weight * cp.sum(discharge_surplus)

        cross_flow_total = self._sum_or_zero(cross_flow_terms, column)
        energy_cost = time_ratio * system_grid_import @ p
        max_demand_cost = self.max_demand_price * cp.max(system_grid_import)
        cross_flow_penalty = self.cross_flow_penalty * cp.sum(cross_flow_total)
        obj = cp.Minimize(energy_cost + max_demand_cost + cross_flow_penalty + smooth_penalty + soc_target_penalty)

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
            raise ValueError(f"optimization failed: {prob.status}; attempts: {'; '.join(solver_errors)}")

        charge_array = e_c_in_matrix.value
        discharge_array = e_c_out_matrix.value
        if charge_array is None or discharge_array is None:
            raise ValueError(f"optimization returned empty solution: {prob.status}")
        if np.isnan(charge_array).any() or np.isnan(discharge_array).any():
            raise ValueError(f"optimization returned NaN: {prob.status}")

        allocation_values = [a.value for a in allocation_by_source]
        grid_to_load_array = grid_to_load_matrix.value
        charge_power_array = -charge_array
        transformer_import = np.zeros((row, column))
        transformer_export = np.zeros((row, column))
        for tj in range(row):
            ci = sum(allocation_values[si][tj, :] for si in range(row) if si != tj)
            transformer_import[tj, :] = grid_to_load_array[tj, :] + ci + charge_power_array[tj, :]
        for si in range(row):
            transformer_export[si, :] = sum(allocation_values[si][tj, :] for tj in range(row) if tj != si)

        if self.config.grid_import_formula == GridImportFormula.SUM_LOAD:
            grid_import_total = np.sum(grid_to_load_array, axis=0) + np.sum(charge_power_array, axis=0)
        else:
            grid_import_total = np.asarray(park_d + np.sum(charge_power_array, axis=0) - np.sum(discharge_array, axis=0))

        self.last_solution = {
            "allocation_by_source": allocation_values,
            "grid_to_load": grid_to_load_array,
            "grid_import_total": grid_import_total,
            "transformer_import": transformer_import,
            "transformer_export": transformer_export,
            "soc": soc_matrix.value,
        }
        return result, charge_array, discharge_array

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
                    soc_room = max((soc_max[i] - soc[i]) / (charge_eff[i] * time_ratio), 0.0)
                    tx_room = max(transform_capacity[i] - local_load[i], 0.0)
                    charge_power[i] = min(charge_limit[i], soc_room, tx_room)
                    soc[i] += charge_power[i] * charge_eff[i] * time_ratio
                charge_array[:, t] = -charge_power
                grid_to_load_array[:, t] = local_load
                transformer_import[:, t] = local_load + charge_power
            elif discharge_allowed_mask[t]:
                remaining_load = local_load.copy()
                remaining_park = max(float(park_d[t]), 0.0)
                for si in range(row):
                    sp = min(discharge_limit[si], max((soc[si] - soc_min[si]) * discharge_eff[si] / time_ratio, 0.0))
                    sp = min(sp, remaining_park)
                    if sp <= 0:
                        continue
                    lt = min(sp, remaining_load[si], remaining_park)
                    allocation_values[si][si, t] = lt
                    remaining_load[si] -= lt
                    remaining_park -= lt
                    sp -= lt
                    cxr = transform_capacity[si]
                    for tj in range(row):
                        if tj == si or sp <= 0 or remaining_park <= 0:
                            continue
                        ct = min(sp, remaining_load[tj], remaining_park, cxr)
                        if ct <= 0:
                            continue
                        allocation_values[si][tj, t] = ct
                        remaining_load[tj] -= ct
                        remaining_park -= ct
                        sp -= ct
                        cxr -= ct
                    dp = float(allocation_values[si][:, t].sum())
                    discharge_array[si, t] = dp
                    soc[si] -= dp / discharge_eff[si] * time_ratio
                supplied = np.sum(np.stack([a[:, t] for a in allocation_values], axis=0), axis=0)
                grid_to_load_array[:, t] = np.maximum(local_load - supplied, 0.0)
                for tj in range(row):
                    ci = sum(allocation_values[si][tj, t] for si in range(row) if si != tj)
                    transformer_import[tj, t] = grid_to_load_array[tj, t] + ci
                for si in range(row):
                    transformer_export[si, t] = sum(allocation_values[si][tj, t] for tj in range(row) if tj != si)
            else:
                grid_to_load_array[:, t] = local_load
                transformer_import[:, t] = local_load

            soc = np.clip(soc, soc_min, soc_max)
            soc_matrix[:, t] = soc

        self.last_solution = {
            "allocation_by_source": allocation_values,
            "grid_to_load": grid_to_load_array,
            "grid_import_total": np.maximum(
                np.asarray(park_d + np.sum(-charge_array, axis=0) - np.sum(discharge_array, axis=0)), 0.0,
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
        schedule_list = []
        for device_i in range(self.devices_num):
            power_array_i = np.around(charge_array[device_i] + discharge_array[device_i], decimals=3)
            power_array_i = np.asarray(list(map(lambda x: 0.0 if abs(x) < 0.1 else x, power_array_i.tolist())))
            schedule_list.append(pd.DataFrame({"value": power_array_i}, index=self.schedule_time_range))
        return schedule_list

    def run(self):
        _, charge_array, discharge_array = self.modeling2solving()
        return self.schedule_generate(charge_array, discharge_array)


# ═══════════════════════════════════════════════════════════════════════════════
# 优化器函数（来自 optimizer.py）
# ═══════════════════════════════════════════════════════════════════════════════

_FULL_GRID_WORKER_CONTEXT: dict | None = None


def combo_key(cabinet_counts: tuple[int, ...], transformer_configs=None) -> str:
    configs = tuple(transformer_configs or TRANSFORMERS)
    return "__".join(f"{cfg.name}-{cabinet_counts[idx]}" for idx, cfg in enumerate(configs))


def load_series(path: Path, start_time: datetime, end_time: datetime) -> pd.Series:
    return read_time_value_csv(path, start_time, end_time)


def load_inputs(base_dir: Path, start_time: datetime, end_time: datetime,
                system_config: DistBESSConfig, load_mode: str = "park_file"):
    local_loads = {cfg.name: load_series(base_dir / cfg.load_file, start_time, end_time)
                   for cfg in system_config.transformers}
    ele_price = pd.read_csv(base_dir / "ele_price.csv")
    ele_price["time"] = pd.to_datetime(ele_price["time"])
    ele_price["value"] = pd.to_numeric(ele_price["value"], errors="raise")
    ele_price = ele_price[(ele_price["time"] >= start_time) & (ele_price["time"] < end_time)]
    ele_price = ele_price.set_index("time").sort_index()

    expected_index = next(iter(local_loads.values())).index
    for name, series in local_loads.items():
        if not series.index.equals(expected_index):
            raise ValueError(f"{name} load time index does not match the system index")
    if not ele_price.index.equals(expected_index):
        raise ValueError("ele_price.csv time index does not match system load index")
    if expected_index.to_series().diff().dropna().nunique() != 1:
        raise ValueError("demand time index must have a constant frequency")

    if load_mode == "sum_local":
        system_load = pd.concat(local_loads.values(), axis=1).sum(axis=1)
    else:
        system_load = load_series(base_dir / system_config.park_load_file, start_time, end_time)
        if not system_load.index.equals(expected_index):
            raise ValueError(f"{system_config.park_load_file} time index does not match system index")
    return system_load, local_loads, ele_price


def build_devices_info(cabinet_counts: tuple[int, ...], transformer_configs=None):
    configs = tuple(transformer_configs or TRANSFORMERS)
    devices_info = []
    for idx, cfg in enumerate(configs):
        power = cabinet_counts[idx] * _CABINET_POWER_KW
        capacity = cabinet_counts[idx] * _CABINET_CAPACITY_KWH
        devices_info.append({
            "usable_depth": 0.90, "charge_loss": 0.92, "discharge_loss": 0.95,
            "es_charge_max": power, "es_charge_min": -power,
            "es_capacity_max": capacity, "es_capacity_min": 0.0,
            "transform_capacity": cfg.transformer_capacity, "cabinet_count": cabinet_counts[idx],
        })
    return devices_info


def monthly_demand_cost(load: pd.Series, max_demand_price: float) -> float:
    return monthly_peak_demand_cost(load, max_demand_price)


def calculate_system_power_limit(system_load: pd.Series) -> float:
    return float(system_load.max())


def calculate_system_max_cabinets(system_load: pd.Series) -> tuple[float, int]:
    kw = float(system_load.max())
    return kw, max(int(kw // _CABINET_POWER_KW), 0)


def cabinet_groups(system_config: DistBESSConfig) -> tuple[tuple[str, ...], ...]:
    if system_config.cabinet_groups:
        return system_config.cabinet_groups
    groups: dict[str, list[str]] = {}
    for cfg in system_config.transformers:
        groups.setdefault(cfg.name.split("_", 1)[0], []).append(cfg.name)
    return tuple(tuple(names) for names in groups.values())


def cabinet_count_by_name(cabinet_counts: tuple[int, ...], system_config: DistBESSConfig) -> dict[str, int]:
    return {cfg.name: cabinet_counts[idx] for idx, cfg in enumerate(system_config.transformers)}


def group_equal_cabinet_violation_count(cabinet_counts: tuple[int, ...], system_config: DistBESSConfig) -> int:
    counts = cabinet_count_by_name(cabinet_counts, system_config)
    return sum(int(len({counts[name] for name in group}) != 1) for group in cabinet_groups(system_config))


def group_cabinet_count(cabinet_counts: tuple[int, ...], system_config: DistBESSConfig, group_prefix: str) -> int:
    counts = cabinet_count_by_name(cabinet_counts, system_config)
    group = next(g for g in cabinet_groups(system_config) if g[0].startswith(group_prefix))
    return counts[group[0]]


def min_required_total_cabinets(system_config: DistBESSConfig, min_cpt: int) -> int:
    return len(system_config.transformers) * min_cpt


def is_combo_feasible(cabinet_counts: tuple[int, ...], system_config: DistBESSConfig,
                      equality_mode: CabinetEqualityMode, min_cpt: int = 0,
                      system_max_cabinets: int | None = None) -> bool:
    if len(cabinet_counts) != len(system_config.transformers):
        return False
    if not all(min_cpt <= c <= cfg.max_cabinets for c, cfg in zip(cabinet_counts, system_config.transformers)):
        return False
    if equality_mode == CabinetEqualityMode.NONE:
        if system_max_cabinets is not None and sum(cabinet_counts) > system_max_cabinets:
            return False
    elif equality_mode == CabinetEqualityMode.GLOBAL:
        if len(set(cabinet_counts)) != 1:
            return False
    elif equality_mode == CabinetEqualityMode.GROUP:
        counts = cabinet_count_by_name(cabinet_counts, system_config)
        for group in cabinet_groups(system_config):
            if len({counts[name] for name in group}) != 1:
                return False
    return True


def full_grid_candidates(system_config: DistBESSConfig, equality_mode: CabinetEqualityMode,
                         max_cabinets_override: int | None = None,
                         system_max_cabinets: int | None = None,
                         min_cpt: int = 0) -> Iterable[tuple[int, ...]]:
    if equality_mode == CabinetEqualityMode.NONE:
        ranges = []
        for cfg in system_config.transformers:
            mc = min(cfg.max_cabinets, max_cabinets_override) if max_cabinets_override else cfg.max_cabinets
            ranges.append(range(min_cpt, mc + 1))
        for combo in itertools.product(*ranges):
            if system_max_cabinets is None or sum(combo) <= system_max_cabinets:
                yield combo
    elif equality_mode == CabinetEqualityMode.GLOBAL:
        common_max = min(cfg.max_cabinets for cfg in system_config.transformers)
        if max_cabinets_override is not None:
            common_max = min(common_max, max_cabinets_override)
        for count in range(min_cpt, common_max + 1):
            yield tuple(count for _ in system_config.transformers)
    elif equality_mode == CabinetEqualityMode.GROUP:
        cfg_by_name = {cfg.name: cfg for cfg in system_config.transformers}
        group_ranges = []
        for group in cabinet_groups(system_config):
            common_max = min(cfg_by_name[n].max_cabinets for n in group)
            if max_cabinets_override is not None:
                common_max = min(common_max, max_cabinets_override)
            group_ranges.append(range(min_cpt, common_max + 1))
        for group_counts in product(*group_ranges):
            cn: dict[str, int] = {}
            for group, count in zip(cabinet_groups(system_config), group_counts):
                for name in group:
                    cn[name] = count
            yield tuple(cn[cfg.name] for cfg in system_config.transformers)


def candidate_neighbors(cabinet_counts: tuple[int, ...], system_config: DistBESSConfig,
                        equality_mode: CabinetEqualityMode, min_cpt: int = 0,
                        system_max_cabinets: int | None = None) -> Iterable[tuple[int, ...]]:
    if not cabinet_counts:
        return
    if equality_mode == CabinetEqualityMode.NONE:
        for idx, cfg in enumerate(system_config.transformers):
            if cabinet_counts[idx] < cfg.max_cabinets:
                c = list(cabinet_counts); c[idx] += 1; ct = tuple(c)
                if is_combo_feasible(ct, system_config, equality_mode, min_cpt, system_max_cabinets):
                    yield ct
    elif equality_mode == CabinetEqualityMode.GLOBAL:
        nc = cabinet_counts[0] + 1
        cm = min(cfg.max_cabinets for cfg in system_config.transformers)
        if nc <= cm:
            ct = tuple(nc for _ in system_config.transformers)
            if is_combo_feasible(ct, system_config, equality_mode, min_cpt):
                yield ct
    elif equality_mode == CabinetEqualityMode.GROUP:
        idx_by_name = {cfg.name: i for i, cfg in enumerate(system_config.transformers)}
        for group in cabinet_groups(system_config):
            c = list(cabinet_counts)
            nc = cabinet_counts[idx_by_name[group[0]]] + 1
            for name in group:
                c[idx_by_name[name]] = nc
            ct = tuple(c)
            if is_combo_feasible(ct, system_config, equality_mode, min_cpt):
                yield ct


def capped_max_capacity_combo(system_config: DistBESSConfig, equality_mode: CabinetEqualityMode,
                              system_max_cabinets: int | None = None, min_cpt: int = 0) -> tuple[int, ...]:
    for cfg in system_config.transformers:
        if cfg.max_cabinets < min_cpt:
            raise ValueError(f"transformer={cfg.name} max_cabinets={cfg.max_cabinets} < min_cpt={min_cpt}")
    if equality_mode == CabinetEqualityMode.NONE:
        counts = [min_cpt for _ in system_config.transformers]
        remaining = (system_max_cabinets or sum(cfg.max_cabinets for cfg in system_config.transformers)) - sum(counts)
        for idx, cfg in enumerate(system_config.transformers):
            extra = min(cfg.max_cabinets - counts[idx], remaining)
            counts[idx] += extra; remaining -= extra
        return tuple(counts)
    if equality_mode == CabinetEqualityMode.GLOBAL:
        cm = min(cfg.max_cabinets for cfg in system_config.transformers)
        return tuple(cm for _ in system_config.transformers)
    counts: dict[str, int] = {}
    cfg_by_name = {cfg.name: cfg for cfg in system_config.transformers}
    for group in cabinet_groups(system_config):
        cm = min(cfg_by_name[n].max_cabinets for n in group)
        for name in group:
            counts[name] = cm
    return tuple(counts[cfg.name] for cfg in system_config.transformers)


def zero_schedule(index: pd.DatetimeIndex, system_config: DistBESSConfig, cabinet_counts: tuple[int, ...]) -> pd.DataFrame:
    data: dict[str, Any] = {"time": index}
    for cfg in system_config.transformers:
        data[f"power_{cfg.name}"] = 0.0; data[f"soc_{cfg.name}"] = 0.0
    data["power_total"] = 0.0; data["grid_import_total"] = 0.0
    for cfg in system_config.transformers:
        data[f"transformer_import_{cfg.name}"] = 0.0; data[f"transformer_export_{cfg.name}"] = 0.0
    return pd.DataFrame(data)


def _fill_zero_schedule_load_columns(schedule_df: pd.DataFrame, local_loads: dict[str, pd.Series],
                                     system_config: DistBESSConfig) -> pd.DataFrame:
    schedule = schedule_df.copy()
    schedule["time"] = pd.to_datetime(schedule["time"])
    indexed = schedule.set_index("time")
    for cfg in system_config.transformers:
        indexed[f"transformer_import_{cfg.name}"] = local_loads[cfg.name].reindex(indexed.index).to_numpy()
        indexed[f"transformer_export_{cfg.name}"] = 0.0
    indexed["grid_import_total"] = sum(local_loads[cfg.name].reindex(indexed.index) for cfg in system_config.transformers)
    return indexed.reset_index()


def optimize_combo(cabinet_counts: tuple[int, ...], system_config: DistBESSConfig,
                   system_load: pd.Series, local_loads: dict[str, pd.Series],
                   ele_price: pd.DataFrame, max_demand_price: float,
                   start_time: datetime, end_time: datetime, freq_minutes: int,
                   scheduler_config: DistBESSSchedulerConfig) -> tuple[pd.DataFrame, float]:
    if sum(cabinet_counts) == 0:
        schedule = zero_schedule(system_load.index, system_config, cabinet_counts)
        return _fill_zero_schedule_load_columns(schedule, local_loads, system_config), 0.0

    devices_info = build_devices_info(cabinet_counts, system_config.transformers)
    monthly_frames = []
    objective_value = 0.0
    for vs, ve in generate_month_ranges(start_time, end_time):
        mi = system_load[(system_load.index >= vs) & (system_load.index < ve)].index
        scheduler = BESSDistributionScheduler(
            mi.to_list(), system_load.loc[mi].to_list(),
            [local_loads[cfg.name].loc[mi].to_list() for cfg in system_config.transformers],
            ele_price.loc[mi, "value"].to_list(), ele_price.loc[mi, "type"].to_list(),
            devices_info, [0.0] * len(system_config.transformers),
            max_demand_price, freq_minutes, config=scheduler_config,
        )
        schedule_list = scheduler.run()
        solution = scheduler.last_solution
        objective_value += float(scheduler.last_objective_value or 0.0)

        month_df = pd.DataFrame({"time": mi})
        power_cols = []
        for idx, cfg in enumerate(system_config.transformers):
            col = f"power_{cfg.name}"
            month_df[col] = schedule_list[idx]["value"].to_numpy()
            month_df[f"soc_{cfg.name}"] = solution["soc"][idx]
            month_df[f"transformer_import_{cfg.name}"] = solution["transformer_import"][idx]
            month_df[f"transformer_export_{cfg.name}"] = solution["transformer_export"][idx]
            power_cols.append(col)
        month_df["power_total"] = month_df[power_cols].sum(axis=1)
        month_df["grid_import_total"] = solution["grid_import_total"]
        abs_ = solution["allocation_by_source"]
        for si, scfg in enumerate(system_config.transformers):
            for ti, tcfg in enumerate(system_config.transformers):
                month_df[f"allocation_{scfg.name}_to_{tcfg.name}"] = abs_[si][ti]
        monthly_frames.append(month_df)

    return pd.concat(monthly_frames, ignore_index=True), objective_value


def evaluate_schedule(cabinet_counts: tuple[int, ...], system_config: DistBESSConfig,
                      schedule_df: pd.DataFrame, objective_value: float,
                      system_load: pd.Series, ele_price: pd.DataFrame,
                      max_demand_price: float, system_power_limit_kw: float,
                      equality_mode: CabinetEqualityMode, min_cpt: int,
                      system_max_cabinets: int | None = None) -> dict:
    schedule = schedule_df.copy()
    schedule["time"] = pd.to_datetime(schedule["time"])
    schedule = schedule.set_index("time").sort_index()
    dt_hours = (schedule.index[1] - schedule.index[0]).total_seconds() / 3600
    git = schedule["grid_import_total"]

    origin_ec = float((system_load * ele_price["value"] * dt_hours).sum())
    opt_ec = float((git * ele_price["value"] * dt_hours).sum())
    ori_mdc = monthly_demand_cost(system_load, max_demand_price)
    opt_mdc = monthly_demand_cost(git, max_demand_price)
    revenue = origin_ec + ori_mdc - opt_ec - opt_mdc

    tvc = 0
    for cfg in system_config.transformers:
        tvc += int((schedule[f"transformer_import_{cfg.name}"] > cfg.transformer_capacity + _CONSTRAINT_TOLERANCE_KW).sum())
        tvc += int((schedule[f"transformer_export_{cfg.name}"] > cfg.transformer_capacity + _CONSTRAINT_TOLERANCE_KW).sum())

    result: dict[str, Any] = {
        "system_name": system_config.name,
        "combo_key": combo_key(cabinet_counts, system_config.transformers),
        "objective_value": objective_value, "revenue": revenue,
        "origin_energy_cost": origin_ec, "opt_energy_cost": opt_ec,
        "ori_max_demand_cost": ori_mdc, "opt_max_demand_cost": opt_mdc,
        "transformer_violation_count": tvc, "system_power_limit_kw": system_power_limit_kw,
        "min_cabinets_per_transformer": min_cpt,
        "min_required_total_cabinets": min_required_total_cabinets(system_config, min_cpt),
        "min_cabinet_violation_count": sum(int(c < min_cpt) for c in cabinet_counts),
        "total_cabinets": sum(cabinet_counts),
        "total_power_kw": sum(cabinet_counts) * _CABINET_POWER_KW,
        "total_capacity_kwh": sum(cabinet_counts) * _CABINET_CAPACITY_KWH,
    }
    if equality_mode == CabinetEqualityMode.NONE and system_max_cabinets is not None:
        result["system_max_cabinets"] = system_max_cabinets
        result["system_cabinet_limit_violation"] = int(sum(cabinet_counts) > system_max_cabinets)
    if equality_mode in (CabinetEqualityMode.GLOBAL, CabinetEqualityMode.GROUP):
        result["equal_cabinets_required"] = True
        if equality_mode == CabinetEqualityMode.GLOBAL:
            result["equal_cabinet_violation_count"] = int(len(set(cabinet_counts)) != 1)
        else:
            result["equal_cabinet_violation_count"] = group_equal_cabinet_violation_count(cabinet_counts, system_config)
    if equality_mode == CabinetEqualityMode.GROUP:
        result["cabinet_group_rule"] = "__".join("_".join(g) for g in cabinet_groups(system_config))
        result["group_equal_cabinet_violation_count"] = group_equal_cabinet_violation_count(cabinet_counts, system_config)
        for group in cabinet_groups(system_config):
            prefix = group[0].split("_", 1)[0]
            result[f"{prefix}_group_cabinets"] = group_cabinet_count(cabinet_counts, system_config, prefix)
    for idx, cfg in enumerate(system_config.transformers):
        c = cabinet_counts[idx]
        result[f"{cfg.name}_cabinets"] = c
        result[f"{cfg.name}_power_kw"] = c * _CABINET_POWER_KW
        result[f"{cfg.name}_capacity_kwh"] = c * _CABINET_CAPACITY_KWH
    return result


def write_schedule(output_dir: Path, schedule_df: pd.DataFrame,
                   cabinet_counts: tuple[int, ...], system_config: DistBESSConfig) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    schedule_df.to_csv(output_dir / f"schedule_result_combo_{combo_key(cabinet_counts, system_config.transformers)}.csv", index=False)


def _evaluate_and_write_combo(cabinet_counts, system_config, system_load, local_loads, ele_price,
                              output_dir, start_time, end_time, max_demand_price, freq_minutes,
                              system_power_limit_kw, equality_mode, min_cpt, system_max_cabinets,
                              iteration, scheduler_config) -> dict:
    if not is_combo_feasible(cabinet_counts, system_config, equality_mode, min_cpt, system_max_cabinets):
        raise ValueError(f"infeasible combo: {combo_key(cabinet_counts, system_config.transformers)}")
    print(f"evaluate system={system_config.name} iteration={iteration} combo={combo_key(cabinet_counts, system_config.transformers)}", flush=True)
    started = perf_counter()
    sdf, ov = optimize_combo(cabinet_counts, system_config, system_load, local_loads, ele_price,
                             max_demand_price, start_time, end_time, freq_minutes, scheduler_config)
    metrics = evaluate_schedule(cabinet_counts, system_config, sdf, ov, system_load, ele_price,
                                max_demand_price, system_power_limit_kw, equality_mode, min_cpt, system_max_cabinets)
    metrics["first_seen_iteration"] = iteration; metrics["selected"] = False
    write_schedule(output_dir, sdf, cabinet_counts, system_config)
    print(f"finished system={system_config.name} combo={combo_key(cabinet_counts, system_config.transformers)} revenue={metrics['revenue']:.2f} sec={perf_counter()-started:.2f}", flush=True)
    return metrics


def _init_full_grid_worker(base_dir, output_dir, start_time, end_time, max_demand_price, freq_minutes,
                           system_config, load_mode, equality_mode, min_cpt, system_max_cabinets, scheduler_config):
    global _FULL_GRID_WORKER_CONTEXT
    sl, ll, ep = load_inputs(base_dir, start_time, end_time, system_config, load_mode)
    splk = calculate_system_power_limit(sl)
    _FULL_GRID_WORKER_CONTEXT = {
        "base_dir": base_dir, "output_dir": output_dir, "start_time": start_time, "end_time": end_time,
        "max_demand_price": max_demand_price, "freq_minutes": freq_minutes, "system_config": system_config,
        "system_load": sl, "local_loads": ll, "ele_price": ep, "system_power_limit_kw": splk,
        "equality_mode": equality_mode, "min_cpt": min_cpt, "system_max_cabinets": system_max_cabinets,
        "scheduler_config": scheduler_config,
    }


def _evaluate_full_grid_combo_worker(task):
    if _FULL_GRID_WORKER_CONTEXT is None:
        raise RuntimeError("worker context not initialized")
    iteration, cabinet_counts = task
    ctx = _FULL_GRID_WORKER_CONTEXT
    return _evaluate_and_write_combo(
        cabinet_counts, ctx["system_config"], ctx["system_load"], ctx["local_loads"], ctx["ele_price"],
        ctx["output_dir"], ctx["start_time"], ctx["end_time"], ctx["max_demand_price"], ctx["freq_minutes"],
        ctx["system_power_limit_kw"], ctx["equality_mode"], ctx["min_cpt"], ctx["system_max_cabinets"],
        iteration, ctx["scheduler_config"],
    )


def _mark_best_full_grid_result(evaluated: dict) -> None:
    if not evaluated:
        return
    for m in evaluated.values():
        m["selected"] = False
    best = max(evaluated, key=lambda c: evaluated[c]["revenue"])
    evaluated[best]["selected"] = True


def run_capacity_search(base_dir: Path, output_dir: Path, start_time: datetime, end_time: datetime,
                        max_demand_price: float, freq_minutes: int, system_name: str,
                        scheduler_config: DistBESSSchedulerConfig, equality_mode: CabinetEqualityMode,
                        load_mode: str = "park_file", search_mode: str = "coordinate",
                        workers: int = 1, min_cpt: int = 1) -> pd.DataFrame:
    if system_name not in SYSTEMS:
        raise ValueError(f"unsupported system_name: {system_name}")
    if workers < 1:
        raise ValueError("workers must be >= 1")
    system_config = SYSTEMS[system_name]
    system_load, local_loads, ele_price = load_inputs(base_dir, start_time, end_time, system_config, load_mode)
    splk = calculate_system_power_limit(system_load)
    smc = None
    if equality_mode == CabinetEqualityMode.NONE:
        _, smc = calculate_system_max_cabinets(system_load)
    for cfg in system_config.transformers:
        if cfg.max_cabinets < min_cpt:
            raise ValueError(f"transformer={cfg.name} max_cabinets={cfg.max_cabinets} < min_cpt={min_cpt}")

    evaluated: dict[tuple[int, ...], dict] = {}

    def evaluate(cc, iteration, selected=False):
        if not is_combo_feasible(cc, system_config, equality_mode, min_cpt, smc):
            raise ValueError(f"infeasible combo: {combo_key(cc, system_config.transformers)}")
        if cc not in evaluated:
            print(f"evaluate system={system_config.name} iteration={iteration} combo={combo_key(cc, system_config.transformers)}", flush=True)
            started = perf_counter()
            sdf, ov = optimize_combo(cc, system_config, system_load, local_loads, ele_price,
                                     max_demand_price, start_time, end_time, freq_minutes, scheduler_config)
            metrics = evaluate_schedule(cc, system_config, sdf, ov, system_load, ele_price,
                                        max_demand_price, splk, equality_mode, min_cpt, smc)
            metrics["first_seen_iteration"] = iteration; metrics["selected"] = False
            evaluated[cc] = metrics
            write_schedule(output_dir, sdf, cc, system_config)
            print(f"finished system={system_config.name} combo={combo_key(cc, system_config.transformers)} revenue={metrics['revenue']:.2f} sec={perf_counter()-started:.2f}", flush=True)
        if selected:
            evaluated[cc]["selected"] = True
        return evaluated[cc]

    if search_mode == "full_grid":
        combos = list(full_grid_candidates(system_config, equality_mode, system_max_cabinets=smc, min_cpt=min_cpt))
        if workers > 1:
            output_dir.mkdir(parents=True, exist_ok=True)
            tasks = [(i, c) for i, c in enumerate(combos)]
            with ProcessPoolExecutor(max_workers=workers, initializer=_init_full_grid_worker,
                                     initargs=(base_dir, output_dir, start_time, end_time, max_demand_price,
                                               freq_minutes, system_config, load_mode, equality_mode,
                                               min_cpt, smc, scheduler_config)) as executor:
                ftc = {executor.submit(_evaluate_full_grid_combo_worker, t): t[1] for t in tasks}
                for f in as_completed(ftc):
                    evaluated[ftc[f]] = f.result()
        else:
            for i, c in enumerate(combos):
                evaluate(c, iteration=i)
        _mark_best_full_grid_result(evaluated)
    elif search_mode == "max_capacity":
        current = tuple(min_cpt for _ in system_config.transformers)
        evaluate(current, iteration=0, selected=True)
        evaluate(capped_max_capacity_combo(system_config, equality_mode, smc, min_cpt), iteration=1, selected=True)
    elif search_mode == "coordinate":
        current = tuple(min_cpt for _ in system_config.transformers)
        cm = evaluate(current, iteration=0, selected=True)
        iteration = 1
        while True:
            candidates = [(c, evaluate(c, iteration=iteration)) for c in candidate_neighbors(current, system_config, equality_mode, min_cpt, smc)]
            if not candidates:
                break
            bc, bm = max(candidates, key=lambda x: x[1]["revenue"])
            if bm["revenue"] <= cm["revenue"] + 1e-6:
                break
            current = bc
            cm = evaluate(current, iteration=iteration, selected=True)
            iteration += 1
    else:
        raise ValueError(f"unsupported search_mode: {search_mode}")

    summary_df = pd.DataFrame(evaluated.values()).sort_values(["revenue", "total_power_kw"], ascending=[False, True])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(output_dir / "capacity_search_summary.csv", index=False)
    return summary_df


_PRESET_CONFIGS = {
    "v1": {"systems": ["338", "342"], "equality": CabinetEqualityMode.NONE, "load_mode": "sum_local"},
    "v2": {"systems": ["338", "342"], "equality": CabinetEqualityMode.GLOBAL, "load_mode": "sum_local"},
    "v3": {"systems": ["park"], "equality": CabinetEqualityMode.GROUP, "load_mode": "park_file"},
    "v4": {"systems": ["park"], "equality": CabinetEqualityMode.GROUP, "load_mode": "park_file"},
    "v5": {"systems": ["park"], "equality": CabinetEqualityMode.GROUP, "load_mode": "park_file"},
}


def run_systems(base_dir: Path, opt_result_dir: Path, start_time: datetime, end_time: datetime,
                max_demand_price: float, freq_minutes: int, search_mode: str, system_name: str,
                workers: int, min_cpt: int, preset: str = "v4") -> dict[str, pd.DataFrame]:
    if preset not in _PRESET_CONFIGS:
        raise ValueError(f"Unknown preset: {preset}")
    cfg = _PRESET_CONFIGS[preset]
    sc = PRESETS[preset]
    em = cfg["equality"]
    lm = cfg["load_mode"]
    selected = cfg["systems"] if system_name == "all" else [system_name]
    results = {}
    for name in selected:
        od = opt_result_dir / f"es_scale_experiment_optim_dist_{name}-{preset}"
        results[name] = run_capacity_search(
            base_dir=base_dir, output_dir=od, start_time=start_time, end_time=end_time,
            max_demand_price=max_demand_price, freq_minutes=freq_minutes, search_mode=search_mode,
            system_name=name, workers=workers, min_cpt=min_cpt, scheduler_config=sc,
            equality_mode=em, load_mode=lm,
        )
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# 仿真函数（来自 simulation.py）
# ═══════════════════════════════════════════════════════════════════════════════

OUTPUT_COLUMN_CN = {
    "system_name": "系统名称", "combo_key": "储能柜组合", "revenue": "收益",
    "max_demand_rise_cost": "需量电费变化", "ori_energy": "原始负荷电量",
    "ori_cost": "原始总成本", "opt_cost": "优化后总成本",
    "charge_energy": "储能充电电量", "discharge_energy": "储能放电电量",
    "charge_balance": "储能充电电费", "discharge_balance": "储能放电收益",
    "transformer_violation_count": "变压器容量违规次数",
    "system_power_limit_kw": "系统原始峰值负荷",
    "system_max_cabinets": "系统最大允许柜数", "system_cabinet_limit_violation": "系统柜数上限违规",
    "equal_cabinets_required": "要求各变压器柜数相等", "equal_cabinet_violation_count": "等柜数约束违规次数",
    "min_cabinets_per_transformer": "单变压器最小柜数", "min_required_total_cabinets": "系统最小必需柜数",
    "min_cabinet_violation_count": "最小柜数违规台数", "total_cabinets": "总储能柜数",
    "total_power_kw": "储能总功率", "total_capacity_kwh": "储能总电容量",
    "cabinet_group_rule": "储能柜分组规则", "group_equal_cabinet_violation_count": "分组等柜数违规次数",
}


def with_chinese_output_columns(result_df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {col: f"{col}_{OUTPUT_COLUMN_CN[col]}" for col in result_df.columns if col in OUTPUT_COLUMN_CN}
    return result_df.rename(columns=rename_map)


@dataclass(frozen=True)
class SimulationResult:
    system_name: str
    combo_key: str
    revenue: float
    max_demand_rise_cost: float
    ori_energy: float
    ori_cost: float
    opt_cost: float
    charge_energy: float
    discharge_energy: float
    charge_balance: float
    discharge_balance: float
    transformer_violation_count: int
    system_power_limit_kw: float
    system_max_cabinets: int | None = None
    system_cabinet_limit_violation: int | None = None
    equal_cabinets_required: bool = False
    equal_cabinet_violation_count: int = 0
    cabinet_group_rule: str = ""
    group_equal_cabinet_violation_count: int = 0
    min_cabinets_per_transformer: int = 0
    min_required_total_cabinets: int = 0
    min_cabinet_violation_count: int = 0
    total_cabinets: int = 0
    total_power_kw: float = 0.0
    total_capacity_kwh: float = 0.0


def monthly_max_cost(load: pd.Series, max_demand_price: float) -> float:
    return monthly_peak_demand_cost(load, max_demand_price)


def parse_cabinet_counts_from_key(key: str) -> tuple[int, ...]:
    return tuple(int(p.rsplit("-", 1)[1]) for p in key.split("__"))


def parse_cabinet_counts_from_schedule(schedule_df: pd.DataFrame, schedule_path: Path) -> tuple[int, ...]:
    if "combo_key" in schedule_df.columns and schedule_df["combo_key"].notna().any():
        return parse_cabinet_counts_from_key(str(schedule_df["combo_key"].dropna().iloc[0]))
    stem = schedule_path.stem
    prefix = "schedule_result_combo_"
    if stem.startswith(prefix):
        return parse_cabinet_counts_from_key(stem[len(prefix):])
    raise ValueError("schedule file must contain combo_key or use schedule_result_combo_<combo>.csv naming.")


def load_base_data(base_dir: Path, system_config: DistBESSConfig, start_time: datetime,
                   end_time: datetime, load_mode: str = "park_file"):
    local_load_dfs = {cfg.name: pd.DataFrame({"value": load_series(base_dir / cfg.load_file, start_time, end_time)})
                      for cfg in system_config.transformers}
    ele_price_df = pd.read_csv(base_dir / "ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])
    ele_price_df["value"] = pd.to_numeric(ele_price_df["value"], errors="raise")
    ele_price_df = ele_price_df[(ele_price_df["time"] >= start_time) & (ele_price_df["time"] < end_time)]
    ele_price_df = ele_price_df.set_index("time").sort_index()

    expected_index = next(iter(local_load_dfs.values())).index
    for name, frame in local_load_dfs.items():
        if not frame.index.equals(expected_index):
            raise ValueError(f"{name} load time index does not match the system index")
    if not ele_price_df.index.equals(expected_index):
        raise ValueError("ele_price.csv time index does not match system load index")

    if load_mode == "sum_local":
        system_load = pd.concat([f["value"] for f in local_load_dfs.values()], axis=1).sum(axis=1)
    else:
        system_load = load_series(base_dir / system_config.park_load_file, start_time, end_time)
        if not system_load.index.equals(expected_index):
            raise ValueError(f"{system_config.park_load_file} time index does not match system index")
    return system_load, local_load_dfs, ele_price_df


def simulate_schedule(schedule_path: Path, base_dir: Path, system_config: DistBESSConfig,
                      max_demand_price: float, start_time: datetime, end_time: datetime,
                      equality_mode: CabinetEqualityMode = CabinetEqualityMode.GROUP,
                      load_mode: str = "park_file", min_cpt: int = 1) -> SimulationResult:
    system_load, _, ele_price_df = load_base_data(base_dir, system_config, start_time, end_time, load_mode)
    schedule_df = pd.read_csv(schedule_path)
    schedule_df["time"] = pd.to_datetime(schedule_df["time"])
    schedule_df = schedule_df[(schedule_df["time"] >= start_time) & (schedule_df["time"] < end_time)]
    schedule_df = schedule_df.set_index("time").sort_index()
    if not schedule_df.index.equals(system_load.index):
        raise ValueError(f"{schedule_path} time index does not match system load index")
    if "grid_import_total" not in schedule_df.columns:
        raise ValueError(f"{schedule_path} missing grid_import_total")
    dt_hours = (system_load.index[1] - system_load.index[0]).total_seconds() / 3600

    cabinet_counts = parse_cabinet_counts_from_schedule(schedule_df.reset_index(), schedule_path)
    combo = combo_key(cabinet_counts, system_config.transformers)
    splk = calculate_system_power_limit(system_load)
    tc = sum(cabinet_counts)
    min_req = len(system_config.transformers) * min_cpt
    min_viol = sum(int(c < min_cpt) for c in cabinet_counts)

    smc_val = None; scli_val = None
    if equality_mode == CabinetEqualityMode.NONE:
        _, smc_val = calculate_system_max_cabinets(system_load)
        scli_val = int(tc > smc_val)

    ecr = equality_mode in (CabinetEqualityMode.GLOBAL, CabinetEqualityMode.GROUP)
    ev = 0
    if equality_mode == CabinetEqualityMode.GLOBAL:
        ev = int(len(set(cabinet_counts)) != 1)
    elif equality_mode == CabinetEqualityMode.GROUP:
        ev = group_equal_cabinet_violation_count(cabinet_counts, system_config)

    gr = ""; gv = 0
    if equality_mode == CabinetEqualityMode.GROUP:
        gr = "__".join("_".join(g) for g in cabinet_groups(system_config))
        gv = group_equal_cabinet_violation_count(cabinet_counts, system_config)

    tvc = 0; ce = 0.0; de = 0.0; cb = 0.0; db = 0.0
    for cfg in system_config.transformers:
        pc, ic, ec_ = f"power_{cfg.name}", f"transformer_import_{cfg.name}", f"transformer_export_{cfg.name}"
        if pc not in schedule_df.columns:
            raise ValueError(f"{schedule_path} missing {pc}")
        if ic not in schedule_df.columns or ec_ not in schedule_df.columns:
            raise ValueError(f"{schedule_path} missing import/export for {cfg.name}")
        tvc += int((schedule_df[ic] > cfg.transformer_capacity + _CONSTRAINT_TOLERANCE_KW).sum())
        tvc += int((schedule_df[ec_] > cfg.transformer_capacity + _CONSTRAINT_TOLERANCE_KW).sum())
        pw = schedule_df[pc]; bal = pw * ele_price_df["value"]
        ce += float(-pw[pw < 0].sum()); de += float(pw[pw > 0].sum())
        cb += float(-bal[bal < 0].sum()); db += float(bal[bal > 0].sum())

    git = schedule_df["grid_import_total"]
    ce *= dt_hours; de *= dt_hours; cb *= dt_hours; db *= dt_hours
    ori_e = float(system_load.sum() * dt_hours)
    oec = float((system_load * ele_price_df["value"] * dt_hours).sum())
    opt_ec = float((git * ele_price_df["value"] * dt_hours).sum())
    omdc = monthly_max_cost(system_load, max_demand_price)
    opt_mdc = monthly_max_cost(git, max_demand_price)
    ori_c = oec + omdc; opt_c = opt_ec + opt_mdc; rev = ori_c - opt_c

    return SimulationResult(
        system_name=system_config.name, combo_key=combo, revenue=rev,
        max_demand_rise_cost=opt_mdc - omdc, ori_energy=ori_e, ori_cost=ori_c, opt_cost=opt_c,
        charge_energy=ce, discharge_energy=de, charge_balance=cb, discharge_balance=db,
        transformer_violation_count=tvc, system_power_limit_kw=splk,
        system_max_cabinets=smc_val, system_cabinet_limit_violation=scli_val,
        equal_cabinets_required=ecr, equal_cabinet_violation_count=ev,
        cabinet_group_rule=gr, group_equal_cabinet_violation_count=gv,
        min_cabinets_per_transformer=min_cpt, min_required_total_cabinets=min_req,
        min_cabinet_violation_count=min_viol, total_cabinets=tc,
        total_power_kw=tc * _CABINET_POWER_KW, total_capacity_kwh=tc * _CABINET_CAPACITY_KWH,
    )


def _find_summary_column(summary_df: pd.DataFrame, english_key: str) -> str | None:
    if english_key in summary_df.columns:
        return english_key
    prefix = f"{english_key}_"
    for col in summary_df.columns:
        if col.startswith(prefix):
            return col
    return None


def _select_combo_key(strategy_path: Path, combo_key_value: str | None) -> str:
    if combo_key_value is not None:
        return combo_key_value
    sp = strategy_path / "capacity_search_summary.csv"
    if not sp.exists():
        raise FileNotFoundError(f"{sp} does not exist")
    sdf = pd.read_csv(sp)
    cc = _find_summary_column(sdf, "combo_key")
    if sdf.empty or cc is None:
        raise ValueError(f"{sp} must contain combo_key")
    sc = _find_summary_column(sdf, "selected")
    if sc is not None:
        sel = sdf[sdf[sc].astype(str).str.lower().isin({"true", "1"})]
        if not sel.empty:
            return str(sel.iloc[0][cc])
    rc = _find_summary_column(sdf, "revenue")
    if rc is not None:
        return str(sdf.sort_values(rc, ascending=False).iloc[0][cc])
    return str(sdf.iloc[0][cc])


def simulate_all(base_dir: Path, strategy_dir: str, system_config: DistBESSConfig,
                 max_demand_price: float, start_time: datetime, end_time: datetime,
                 equality_mode: CabinetEqualityMode = CabinetEqualityMode.GROUP,
                 load_mode: str = "park_file", min_cpt: int = 1) -> pd.DataFrame:
    strategy_path = base_dir / "opt_result" / strategy_dir
    summary_path = strategy_path / "capacity_search_summary.csv"
    if summary_path.exists():
        sdf = pd.read_csv(summary_path)
        cc = _find_summary_column(sdf, "combo_key")
        if cc is None:
            raise ValueError(f"{summary_path} must contain combo_key")
        schedule_files = [strategy_path / f"schedule_result_combo_{k}.csv" for k in sdf[cc].astype(str).tolist()]
    else:
        schedule_files = sorted(strategy_path.glob("schedule_result_combo_*.csv"))
    if not schedule_files:
        raise FileNotFoundError(f"no schedule files in {strategy_path}")
    missing = [p for p in schedule_files if not p.exists()]
    if missing:
        raise FileNotFoundError(f"missing: {', '.join(str(p) for p in missing[:5])}")

    rows = []
    for sf in schedule_files:
        rows.append(simulate_schedule(sf, base_dir, system_config, max_demand_price,
                                      start_time, end_time, equality_mode, load_mode, min_cpt).__dict__)
    result_df = pd.DataFrame(rows).sort_values("revenue", ascending=False)
    output_df = with_chinese_output_columns(result_df)
    output_df.to_csv(strategy_path / "simulation_summary.csv", index=False, encoding="utf-8-sig")
    return result_df


# ═══════════════════════════════════════════════════════════════════════════════
# 对外 API
# ═══════════════════════════════════════════════════════════════════════════════

def run_dist_bess_dispatch(input: DistBESSDispatchInput) -> DistBESSDispatchResult:
    """分布式储能容量搜索。ele_trading 标准函数式 API。"""
    base_dir = Path(input.base_dir)
    opt_result_dir = base_dir / "opt_result"
    result_map = run_systems(
        base_dir=base_dir, opt_result_dir=opt_result_dir,
        start_time=input.start_time, end_time=input.end_time,
        max_demand_price=input.max_demand_price, freq_minutes=input.freq_minutes,
        search_mode=input.search_mode, system_name=input.system_name,
        workers=input.workers, min_cpt=input.min_cabinets_per_transformer,
        preset=input.preset,
    )
    summary_df = next(iter(result_map.values()))
    best_row = summary_df.iloc[0] if not summary_df.empty else {}
    output_name = f"bess_scale_experiment_optim_dist_{input.system_name}-{input.preset}"
    return DistBESSDispatchResult(
        summary=summary_df,
        output_dir=str(opt_result_dir / output_name),
        preset=input.preset,
        system_name=input.system_name,
        best_revenue=float(best_row.get("revenue", 0.0)),
        best_combo_key=str(best_row.get("combo_key", "")),
        best_total_cabinets=int(best_row.get("total_cabinets", 0)),
        best_total_capacity_kwh=float(best_row.get("total_capacity_kwh", 0.0)),
    )
