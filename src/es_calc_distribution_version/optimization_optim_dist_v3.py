from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Iterable

# 多进程 full_grid 会同时启动多个优化求解器；先限制底层数值库线程数，避免进程数和线程数叠乘抢占 CPU。
for _thread_env_name in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_thread_env_name, "1")

import pandas as pd

from models.optimization.EsArbitraryRangeScheduler_withMaxDemand_optim_dist_v3 import (
    EsArbitraryRangeScheduler_withMaxDemand,
)


# ##############################
# 
# ##############################
@dataclass(frozen=True)
class TransformerConfig:
    """
    单个局部变压器的输入数据与容量边界。
    """
    name: str
    load_file: str
    transformer_capacity: float
    max_cabinets: int


@dataclass(frozen=True)
class SystemConfig:
    """
    一个可独立优化的系统，内部多台变压器连接在同一母线。
    """
    name: str
    transformers: tuple[TransformerConfig, ...]
    cabinet_groups: tuple[tuple[str, ...], ...] = ()
    park_load_file: str = "demand_load.csv"


# 单柜规格固定为 150kW/300kWh；搜索时只枚举柜数，避免出现不可落地的任意容量。
CABINET_POWER_KW = 150.0
CABINET_CAPACITY_KWH = 300.0
CONSTRAINT_TOLERANCE_KW = 1e-2

TRANSFORMERS = [
    TransformerConfig("338_1", "demand_load_338_1.csv", 2000.0, 13),
    TransformerConfig("338_2", "demand_load_338_2.csv", 1600.0, 10),
    TransformerConfig("338_3", "demand_load_338_3.csv", 1600.0, 10),
    TransformerConfig("342_1", "demand_load_342_1.csv", 1250.0, 8),
    TransformerConfig("342_2", "demand_load_342_2.csv", 1250.0, 8),
]
TRANSFORMER_BY_NAME = {cfg.name: cfg for cfg in TRANSFORMERS}
SYSTEMS = {
    "park": SystemConfig(
        "park",
        (
            TRANSFORMER_BY_NAME["338_1"],
            TRANSFORMER_BY_NAME["338_2"],
            TRANSFORMER_BY_NAME["338_3"],
            TRANSFORMER_BY_NAME["342_1"],
            TRANSFORMER_BY_NAME["342_2"],
        ),
        (
            ("338_1", "338_2", "338_3"),
            ("342_1", "342_2"),
        ),
    ),
}

_FULL_GRID_WORKER_CONTEXT: dict | None = None


def generate_month_ranges(start_time: datetime, end_time: datetime):
    """按自然月切分优化窗口，保持需量电费的月最大值语义。"""

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
    """
    把系统内变压器柜数组合编码进文件名，便于回溯调度结果。
    """
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
):
    """读取局部变压器负荷、园区总负荷和电价，并强制校验时间轴一致。"""

    local_loads = {
        cfg.name: load_series(base_dir / cfg.load_file, start_time, end_time)
        for cfg in system_config.transformers
    }
    park_load = load_series(base_dir / system_config.park_load_file, start_time, end_time)
    ele_price = pd.read_csv(base_dir / "ele_price.csv")
    ele_price["time"] = pd.to_datetime(ele_price["time"])
    ele_price["value"] = pd.to_numeric(ele_price["value"], errors="raise")
    ele_price = ele_price[(ele_price["time"] >= start_time) & (ele_price["time"] < end_time)]
    ele_price = ele_price.set_index("time").sort_index()

    expected_index = next(iter(local_loads.values())).index
    for name, series in local_loads.items():
        if not series.index.equals(expected_index):
            raise ValueError(f"{name} load time index does not match the system index")
    if not park_load.index.equals(expected_index):
        raise ValueError(f"{system_config.park_load_file} time index does not match the system index")
    if not ele_price.index.equals(expected_index):
        raise ValueError("ele_price.csv time index does not match system load index")
    if expected_index.to_series().diff().dropna().nunique() != 1:
        raise ValueError("demand time index must have a constant frequency")

    return park_load, local_loads, ele_price


