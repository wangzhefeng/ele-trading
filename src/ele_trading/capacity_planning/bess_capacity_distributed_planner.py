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
from typing import Any, Iterable

for _thread_env_name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_thread_env_name, "1")

import numpy as np
import pandas as pd

from ..optimization.user_side_bess_distributed_dispatch_class import DistributedBESSDispatcher
from ..optimization.interfaces import (
    DistributedBESSDemandChargeConfig,
    DistributedBESSDispatchInput as OptimizationDistributedBESSDispatchInput,
    DistributedBESSDispatchPolicy,
    DistributedBESSNodeParams,
)
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
    current_soc_kwh = [0.0] * len(system_config.transformers)
    for vs, ve in generate_month_ranges(start_time, end_time):
        mi = system_load[(system_load.index >= vs) & (system_load.index < ve)].index
        node_params = [
            DistributedBESSNodeParams(
                name=cfg.name,
                transformer_capacity_kw=cfg.transformer_capacity,
                bess_power_kw=device["es_charge_max"],
                bess_capacity_kwh=device["es_capacity_max"],
                soc_min_kwh=device["es_capacity_min"],
                soc_max_kwh=device["es_capacity_max"] * device["usable_depth"],
                charge_efficiency=device["charge_loss"],
                discharge_efficiency=device["discharge_loss"],
            )
            for cfg, device in zip(system_config.transformers, devices_info)
        ]
        policy = DistributedBESSDispatchPolicy(
            discharge_mask_mode=scheduler_config.discharge_mask_mode,
            smooth_penalty_weight=scheduler_config.smooth_penalty_weight,
            ramp_rate_fraction_per_step=scheduler_config.ramp_rate_fraction_per_step,
            charge_target_penalty_weight=scheduler_config.charge_target_penalty_weight,
            discharge_target_penalty_weight=scheduler_config.discharge_target_penalty_weight,
        )
        dispatch_input = OptimizationDistributedBESSDispatchInput(
            timestamps=mi.to_list(),
            local_load_forecast=[
                local_loads[cfg.name].loc[mi].to_list() for cfg in system_config.transformers
            ],
            system_load_forecast=system_load.loc[mi].to_list(),
            buy_price=ele_price.loc[mi, "value"].to_list(),
            price_type=ele_price.loc[mi, "type"].to_list(),
            nodes=node_params,
            initial_soc_kwh=current_soc_kwh,
            step_hours=freq_minutes / 60,
            demand_charge_rate=max_demand_price,
            grid_import_formula=scheduler_config.grid_import_formula.value,
            grid_import_nonneg=scheduler_config.grid_import_nonneg,
            demand_charge=DistributedBESSDemandChargeConfig(
                mode=scheduler_config.demand_charge_mode,
                window_minutes=scheduler_config.demand_charge_window_minutes,
            ),
            policy=policy,
            solver=scheduler_config.solver.value,
        )
        result = DistributedBESSDispatcher(dispatch_input).solve()
        objective_value += float(result.total_cost)
        current_soc_kwh = [node_soc[-1] for node_soc in result.soc_by_node]

        month_df = pd.DataFrame({"time": mi})
        power_cols = []
        for idx, cfg in enumerate(system_config.transformers):
            col = f"power_{cfg.name}"
            month_df[col] = np.asarray(result.net_bess_power_by_node[idx], dtype=float)
            month_df[f"soc_{cfg.name}"] = np.asarray(result.soc_by_node[idx], dtype=float)
            month_df[f"transformer_import_{cfg.name}"] = np.asarray(
                result.transformer_import_by_node[idx], dtype=float
            )
            month_df[f"transformer_export_{cfg.name}"] = np.asarray(
                result.transformer_export_by_node[idx], dtype=float
            )
            power_cols.append(col)
        month_df["power_total"] = month_df[power_cols].sum(axis=1)
        month_df["grid_import_total"] = np.asarray(result.grid_import_total, dtype=float)
        abs_ = result.allocation_by_source_target
        for si, scfg in enumerate(system_config.transformers):
            for ti, tcfg in enumerate(system_config.transformers):
                month_df[f"allocation_{scfg.name}_to_{tcfg.name}"] = np.asarray(
                    abs_[si][ti], dtype=float
                )
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
