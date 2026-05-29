from __future__ import annotations

import itertools
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from itertools import product
from pathlib import Path
from time import perf_counter
from typing import Iterable

for _thread_env_name in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_thread_env_name, "1")

import pandas as pd

from .config import (
    CABINET_CAPACITY_KWH,
    CABINET_POWER_KW,
    CONSTRAINT_TOLERANCE_KW,
    CabinetEqualityMode,
    GridImportFormula,
    SchedulerConfig,
    SystemConfig,
    TransformerConfig,
    SYSTEMS,
    TRANSFORMERS,
    PRESETS,
)
from .scheduler import EsDistributionScheduler

_FULL_GRID_WORKER_CONTEXT: dict | None = None


# ── 通用工具 ──────────────────────────────────────────────────────────────────


def generate_month_ranges(start_time: datetime, end_time: datetime):
    if start_time >= end_time:
        return []
    result = []
    current = start_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current < end_time:
        if current.month == 12:
            next_month_start = current.replace(year=current.year + 1, month=1, day=1)
        else:
            next_month_start = current.replace(month=current.month + 1)
        result.append((current, next_month_start))
        current = next_month_start
    return result


def combo_key(
    cabinet_counts: tuple[int, ...],
    transformer_configs: tuple[TransformerConfig, ...] | list[TransformerConfig] | None = None,
) -> str:
    configs = tuple(transformer_configs or TRANSFORMERS)
    return "__".join(f"{cfg.name}-{cabinet_counts[idx]}" for idx, cfg in enumerate(configs))


def load_series(path: Path, start_time: datetime, end_time: datetime) -> pd.Series:
    data = pd.read_csv(path)
    data["time"] = pd.to_datetime(data["time"])
    data["value"] = pd.to_numeric(data["value"], errors="raise")
    data = data[(data["time"] >= start_time) & (data["time"] < end_time)]
    data = data.set_index("time").sort_index()
    return data["value"]


def load_inputs(
    base_dir: Path,
    start_time: datetime,
    end_time: datetime,
    system_config: SystemConfig,
    load_mode: str = "park_file",
) -> tuple[pd.Series, dict[str, pd.Series], pd.DataFrame]:
    local_loads = {
        cfg.name: load_series(base_dir / cfg.load_file, start_time, end_time)
        for cfg in system_config.transformers
    }
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


def build_devices_info(
    cabinet_counts: tuple[int, ...],
    transformer_configs: tuple[TransformerConfig, ...] | list[TransformerConfig] | None = None,
):
    configs = tuple(transformer_configs or TRANSFORMERS)
    devices_info = []
    for idx, cfg in enumerate(configs):
        power = cabinet_counts[idx] * CABINET_POWER_KW
        capacity = cabinet_counts[idx] * CABINET_CAPACITY_KWH
        devices_info.append({
            "usable_depth": 0.90,
            "charge_loss": 0.92,
            "discharge_loss": 0.95,
            "es_charge_max": power,
            "es_charge_min": -power,
            "es_capacity_max": capacity,
            "es_capacity_min": 0.0,
            "transform_capacity": cfg.transformer_capacity,
            "cabinet_count": cabinet_counts[idx],
        })
    return devices_info


def monthly_demand_cost(load: pd.Series, max_demand_price: float) -> float:
    return float(load.resample("ME").max().sum() * max_demand_price)


def calculate_system_power_limit(system_load: pd.Series) -> float:
    return float(system_load.max())