def build_devices_info(
    cabinet_counts: tuple[int, ...],
    transformer_configs: tuple[TransformerConfig, ...] | list[TransformerConfig] | None = None,
):
    """把柜数转换为优化模型需要的功率、容量和局部变压器参数。"""
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


def calculate_system_power_limit(system_load: pd.Series) -> float:
    """保留系统无储能峰值负荷作为诊断字段，v2 不再把它转换为柜数上限。"""

    return float(system_load.max())


def is_combo_feasible(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    min_cabinets_per_transformer: int = 0,
) -> bool:
    """校验 v3 柜数组合：单变压器上下限 + 338/342 组内柜数相等。"""
    if len(cabinet_counts) != len(system_config.transformers):
        return False
    count_by_name = {
        cfg.name: cabinet_counts[idx]
        for idx, cfg in enumerate(system_config.transformers)
    }
    for group in cabinet_groups(system_config):
        if len({count_by_name[name] for name in group}) != 1:
            return False
    return all(
        min_cabinets_per_transformer <= count <= cfg.max_cabinets
        for count, cfg in zip(cabinet_counts, system_config.transformers)
    )


def min_required_total_cabinets(system_config: SystemConfig, min_cabinets_per_transformer: int) -> int:
    """系统内所有变压器都满足最小柜数要求时需要的总柜数。"""
    return len(system_config.transformers) * min_cabinets_per_transformer


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
    group = next(group for group in cabinet_groups(system_config) if group[0].startswith(group_prefix))
    return counts[group[0]]


def allocation_group_labels(system_config: SystemConfig) -> list[str]:
    labels_by_name = {}
    for group in cabinet_groups(system_config):
        label = group[0].split("_", 1)[0]
        for name in group:
            labels_by_name[name] = label
    return [labels_by_name[cfg.name] for cfg in system_config.transformers]


def capped_max_capacity_combo(
    system_config: SystemConfig,
    min_cabinets_per_transformer: int = 0,
) -> tuple[int, ...]:
    """
    v3 max_capacity 诊断模式使用各分组共同最大等柜数组合。
    """
    for cfg in system_config.transformers:
        if cfg.max_cabinets < min_cabinets_per_transformer:
            raise ValueError(
                f"transformer={cfg.name} max_cabinets={cfg.max_cabinets} "
                f"is less than min_cabinets_per_transformer={min_cabinets_per_transformer}"
            )
    counts = {}
    cfg_by_name = {cfg.name: cfg for cfg in system_config.transformers}
    for group in cabinet_groups(system_config):
        common_max = min(cfg_by_name[name].max_cabinets for name in group)
        for name in group:
            counts[name] = common_max
    return tuple(counts[cfg.name] for cfg in system_config.transformers)


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
    indexed["grid_import_total"] = sum(local_loads[cfg.name].reindex(indexed.index) for cfg in system_config.transformers)
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
    smooth_penalty_weight: float = 1e-4,
    ramp_rate_fraction_per_step: float | None = 0.5,
    charge_target_penalty_weight: float = 0.0,
    discharge_target_penalty_weight: float = 0.0,
) -> tuple[pd.DataFrame, float]:
    """
    对一个系统内的柜数组合求全年调度策略。

    求解按月进行：每个月内部优化 SOC 与充放电，月份之间暂不传递 SOC。
    """
    if sum(cabinet_counts) == 0:
        schedule = zero_schedule(system_load.index, system_config, cabinet_counts)
        return _fill_zero_schedule_load_columns(schedule, local_loads, system_config), 0.0

    devices_info = build_devices_info(cabinet_counts, system_config.transformers)
    monthly_frames = []
    objective_value = 0.0
    for vs_time, ve_time in generate_month_ranges(start_time, end_time):
        month_index = system_load[(system_load.index >= vs_time) & (system_load.index < ve_time)].index
        scheduler = EsArbitraryRangeScheduler_withMaxDemand(
            month_index.to_list(),
            system_load.loc[month_index].to_list(),
            [local_loads[cfg.name].loc[month_index].to_list() for cfg in system_config.transformers],
            ele_price.loc[month_index, "value"].to_list(),
            ele_price.loc[month_index, "type"].to_list(),
            devices_info,
            [0.0] * len(system_config.transformers),
            max_demand_price,
            freq_minutes,
            allocation_group_labels=allocation_group_labels(system_config),
            smooth_penalty_weight=smooth_penalty_weight,
            ramp_rate_fraction_per_step=ramp_rate_fraction_per_step,
            charge_target_penalty_weight=charge_target_penalty_weight,
            discharge_target_penalty_weight=discharge_target_penalty_weight,
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


def monthly_demand_cost(load: pd.Series, max_demand_price: float) -> float:
    return float(load.resample("ME").max().sum() * max_demand_price)


def evaluate_schedule(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    schedule_df: pd.DataFrame,
    objective_value: float,
    system_load: pd.Series,
    ele_price: pd.DataFrame,
    max_demand_price: float,
    system_power_limit_kw: float,
    min_cabinets_per_transformer: int,
) -> dict:
    """
    按系统总负荷评价一个调度结果的运行收益和容量违规次数。
    """
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
        "equal_cabinets_required": True,
        "equal_cabinet_violation_count": group_equal_cabinet_violation_count(cabinet_counts, system_config),
        "cabinet_group_rule": "338_equal__342_equal",
        "338_group_cabinets": group_cabinet_count(cabinet_counts, system_config, "338"),
        "342_group_cabinets": group_cabinet_count(cabinet_counts, system_config, "342"),
        "group_equal_cabinet_violation_count": group_equal_cabinet_violation_count(cabinet_counts, system_config),
        "min_cabinets_per_transformer": min_cabinets_per_transformer,
        "min_required_total_cabinets": min_required_total_cabinets(system_config, min_cabinets_per_transformer),
        "min_cabinet_violation_count": sum(
            int(count < min_cabinets_per_transformer)
            for count in cabinet_counts
        ),
        "total_cabinets": sum(cabinet_counts),
        "total_power_kw": sum(cabinet_counts) * CABINET_POWER_KW,
        "total_capacity_kwh": sum(cabinet_counts) * CABINET_CAPACITY_KWH,
    }
    for idx, cfg in enumerate(system_config.transformers):
        count = cabinet_counts[idx]
        result[f"{cfg.name}_cabinets"] = count
        result[f"{cfg.name}_power_kw"] = count * CABINET_POWER_KW
        result[f"{cfg.name}_capacity_kwh"] = count * CABINET_CAPACITY_KWH
    return result


def candidate_neighbors(
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    min_cabinets_per_transformer: int = 0,
) -> Iterable[tuple[int, ...]]:
    """
    v3 坐标增量搜索的邻域：每次只给 338 或 342 分组同步增加 1 台柜。
    """
    if not cabinet_counts:
        return
    index_by_name = {cfg.name: idx for idx, cfg in enumerate(system_config.transformers)}
    for group in cabinet_groups(system_config):
        candidate = list(cabinet_counts)
        next_count = cabinet_counts[index_by_name[group[0]]] + 1
        for name in group:
            candidate[index_by_name[name]] = next_count
        candidate_tuple = tuple(candidate)
        if is_combo_feasible(candidate_tuple, system_config, min_cabinets_per_transformer):
            yield candidate_tuple


def full_grid_candidates(
    system_config: SystemConfig,
    max_cabinets_override: int | None = None,
    min_cabinets_per_transformer: int = 0,
) -> Iterable[tuple[int, ...]]:
    """
    v3 全量组合枚举入口：枚举 338/342 分组内等柜、分组间独立的组合。
    """
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