def calculate_system_max_cabinets(system_load: pd.Series) -> tuple[float, int]:
    system_power_limit_kw = float(system_load.max())
    system_max_cabinets = int(system_power_limit_kw // CABINET_POWER_KW)
    return system_power_limit_kw, max(system_max_cabinets, 0)


# ── 分组工具 ──────────────────────────────────────────────────────────────────


def cabinet_groups(system_config: SystemConfig) -> tuple[tuple[str, ...], ...]:
    if system_config.cabinet_groups:
        return system_config.cabinet_groups
    groups: dict[str, list[str]] = {}
    for cfg in system_config.transformers:
        groups.setdefault(cfg.name.split("_", 1)[0], []).append(cfg.name)
    return tuple(tuple(names) for names in groups.values())


def cabinet_count_by_name(cabinet_counts: tuple[int, ...], system_config: SystemConfig) -> dict[str, int]:
    return {cfg.name: cabinet_counts[idx] for idx, cfg in enumerate(system_config.transformers)}


def group_equal_cabinet_violation_count(cabinet_counts: tuple[int, ...], system_config: SystemConfig) -> int:
    counts = cabinet_count_by_name(cabinet_counts, system_config)
    return sum(int(len({counts[name] for name in group}) != 1) for group in cabinet_groups(system_config))


def group_cabinet_count(cabinet_counts: tuple[int, ...], system_config: SystemConfig, group_prefix: str) -> int:
    counts = cabinet_count_by_name(cabinet_counts, system_config)
    group = next(g for g in cabinet_groups(system_config) if g[0].startswith(group_prefix))
    return counts[group[0]]


def min_required_total_cabinets(system_config: SystemConfig, min_cabinets_per_transformer: int) -> int:
    return len(system_config.transformers) * min_cabinets_per_transformer


# ── 组合可行性 ────────────────────────────────────────────────────────────────


def is_combo_feasible(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    equality_mode: CabinetEqualityMode,
    min_cabinets_per_transformer: int = 0,
    system_max_cabinets: int | None = None,
) -> bool:
    if len(cabinet_counts) != len(system_config.transformers):
        return False
    if not all(
        min_cabinets_per_transformer <= count <= cfg.max_cabinets
        for count, cfg in zip(cabinet_counts, system_config.transformers)
    ):
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


# ── 组合枚举 ──────────────────────────────────────────────────────────────────


def full_grid_candidates(
    system_config: SystemConfig,
    equality_mode: CabinetEqualityMode,
    max_cabinets_override: int | None = None,
    system_max_cabinets: int | None = None,
    min_cabinets_per_transformer: int = 0,
) -> Iterable[tuple[int, ...]]:
    if equality_mode == CabinetEqualityMode.NONE:
        ranges = []
        for cfg in system_config.transformers:
            max_count = min(cfg.max_cabinets, max_cabinets_override) if max_cabinets_override else cfg.max_cabinets
            ranges.append(range(min_cabinets_per_transformer, max_count + 1))
        for combo in itertools.product(*ranges):
            if system_max_cabinets is None or sum(combo) <= system_max_cabinets:
                yield combo

    elif equality_mode == CabinetEqualityMode.GLOBAL:
        common_max = min(cfg.max_cabinets for cfg in system_config.transformers)
        if max_cabinets_override is not None:
            common_max = min(common_max, max_cabinets_override)
        for count in range(min_cabinets_per_transformer, common_max + 1):
            yield tuple(count for _ in system_config.transformers)

    elif equality_mode == CabinetEqualityMode.GROUP:
        cfg_by_name = {cfg.name: cfg for cfg in system_config.transformers}
        group_ranges = []
        for group in cabinet_groups(system_config):
            common_max = min(cfg_by_name[name].max_cabinets for name in group)
            if max_cabinets_override is not None:
                common_max = min(common_max, max_cabinets_override)
            group_ranges.append(range(min_cabinets_per_transformer, common_max + 1))
        for group_counts in product(*group_ranges):
            count_by_name = {}
            for group, count in zip(cabinet_groups(system_config), group_counts):
                for name in group:
                    count_by_name[name] = count
            yield tuple(count_by_name[cfg.name] for cfg in system_config.transformers)


def candidate_neighbors(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    equality_mode: CabinetEqualityMode,
    min_cabinets_per_transformer: int = 0,
    system_max_cabinets: int | None = None,
) -> Iterable[tuple[int, ...]]:
    if not cabinet_counts:
        return

    if equality_mode == CabinetEqualityMode.NONE:
        for idx, cfg in enumerate(system_config.transformers):
            if cabinet_counts[idx] < cfg.max_cabinets:
                candidate = list(cabinet_counts)
                candidate[idx] += 1
                candidate_tuple = tuple(candidate)
                if is_combo_feasible(candidate_tuple, system_config, equality_mode, min_cabinets_per_transformer, system_max_cabinets):
                    yield candidate_tuple

    elif equality_mode == CabinetEqualityMode.GLOBAL:
        next_count = cabinet_counts[0] + 1
        common_max = min(cfg.max_cabinets for cfg in system_config.transformers)
        if next_count <= common_max:
            candidate_tuple = tuple(next_count for _ in system_config.transformers)
            if is_combo_feasible(candidate_tuple, system_config, equality_mode, min_cabinets_per_transformer):
                yield candidate_tuple

    elif equality_mode == CabinetEqualityMode.GROUP:
        index_by_name = {cfg.name: idx for idx, cfg in enumerate(system_config.transformers)}
        for group in cabinet_groups(system_config):
            candidate = list(cabinet_counts)
            next_count = cabinet_counts[index_by_name[group[0]]] + 1
            for name in group:
                candidate[index_by_name[name]] = next_count
            candidate_tuple = tuple(candidate)
            if is_combo_feasible(candidate_tuple, system_config, equality_mode, min_cabinets_per_transformer):
                yield candidate_tuple


def capped_max_capacity_combo(
    system_config: SystemConfig,
    equality_mode: CabinetEqualityMode,
    system_max_cabinets: int | None = None,
    min_cabinets_per_transformer: int = 0,
) -> tuple[int, ...]:
    for cfg in system_config.transformers:
        if cfg.max_cabinets < min_cabinets_per_transformer:
            raise ValueError(
                f"transformer={cfg.name} max_cabinets={cfg.max_cabinets} "
                f"is less than min_cabinets_per_transformer={min_cabinets_per_transformer}"
            )

    if equality_mode == CabinetEqualityMode.NONE:
        counts = [min_cabinets_per_transformer for _ in system_config.transformers]
        remaining = (system_max_cabinets or sum(cfg.max_cabinets for cfg in system_config.transformers)) - sum(counts)
        for idx, cfg in enumerate(system_config.transformers):
            extra = min(cfg.max_cabinets - counts[idx], remaining)
            counts[idx] += extra
            remaining -= extra
        return tuple(counts)

    if equality_mode == CabinetEqualityMode.GLOBAL:
        common_max = min(cfg.max_cabinets for cfg in system_config.transformers)
        return tuple(common_max for _ in system_config.transformers)

    # GROUP
    counts = {}
    cfg_by_name = {cfg.name: cfg for cfg in system_config.transformers}
    for group in cabinet_groups(system_config):
        common_max = min(cfg_by_name[name].max_cabinets for name in group)
        for name in group:
            counts[name] = common_max
    return tuple(counts[cfg.name] for cfg in system_config.transformers)


# ── 调度与评价 ────────────────────────────────────────────────────────────────


def zero_schedule(index: pd.DatetimeIndex, system_config: SystemConfig, cabinet_counts: tuple[int, ...]) -> pd.DataFrame:
    data = {"time": index}
    for cfg in system_config.transformers:
        data[f"power_{cfg.name}"] = 0.0
        data[f"soc_{cfg.name}"] = 0.0
    data["power_total"] = 0.0
    data["grid_import_total"] = 0.0
    for cfg in system_config.transformers:
        data[f"transformer_import_{cfg.name}"] = 0.0
        data[f"transformer_export_{cfg.name}"] = 0.0
    return pd.DataFrame(data)


def _fill_zero_schedule_load_columns(
    schedule_df: pd.DataFrame,
    local_loads: dict[str, pd.Series],
    system_config: SystemConfig,
) -> pd.DataFrame:
    schedule = schedule_df.copy()
    schedule["time"] = pd.to_datetime(schedule["time"])
    indexed = schedule.set_index("time")
    for cfg in system_config.transformers:
        indexed[f"transformer_import_{cfg.name}"] = local_loads[cfg.name].reindex(indexed.index).to_numpy()
        indexed[f"transformer_export_{cfg.name}"] = 0.0
    indexed["grid_import_total"] = sum(
        local_loads[cfg.name].reindex(indexed.index) for cfg in system_config.transformers
    )
    return indexed.reset_index()


def optimize_combo(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    system_load: pd.Series,
    local_loads: dict[str, pd.Series],
    ele_price: pd.DataFrame,
    max_demand_price: float,
    start_time: datetime,
    end_time: datetime,
    freq_minutes: int,
    scheduler_config: SchedulerConfig,
) -> tuple[pd.DataFrame, float]:
    if sum(cabinet_counts) == 0:
        schedule = zero_schedule(system_load.index, system_config, cabinet_counts)
        return _fill_zero_schedule_load_columns(schedule, local_loads, system_config), 0.0

    devices_info = build_devices_info(cabinet_counts, system_config.transformers)
    monthly_frames = []
    objective_value = 0.0
    for vs_time, ve_time in generate_month_ranges(start_time, end_time):
        month_index = system_load[(system_load.index >= vs_time) & (system_load.index < ve_time)].index
        scheduler = EsDistributionScheduler(
            month_index.to_list(),
            system_load.loc[month_index].to_list(),
            [local_loads[cfg.name].loc[month_index].to_list() for cfg in system_config.transformers],
            ele_price.loc[month_index, "value"].to_list(),
            ele_price.loc[month_index, "type"].to_list(),
            devices_info,
            [0.0] * len(system_config.transformers),
            max_demand_price,
            freq_minutes,
            config=scheduler_config,
        )
        schedule_list = scheduler.run()
        solution = scheduler.last_solution
        objective_value += float(scheduler.last_objective_value or 0.0)

        month_df = pd.DataFrame({"time": month_index})
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
        allocation_by_source = solution["allocation_by_source"]
        for source_idx, source_cfg in enumerate(system_config.transformers):
            allocation = allocation_by_source[source_idx]
            for target_idx, target_cfg in enumerate(system_config.transformers):
                month_df[f"allocation_{source_cfg.name}_to_{target_cfg.name}"] = allocation[target_idx]
        monthly_frames.append(month_df)

    return pd.concat(monthly_frames, ignore_index=True), objective_value


def evaluate_schedule(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    schedule_df: pd.DataFrame,
    objective_value: float,
    system_load: pd.Series,
    ele_price: pd.DataFrame,
    max_demand_price: float,
    system_power_limit_kw: float,
    equality_mode: CabinetEqualityMode,
    min_cabinets_per_transformer: int,
    system_max_cabinets: int | None = None,
) -> dict:
    schedule = schedule_df.copy()
    schedule["time"] = pd.to_datetime(schedule["time"])
    schedule = schedule.set_index("time").sort_index()
    dt_hours = (schedule.index[1] - schedule.index[0]).total_seconds() / 3600
    grid_import_total = schedule["grid_import_total"]

    origin_energy_cost = float((system_load * ele_price["value"] * dt_hours).sum())
    opt_energy_cost = float((grid_import_total * ele_price["value"] * dt_hours).sum())
    ori_max_demand_cost = monthly_demand_cost(system_load, max_demand_price)
    opt_max_demand_cost = monthly_demand_cost(grid_import_total, max_demand_price)
    revenue = origin_energy_cost + ori_max_demand_cost - opt_energy_cost - opt_max_demand_cost

    transformer_violation_count = 0
    for cfg in system_config.transformers:
        import_col = f"transformer_import_{cfg.name}"
        export_col = f"transformer_export_{cfg.name}"
        transformer_violation_count += int((schedule[import_col] > cfg.transformer_capacity + CONSTRAINT_TOLERANCE_KW).sum())
        transformer_violation_count += int((schedule[export_col] > cfg.transformer_capacity + CONSTRAINT_TOLERANCE_KW).sum())

    result = {
        "system_name": system_config.name,
        "combo_key": combo_key(cabinet_counts, system_config.transformers),
        "objective_value": objective_value,
        "revenue": revenue,
        "origin_energy_cost": origin_energy_cost,
        "opt_energy_cost": opt_energy_cost,
        "ori_max_demand_cost": ori_max_demand_cost,
        "opt_max_demand_cost": opt_max_demand_cost,
        "transformer_violation_count": transformer_violation_count,
        "system_power_limit_kw": system_power_limit_kw,
        "min_cabinets_per_transformer": min_cabinets_per_transformer,
        "min_required_total_cabinets": min_required_total_cabinets(system_config, min_cabinets_per_transformer),
        "min_cabinet_violation_count": sum(
            int(count < min_cabinets_per_transformer) for count in cabinet_counts
        ),
        "total_cabinets": sum(cabinet_counts),
        "total_power_kw": sum(cabinet_counts) * CABINET_POWER_KW,
        "total_capacity_kwh": sum(cabinet_counts) * CABINET_CAPACITY_KWH,
    }

    # v1 专属字段
    if equality_mode == CabinetEqualityMode.NONE and system_max_cabinets is not None:
        result["system_max_cabinets"] = system_max_cabinets
        result["system_cabinet_limit_violation"] = int(sum(cabinet_counts) > system_max_cabinets)

    # v2+ 等柜字段
    if equality_mode in (CabinetEqualityMode.GLOBAL, CabinetEqualityMode.GROUP):
        result["equal_cabinets_required"] = True
        if equality_mode == CabinetEqualityMode.GLOBAL:
            result["equal_cabinet_violation_count"] = int(len(set(cabinet_counts)) != 1)
        else:
            result["equal_cabinet_violation_count"] = group_equal_cabinet_violation_count(cabinet_counts, system_config)

    # v3+ 分组字段
    if equality_mode == CabinetEqualityMode.GROUP:
        result["cabinet_group_rule"] = "__".join(
            "_".join(g) for g in cabinet_groups(system_config)
        )
        result["group_equal_cabinet_violation_count"] = group_equal_cabinet_violation_count(cabinet_counts, system_config)
        for group in cabinet_groups(system_config):
            prefix = group[0].split("_", 1)[0]
            result[f"{prefix}_group_cabinets"] = group_cabinet_count(cabinet_counts, system_config, prefix)

    for idx, cfg in enumerate(system_config.transformers):
        count = cabinet_counts[idx]
        result[f"{cfg.name}_cabinets"] = count
        result[f"{cfg.name}_power_kw"] = count * CABINET_POWER_KW
        result[f"{cfg.name}_capacity_kwh"] = count * CABINET_CAPACITY_KWH
    return result


# ── 容量搜索 ──────────────────────────────────────────────────────────────────


def write_schedule(output_dir: Path, schedule_df: pd.DataFrame, cabinet_counts: tuple[int, ...], system_config: SystemConfig) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    schedule_df.to_csv(output_dir / f"schedule_result_combo_{combo_key(cabinet_counts, system_config.transformers)}.csv", index=False)


def _evaluate_and_write_combo(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    system_load: pd.Series,
    local_loads: dict[str, pd.Series],
    ele_price: pd.DataFrame,
    output_dir: Path,
    start_time: datetime,
    end_time: datetime,
    max_demand_price: float,
    freq_minutes: int,
    system_power_limit_kw: float,
    equality_mode: CabinetEqualityMode,
    min_cabinets_per_transformer: int,
    system_max_cabinets: int | None,
    iteration: int,
    scheduler_config: SchedulerConfig,
) -> dict:
    if not is_combo_feasible(cabinet_counts, system_config, equality_mode, min_cabinets_per_transformer, system_max_cabinets):
        raise ValueError(
            f"infeasible cabinet combo for system={system_config.name}: "
            f"{combo_key(cabinet_counts, system_config.transformers)}"
        )

    print(
        f"evaluate system={system_config.name} iteration={iteration} "
        f"combo={combo_key(cabinet_counts, system_config.transformers)}",
        flush=True,
    )
    started_at = perf_counter()
    schedule_df, objective_value = optimize_combo(
        cabinet_counts, system_config, system_load, local_loads, ele_price,
        max_demand_price, start_time, end_time, freq_minutes, scheduler_config,
    )
    metrics = evaluate_schedule(
        cabinet_counts, system_config, schedule_df, objective_value,
        system_load, ele_price, max_demand_price, system_power_limit_kw,
        equality_mode, min_cabinets_per_transformer, system_max_cabinets,
    )
    metrics["first_seen_iteration"] = iteration
    metrics["selected"] = False
    write_schedule(output_dir, schedule_df, cabinet_counts, system_config)
    print(
        f"finished system={system_config.name} "
        f"combo={combo_key(cabinet_counts, system_config.transformers)} "
        f"revenue={metrics['revenue']:.2f} sec={perf_counter() - started_at:.2f}",
        flush=True,
    )
    return metrics


def _init_full_grid_worker(
    base_dir, output_dir, start_time, end_time, max_demand_price, freq_minutes,
    system_config, load_mode, equality_mode, min_cabinets_per_transformer,
    system_max_cabinets, scheduler_config,
) -> None:
    global _FULL_GRID_WORKER_CONTEXT
    system_load, local_loads, ele_price = load_inputs(base_dir, start_time, end_time, system_config, load_mode)
    system_power_limit_kw = calculate_system_power_limit(system_load)
    _FULL_GRID_WORKER_CONTEXT = {
        "base_dir": base_dir, "output_dir": output_dir,
        "start_time": start_time, "end_time": end_time,
        "max_demand_price": max_demand_price, "freq_minutes": freq_minutes,
        "system_config": system_config, "system_load": system_load,
        "local_loads": local_loads, "ele_price": ele_price,
        "system_power_limit_kw": system_power_limit_kw,
        "equality_mode": equality_mode,
        "min_cabinets_per_transformer": min_cabinets_per_transformer,
        "system_max_cabinets": system_max_cabinets,
        "scheduler_config": scheduler_config,
    }


def _evaluate_full_grid_combo_worker(task):
    if _FULL_GRID_WORKER_CONTEXT is None:
        raise RuntimeError("full_grid worker context is not initialized")
    iteration, cabinet_counts = task
    ctx = _FULL_GRID_WORKER_CONTEXT
    return _evaluate_and_write_combo(
        cabinet_counts, ctx["system_config"], ctx["system_load"], ctx["local_loads"],
        ctx["ele_price"], ctx["output_dir"], ctx["start_time"], ctx["end_time"],
        ctx["max_demand_price"], ctx["freq_minutes"], ctx["system_power_limit_kw"],
        ctx["equality_mode"], ctx["min_cabinets_per_transformer"],
        ctx["system_max_cabinets"], iteration, ctx["scheduler_config"],
    )


def _mark_best_full_grid_result(evaluated: dict[tuple[int, ...], dict]) -> None:
    if not evaluated:
        return
    for metrics in evaluated.values():
        metrics["selected"] = False
    best_combo = max(evaluated, key=lambda combo: evaluated[combo]["revenue"])
    evaluated[best_combo]["selected"] = True


def run_capacity_search(
    base_dir: Path,
    output_dir: Path,
    start_time: datetime,
    end_time: datetime,
    max_demand_price: float,
    freq_minutes: int,
    system_name: str,
    scheduler_config: SchedulerConfig,
    equality_mode: CabinetEqualityMode,
    load_mode: str = "park_file",
    search_mode: str = "coordinate",
    workers: int = 1,
    min_cabinets_per_transformer: int = 1,
) -> pd.DataFrame:
    if system_name not in SYSTEMS:
        raise ValueError(f"unsupported system_name: {system_name}; expected one of {sorted(SYSTEMS)}")
    if workers < 1:
        raise ValueError("workers must be >= 1")

    system_config = SYSTEMS[system_name]
    system_load, local_loads, ele_price = load_inputs(base_dir, start_time, end_time, system_config, load_mode)
    system_power_limit_kw = calculate_system_power_limit(system_load)
    system_max_cabinets = None
    if equality_mode == CabinetEqualityMode.NONE:
        _, system_max_cabinets = calculate_system_max_cabinets(system_load)

    for cfg in system_config.transformers:
        if cfg.max_cabinets < min_cabinets_per_transformer:
            raise ValueError(
                f"system={system_config.name} transformer={cfg.name} max_cabinets={cfg.max_cabinets} "
                f"is less than min_cabinets_per_transformer={min_cabinets_per_transformer}"
            )

    evaluated: dict[tuple[int, ...], dict] = {}

    def evaluate(cabinet_counts: tuple[int, ...], iteration: int, selected: bool = False):
        if not is_combo_feasible(cabinet_counts, system_config, equality_mode, min_cabinets_per_transformer, system_max_cabinets):
            raise ValueError(
                f"infeasible cabinet combo for system={system_config.name}: "
                f"{combo_key(cabinet_counts, system_config.transformers)}"
            )
        if cabinet_counts not in evaluated:
            print(
                f"evaluate system={system_config.name} iteration={iteration} "
                f"combo={combo_key(cabinet_counts, system_config.transformers)}",
                flush=True,
            )
            started_at = perf_counter()
            schedule_df, objective_value = optimize_combo(
                cabinet_counts, system_config, system_load, local_loads, ele_price,
                max_demand_price, start_time, end_time, freq_minutes, scheduler_config,
            )
            metrics = evaluate_schedule(
                cabinet_counts, system_config, schedule_df, objective_value,
                system_load, ele_price, max_demand_price, system_power_limit_kw,
                equality_mode, min_cabinets_per_transformer, system_max_cabinets,
            )
            metrics["first_seen_iteration"] = iteration
            metrics["selected"] = False
            evaluated[cabinet_counts] = metrics
            write_schedule(output_dir, schedule_df, cabinet_counts, system_config)
            print(
                f"finished system={system_config.name} "
                f"combo={combo_key(cabinet_counts, system_config.transformers)} "
                f"revenue={metrics['revenue']:.2f} sec={perf_counter() - started_at:.2f}",
                flush=True,
            )
        if selected:
            evaluated[cabinet_counts]["selected"] = True
        return evaluated[cabinet_counts]

    if search_mode == "full_grid":
        combos = list(full_grid_candidates(
            system_config, equality_mode,
            system_max_cabinets=system_max_cabinets,
            min_cabinets_per_transformer=min_cabinets_per_transformer,
        ))
        if workers > 1:
            output_dir.mkdir(parents=True, exist_ok=True)
            tasks = [(idx, combo) for idx, combo in enumerate(combos)]
            with ProcessPoolExecutor(
                max_workers=workers,
                initializer=_init_full_grid_worker,
                initargs=(
                    base_dir, output_dir, start_time, end_time, max_demand_price,
                    freq_minutes, system_config, load_mode, equality_mode,
                    min_cabinets_per_transformer, system_max_cabinets, scheduler_config,
                ),
            ) as executor:
                future_to_combo = {executor.submit(_evaluate_full_grid_combo_worker, task): task[1] for task in tasks}
                for future in as_completed(future_to_combo):
                    combo = future_to_combo[future]
                    evaluated[combo] = future.result()
        else:
            for iteration, combo in enumerate(combos):
                evaluate(combo, iteration=iteration)
        _mark_best_full_grid_result(evaluated)

    elif search_mode == "max_capacity":
        current = tuple(min_cabinets_per_transformer for _ in system_config.transformers)
        evaluate(current, iteration=0, selected=True)
        max_combo = capped_max_capacity_combo(system_config, equality_mode, system_max_cabinets, min_cabinets_per_transformer)
        evaluate(max_combo, iteration=1, selected=True)

    elif search_mode == "coordinate":
        current = tuple(min_cabinets_per_transformer for _ in system_config.transformers)
        current_metrics = evaluate(current, iteration=0, selected=True)
        iteration = 1
        while True:
            candidates = []
            for candidate in candidate_neighbors(
                current, system_config, equality_mode,
                min_cabinets_per_transformer, system_max_cabinets,
            ):
                candidates.append((candidate, evaluate(candidate, iteration=iteration)))
            if not candidates:
                break
            best_candidate, best_metrics = max(candidates, key=lambda item: item[1]["revenue"])
            if best_metrics["revenue"] <= current_metrics["revenue"] + 1e-6:
                break
            current = best_candidate
            current_metrics = evaluate(current, iteration=iteration, selected=True)
            iteration += 1
    else:
        raise ValueError(f"unsupported search_mode: {search_mode}")

    summary_df = pd.DataFrame(evaluated.values()).sort_values(["revenue", "total_power_kw"], ascending=[False, True])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(output_dir / "capacity_search_summary.csv", index=False)
    return summary_df


# ── 预设驱动入口 ──────────────────────────────────────────────────────────────

_PRESET_CONFIGS = {
    "v1": {
        "systems": ["338", "342"],
        "equality": CabinetEqualityMode.NONE,
        "load_mode": "sum_local",
    },
    "v2": {
        "systems": ["338", "342"],
        "equality": CabinetEqualityMode.GLOBAL,
        "load_mode": "sum_local",
    },
    "v3": {
        "systems": ["park"],
        "equality": CabinetEqualityMode.GROUP,
        "load_mode": "park_file",
    },
    "v4": {
        "systems": ["park"],
        "equality": CabinetEqualityMode.GROUP,
        "load_mode": "park_file",
    },
    "v5": {
        "systems": ["park"],
        "equality": CabinetEqualityMode.GROUP,
        "load_mode": "park_file",
    },
}


def run_systems(
    base_dir: Path,
    opt_result_dir: Path,
    start_time: datetime,
    end_time: datetime,
    max_demand_price: float,
    freq_minutes: int,
    search_mode: str,
    system_name: str,
    workers: int,
    min_cabinets_per_transformer: int,
    preset: str = "v4",
) -> dict[str, pd.DataFrame]:
    if preset not in _PRESET_CONFIGS:
        raise ValueError(f"Unknown preset: {preset}. Choose from {list(_PRESET_CONFIGS)}")

    cfg = _PRESET_CONFIGS[preset]
    scheduler_config = PRESETS[preset]
    equality_mode = cfg["equality"]
    load_mode = cfg["load_mode"]

    selected_systems = cfg["systems"] if system_name == "all" else [system_name]
    results = {}
    for name in selected_systems:
        output_dir = opt_result_dir / f"es_scale_experiment_optim_dist_{name}-{preset}"
        results[name] = run_capacity_search(
            base_dir=base_dir,
            output_dir=output_dir,
            start_time=start_time,
            end_time=end_time,
            max_demand_price=max_demand_price,
            freq_minutes=freq_minutes,
            search_mode=search_mode,
            system_name=name,
            workers=workers,
            min_cabinets_per_transformer=min_cabinets_per_transformer,
            scheduler_config=scheduler_config,
            equality_mode=equality_mode,
            load_mode=load_mode,
        )
    return results