def write_schedule(output_dir: Path,
                   schedule_df: pd.DataFrame,
                   cabinet_counts: tuple[int, ...],
                   system_config: SystemConfig) -> None:
    """
    调度结果写入文件
    """
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
    min_cabinets_per_transformer: int,
    iteration: int,
    smooth_penalty_weight: float,
    ramp_rate_fraction_per_step: float | None,
    charge_target_penalty_weight: float,
    discharge_target_penalty_weight: float,
) -> dict:
    """
    求解单个柜数组合、写调度文件并返回汇总指标。

    该函数同时服务串行和多进程 full_grid，保证两条路径的指标口径完全一致。
    """
    if not is_combo_feasible(cabinet_counts, system_config, min_cabinets_per_transformer):
        raise ValueError(
            f"infeasible cabinet combo for system={system_config.name}: "
            f"{combo_key(cabinet_counts, system_config.transformers)}; "
            f"min_cabinets_per_transformer={min_cabinets_per_transformer}"
        )

    print(
        f"evaluate system={system_config.name} iteration={iteration} "
        f"combo={combo_key(cabinet_counts, system_config.transformers)}",
        flush=True,
    )
    started_at = perf_counter()
    schedule_df, objective_value = optimize_combo(
        cabinet_counts,
        system_config,
        system_load,
        local_loads,
        ele_price,
        max_demand_price,
        start_time,
        end_time,
        freq_minutes,
        smooth_penalty_weight,
        ramp_rate_fraction_per_step,
        charge_target_penalty_weight,
        discharge_target_penalty_weight,
    )
    metrics = evaluate_schedule(
        cabinet_counts,
        system_config,
        schedule_df,
        objective_value,
        system_load,
        ele_price,
        max_demand_price,
        system_power_limit_kw,
        min_cabinets_per_transformer,
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
    base_dir: Path,
    output_dir: Path,
    start_time: datetime,
    end_time: datetime,
    max_demand_price: float,
    freq_minutes: int,
    system_config: SystemConfig,
    min_cabinets_per_transformer: int,
    smooth_penalty_weight: float,
    ramp_rate_fraction_per_step: float | None,
    charge_target_penalty_weight: float,
    discharge_target_penalty_weight: float,
) -> None:
    """每个 full_grid 子进程启动时读取一次只读输入数据，避免每个组合重复加载 CSV。"""
    global _FULL_GRID_WORKER_CONTEXT
    system_load, local_loads, ele_price = load_inputs(base_dir, start_time, end_time, system_config)
    system_power_limit_kw = calculate_system_power_limit(system_load)
    _FULL_GRID_WORKER_CONTEXT = {
        "base_dir": base_dir,
        "output_dir": output_dir,
        "start_time": start_time,
        "end_time": end_time,
        "max_demand_price": max_demand_price,
        "freq_minutes": freq_minutes,
        "system_config": system_config,
        "system_load": system_load,
        "local_loads": local_loads,
        "ele_price": ele_price,
        "system_power_limit_kw": system_power_limit_kw,
        "min_cabinets_per_transformer": min_cabinets_per_transformer,
        "smooth_penalty_weight": smooth_penalty_weight,
        "ramp_rate_fraction_per_step": ramp_rate_fraction_per_step,
        "charge_target_penalty_weight": charge_target_penalty_weight,
        "discharge_target_penalty_weight": discharge_target_penalty_weight,
    }


def _evaluate_full_grid_combo_worker(task: tuple[int, tuple[int, ...]]) -> dict:
    """ProcessPoolExecutor 调用的 top-level worker，避免 macOS spawn pickle 嵌套函数失败。"""
    if _FULL_GRID_WORKER_CONTEXT is None:
        raise RuntimeError("full_grid worker context is not initialized")
    iteration, cabinet_counts = task
    return _evaluate_and_write_combo(
        cabinet_counts,
        _FULL_GRID_WORKER_CONTEXT["system_config"],
        _FULL_GRID_WORKER_CONTEXT["system_load"],
        _FULL_GRID_WORKER_CONTEXT["local_loads"],
        _FULL_GRID_WORKER_CONTEXT["ele_price"],
        _FULL_GRID_WORKER_CONTEXT["output_dir"],
        _FULL_GRID_WORKER_CONTEXT["start_time"],
        _FULL_GRID_WORKER_CONTEXT["end_time"],
        _FULL_GRID_WORKER_CONTEXT["max_demand_price"],
        _FULL_GRID_WORKER_CONTEXT["freq_minutes"],
        _FULL_GRID_WORKER_CONTEXT["system_power_limit_kw"],
        _FULL_GRID_WORKER_CONTEXT["min_cabinets_per_transformer"],
        iteration,
        _FULL_GRID_WORKER_CONTEXT["smooth_penalty_weight"],
        _FULL_GRID_WORKER_CONTEXT["ramp_rate_fraction_per_step"],
        _FULL_GRID_WORKER_CONTEXT["charge_target_penalty_weight"],
        _FULL_GRID_WORKER_CONTEXT["discharge_target_penalty_weight"],
    )


def _mark_best_full_grid_result(evaluated: dict[tuple[int, ...], dict]) -> None:
    """full_grid 完成后只把全局收益最高的组合标记为 selected。"""
    if not evaluated:
        return
    for metrics in evaluated.values():
        metrics["selected"] = False
    best_combo = max(evaluated, key=lambda combo: evaluated[combo]["revenue"])
    evaluated[best_combo]["selected"] = True


def run_parallel_full_grid_search(
    combos: list[tuple[int, ...]],
    base_dir: Path,
    output_dir: Path,
    start_time: datetime,
    end_time: datetime,
    max_demand_price: float,
    freq_minutes: int,
    system_config: SystemConfig,
    workers: int,
    min_cabinets_per_transformer: int,
    smooth_penalty_weight: float,
    ramp_rate_fraction_per_step: float | None,
    charge_target_penalty_weight: float,
    discharge_target_penalty_weight: float,
) -> dict[tuple[int, ...], dict]:
    """按柜数组合并行执行 full_grid 搜索。"""
    evaluated: dict[tuple[int, ...], dict] = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = [(idx, combo) for idx, combo in enumerate(combos)]
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_full_grid_worker,
        initargs=(
            base_dir,
            output_dir,
            start_time,
            end_time,
            max_demand_price,
            freq_minutes,
            system_config,
            min_cabinets_per_transformer,
            smooth_penalty_weight,
            ramp_rate_fraction_per_step,
            charge_target_penalty_weight,
            discharge_target_penalty_weight,
        ),
    ) as executor:
        future_to_combo = {executor.submit(_evaluate_full_grid_combo_worker, task): task[1] for task in tasks}
        for future in as_completed(future_to_combo):
            combo = future_to_combo[future]
            evaluated[combo] = future.result()
    return evaluated


def run_capacity_search(
    base_dir: Path,
    output_dir: Path,
    start_time: datetime,
    end_time: datetime,
    max_demand_price: float,
    freq_minutes: int,
    search_mode: str = "coordinate",
    system_name: str = "338",
    workers: int = 1,
    min_cabinets_per_transformer: int = 1,
    smooth_penalty_weight: float = 1e-4,
    ramp_rate_fraction_per_step: float | None = 0.5,
    charge_target_penalty_weight: float = 0.0,
    discharge_target_penalty_weight: float = 0.0,
):
    """
    执行指定系统的容量搜索并写出每个候选调度与汇总表。
    """
    if system_name not in SYSTEMS:
        raise ValueError(f"unsupported system_name: {system_name}; expected one of {sorted(SYSTEMS)}")
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if min_cabinets_per_transformer < 0:
        raise ValueError("min_cabinets_per_transformer must be >= 0")
    system_config = SYSTEMS[system_name]
    system_load, local_loads, ele_price = load_inputs(base_dir, start_time, end_time, system_config)
    system_power_limit_kw = calculate_system_power_limit(system_load)
    for cfg in system_config.transformers:
        if cfg.max_cabinets < min_cabinets_per_transformer:
            raise ValueError(
                f"system={system_config.name} transformer={cfg.name} max_cabinets={cfg.max_cabinets} "
                f"is less than min_cabinets_per_transformer={min_cabinets_per_transformer}"
            )
    evaluated: dict[tuple[int, ...], dict] = {}
    schedules: dict[tuple[int, ...], pd.DataFrame] = {}
    # ------------------------------
    # 
    # ------------------------------
    def evaluate(cabinet_counts: tuple[int, ...], iteration: int, selected: bool = False):
        if not is_combo_feasible(cabinet_counts, system_config, min_cabinets_per_transformer):
            raise ValueError(
                f"infeasible cabinet combo for system={system_config.name}: "
                f"{combo_key(cabinet_counts, system_config.transformers)}; "
                f"min_cabinets_per_transformer={min_cabinets_per_transformer}"
            )
        
        if cabinet_counts not in evaluated:
            print(
                f"evaluate system={system_config.name} iteration={iteration} "
                f"combo={combo_key(cabinet_counts, system_config.transformers)}",
                flush=True,
            )
            started_at = perf_counter()
            schedule_df, objective_value = optimize_combo(
                cabinet_counts,
                system_config,
                system_load,
                local_loads,
                ele_price,
                max_demand_price,
                start_time,
                end_time,
                freq_minutes,
                smooth_penalty_weight,
                ramp_rate_fraction_per_step,
                charge_target_penalty_weight,
                discharge_target_penalty_weight,
            )
            metrics = evaluate_schedule(
                cabinet_counts,
                system_config,
                schedule_df,
                objective_value,
                system_load,
                ele_price,
                max_demand_price,
                system_power_limit_kw,
                min_cabinets_per_transformer,
            )
            metrics["first_seen_iteration"] = iteration
            metrics["selected"] = False
            evaluated[cabinet_counts] = metrics
            schedules[cabinet_counts] = schedule_df
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
    # ------------------------------
    # 
    # ------------------------------
    if search_mode == "full_grid":
        combos = list(
            full_grid_candidates(
                system_config,
                min_cabinets_per_transformer=min_cabinets_per_transformer,
            )
        )
        if workers > 1:
            evaluated = run_parallel_full_grid_search(
                combos,
                base_dir,
                output_dir,
                start_time,
                end_time,
                max_demand_price,
                freq_minutes,
                system_config,
                workers,
                min_cabinets_per_transformer,
                smooth_penalty_weight,
                ramp_rate_fraction_per_step,
                charge_target_penalty_weight,
                discharge_target_penalty_weight,
            )
        else:
            for iteration, combo in enumerate(combos):
                evaluate(combo, iteration=iteration)
        _mark_best_full_grid_result(evaluated)
    elif search_mode == "max_capacity":
        current = tuple(min_cabinets_per_transformer for _ in system_config.transformers)
        evaluate(current, iteration=0, selected=True)
        max_combo = capped_max_capacity_combo(system_config, min_cabinets_per_transformer)
        evaluate(max_combo, iteration=1, selected=True)
    elif search_mode == "coordinate":
        current = tuple(min_cabinets_per_transformer for _ in system_config.transformers)
        current_metrics = evaluate(current, iteration=0, selected=True)
        iteration = 1
        while True:
            candidates = []
            for candidate in candidate_neighbors(
                current,
                system_config,
                min_cabinets_per_transformer,
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
    # ------------------------------
    # 
    # ------------------------------
    summary_df = pd.DataFrame(evaluated.values()).sort_values(["revenue", "total_power_kw"], ascending=[False, True])
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(output_dir / "capacity_search_summary.csv", index=False)
    return summary_df


# ##############################
# run
# ##############################
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
    smooth_penalty_weight: float = 1e-4,
    ramp_rate_fraction_per_step: float | None = 0.5,
    charge_target_penalty_weight: float = 0.0,
    discharge_target_penalty_weight: float = 0.0,
) -> dict[str, pd.DataFrame]:
    selected_systems = ["park"] if system_name == "all" else [system_name]
    results = {}
    for name in selected_systems:
        output_dir = opt_result_dir / f"es_scale_experiment_optim_dist_{name}-v3"
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
            smooth_penalty_weight=smooth_penalty_weight,
            ramp_rate_fraction_per_step=ramp_rate_fraction_per_step,
            charge_target_penalty_weight=charge_target_penalty_weight,
            discharge_target_penalty_weight=discharge_target_penalty_weight,
        )
    
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Distributed ESS optimization for the park bus.")
    parser.add_argument("--system", choices=["park", "all"], default="park")
    parser.add_argument("--search-mode", choices=["max_capacity", "coordinate", "full_grid"], default="full_grid")
    parser.add_argument("--workers", type=int, default=8, help="Number of worker processes for full_grid search.")
    parser.add_argument(
        "--min-cabinets-per-transformer",
        type=int,
        default=1,
        help="Minimum cabinet count required for each transformer in the selected system.",
    )
    parser.add_argument(
        "--smooth-penalty-weight",
        type=float,
        default=1e-4,
        help="Penalty weight for total variation of per-transformer storage net power.",
    )
    parser.add_argument(
        "--ramp-rate-fraction-per-step",
        type=float,
        default=0.5,
        help="Max net-power change per step as a fraction of rated power.",
    )
    parser.add_argument(
        "--disable-ramp-constraint",
        action="store_true",
        help="Disable the hard per-step net-power ramp constraint.",
    )
    parser.add_argument(
        "--charge-target-penalty-weight",
        type=float,
        default=0.0,
        help="Soft penalty weight for charging-window-end SOC shortfall.",
    )
    parser.add_argument(
        "--discharge-target-penalty-weight",
        type=float,
        default=0.0,
        help="Soft penalty weight for discharge-window-end SOC surplus.",
    )
    
    return parser.parse_args()




if __name__ == "__main__":
    args = parse_args()
    exp_name = "hongtaiyang"
    node_name = "route_A"
    print(
        "start!",
        exp_name,
        node_name,
        args.system,
        args.search_mode,
        "workers",
        args.workers,
        "min_cabinets_per_transformer",
        args.min_cabinets_per_transformer,
        "smooth_penalty_weight",
        args.smooth_penalty_weight,
        "ramp_rate_fraction_per_step",
        None if args.disable_ramp_constraint else args.ramp_rate_fraction_per_step,
        "charge_target_penalty_weight",
        args.charge_target_penalty_weight,
        "discharge_target_penalty_weight",
        args.discharge_target_penalty_weight,
    )

    start_time = datetime(2025, 1, 1, 0, 0, 0)
    end_time = datetime(2026, 1, 1, 0, 0, 0)
    freq_minutes = 15
    max_demand_price = 33.8

    base_dir = Path("data") / exp_name / node_name
    result_map = run_systems(
        base_dir=base_dir,
        opt_result_dir=base_dir / "opt_result",
        start_time=start_time,
        end_time=end_time,
        max_demand_price=max_demand_price,
        freq_minutes=freq_minutes,
        search_mode=args.search_mode,
        system_name=args.system,
        workers=args.workers,
        min_cabinets_per_transformer=args.min_cabinets_per_transformer,
        smooth_penalty_weight=args.smooth_penalty_weight,
        ramp_rate_fraction_per_step=None if args.disable_ramp_constraint else args.ramp_rate_fraction_per_step,
        charge_target_penalty_weight=args.charge_target_penalty_weight,
        discharge_target_penalty_weight=args.discharge_target_penalty_weight,
    )
    for name, result in result_map.items():
        print(f"system={name}")
        print(result.head(10).to_string(index=False))
