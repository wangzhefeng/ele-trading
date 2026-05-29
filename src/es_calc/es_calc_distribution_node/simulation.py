from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import (
    CABINET_CAPACITY_KWH,
    CABINET_POWER_KW,
    CONSTRAINT_TOLERANCE_KW,
    CabinetEqualityMode,
    SystemConfig,
    SYSTEMS,
)
from .optimizer import (
    calculate_system_max_cabinets,
    calculate_system_power_limit,
    cabinet_groups,
    combo_key,
    group_cabinet_count,
    group_equal_cabinet_violation_count,
    load_series,
)


OUTPUT_COLUMN_CN = {
    "system_name": "系统名称",
    "combo_key": "储能柜组合",
    "revenue": "收益",
    "max_demand_rise_cost": "需量电费变化",
    "ori_energy": "原始负荷电量",
    "ori_cost": "原始总成本",
    "opt_cost": "优化后总成本",
    "charge_energy": "储能充电电量",
    "discharge_energy": "储能放电电量",
    "charge_balance": "储能充电电费",
    "discharge_balance": "储能放电收益",
    "transformer_violation_count": "变压器容量违规次数",
    "system_power_limit_kw": "系统原始峰值负荷",
    "system_max_cabinets": "系统最大允许柜数",
    "system_cabinet_limit_violation": "系统柜数上限违规",
    "equal_cabinets_required": "要求各变压器柜数相等",
    "equal_cabinet_violation_count": "等柜数约束违规次数",
    "min_cabinets_per_transformer": "单变压器最小柜数",
    "min_required_total_cabinets": "系统最小必需柜数",
    "min_cabinet_violation_count": "最小柜数违规台数",
    "total_cabinets": "总储能柜数",
    "total_power_kw": "储能总功率",
    "total_capacity_kwh": "储能总电容量",
    "cabinet_group_rule": "储能柜分组规则",
    "group_equal_cabinet_violation_count": "分组等柜数违规次数",
}


def with_chinese_output_columns(result_df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {
        col: f"{col}_{OUTPUT_COLUMN_CN[col]}"
        for col in result_df.columns
        if col in OUTPUT_COLUMN_CN
    }
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
    # v1 专属
    system_max_cabinets: int | None = None
    system_cabinet_limit_violation: int | None = None
    # v2+
    equal_cabinets_required: bool = False
    equal_cabinet_violation_count: int = 0
    # v3+ 分组
    cabinet_group_rule: str = ""
    group_equal_cabinet_violation_count: int = 0
    # 通用
    min_cabinets_per_transformer: int = 0
    min_required_total_cabinets: int = 0
    min_cabinet_violation_count: int = 0
    total_cabinets: int = 0
    total_power_kw: float = 0.0
    total_capacity_kwh: float = 0.0


def monthly_max_cost(load: pd.Series, max_demand_price: float) -> float:
    return float(load.resample("ME").max().sum() * max_demand_price)


def parse_cabinet_counts_from_key(key: str) -> tuple[int, ...]:
    values = []
    for part in key.split("__"):
        values.append(int(part.rsplit("-", 1)[1]))
    return tuple(values)


def parse_cabinet_counts_from_schedule(schedule_df: pd.DataFrame, schedule_path: Path) -> tuple[int, ...]:
    if "combo_key" in schedule_df.columns and schedule_df["combo_key"].notna().any():
        key = str(schedule_df["combo_key"].dropna().iloc[0])
        return parse_cabinet_counts_from_key(key)
    stem = schedule_path.stem
    prefix = "schedule_result_combo_"
    if stem.startswith(prefix):
        return parse_cabinet_counts_from_key(stem[len(prefix):])
    raise ValueError("schedule file must contain combo_key or use schedule_result_combo_<combo>.csv naming.")


def load_base_data(
    base_dir: Path,
    system_config: SystemConfig,
    start_time: datetime,
    end_time: datetime,
    load_mode: str = "park_file",
):
    local_load_dfs = {
        cfg.name: pd.DataFrame({"value": load_series(base_dir / cfg.load_file, start_time, end_time)})
        for cfg in system_config.transformers
    }
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
        system_load = pd.concat([frame["value"] for frame in local_load_dfs.values()], axis=1).sum(axis=1)
    else:
        system_load = load_series(base_dir / system_config.park_load_file, start_time, end_time)
        if not system_load.index.equals(expected_index):
            raise ValueError(f"{system_config.park_load_file} time index does not match system index")

    return system_load, local_load_dfs, ele_price_df


def simulate_schedule(
    schedule_path: Path,
    base_dir: Path,
    system_config: SystemConfig,
    max_demand_price: float,
    start_time: datetime,
    end_time: datetime,
    equality_mode: CabinetEqualityMode = CabinetEqualityMode.GROUP,
    load_mode: str = "park_file",
    min_cabinets_per_transformer: int = 1,
) -> SimulationResult:
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
    system_power_limit_kw = calculate_system_power_limit(system_load)
    total_cabinets = sum(cabinet_counts)
    min_required = len(system_config.transformers) * min_cabinets_per_transformer
    min_violation = sum(int(count < min_cabinets_per_transformer) for count in cabinet_counts)

    # v1 专属
    system_max_cabinets_val = None
    system_cabinet_limit_violation_val = None
    if equality_mode == CabinetEqualityMode.NONE:
        _, system_max_cabinets_val = calculate_system_max_cabinets(system_load)
        system_cabinet_limit_violation_val = int(total_cabinets > system_max_cabinets_val)

    # v2+ 等柜
    equal_cabinets_required_val = equality_mode in (CabinetEqualityMode.GLOBAL, CabinetEqualityMode.GROUP)
    equal_violation = 0
    if equality_mode == CabinetEqualityMode.GLOBAL:
        equal_violation = int(len(set(cabinet_counts)) != 1)
    elif equality_mode == CabinetEqualityMode.GROUP:
        equal_violation = group_equal_cabinet_violation_count(cabinet_counts, system_config)

    # v3+ 分组
    group_rule = ""
    group_violation = 0
    if equality_mode == CabinetEqualityMode.GROUP:
        group_rule = "__".join("_".join(g) for g in cabinet_groups(system_config))
        group_violation = group_equal_cabinet_violation_count(cabinet_counts, system_config)

    transformer_violation_count = 0
    charge_energy = 0.0
    discharge_energy = 0.0
    charge_balance = 0.0
    discharge_balance = 0.0

    for cfg in system_config.transformers:
        power_col = f"power_{cfg.name}"
        import_col = f"transformer_import_{cfg.name}"
        export_col = f"transformer_export_{cfg.name}"
        if power_col not in schedule_df.columns:
            raise ValueError(f"{schedule_path} missing {power_col}")
        if import_col not in schedule_df.columns or export_col not in schedule_df.columns:
            raise ValueError(f"{schedule_path} missing transformer import/export columns for {cfg.name}")

        transformer_violation_count += int((schedule_df[import_col] > cfg.transformer_capacity + CONSTRAINT_TOLERANCE_KW).sum())
        transformer_violation_count += int((schedule_df[export_col] > cfg.transformer_capacity + CONSTRAINT_TOLERANCE_KW).sum())

        power = schedule_df[power_col]
        balance = power * ele_price_df["value"]
        charge_energy += float(-power[power < 0].sum())
        discharge_energy += float(power[power > 0].sum())
        charge_balance += float(-balance[balance < 0].sum())
        discharge_balance += float(balance[balance > 0].sum())

    grid_import_total = schedule_df["grid_import_total"]
    charge_energy *= dt_hours
    discharge_energy *= dt_hours
    charge_balance *= dt_hours
    discharge_balance *= dt_hours
    ori_energy = float(system_load.sum() * dt_hours)
    origin_energy_cost = float((system_load * ele_price_df["value"] * dt_hours).sum())
    opt_energy_cost = float((grid_import_total * ele_price_df["value"] * dt_hours).sum())
    ori_max_demand_cost = monthly_max_cost(system_load, max_demand_price)
    opt_max_demand_cost = monthly_max_cost(grid_import_total, max_demand_price)
    ori_cost = origin_energy_cost + ori_max_demand_cost
    opt_cost = opt_energy_cost + opt_max_demand_cost
    revenue = ori_cost - opt_cost

    return SimulationResult(
        system_name=system_config.name,
        combo_key=combo,
        revenue=revenue,
        max_demand_rise_cost=opt_max_demand_cost - ori_max_demand_cost,
        ori_energy=ori_energy,
        ori_cost=ori_cost,
        opt_cost=opt_cost,
        charge_energy=charge_energy,
        discharge_energy=discharge_energy,
        charge_balance=charge_balance,
        discharge_balance=discharge_balance,
        transformer_violation_count=transformer_violation_count,
        system_power_limit_kw=system_power_limit_kw,
        system_max_cabinets=system_max_cabinets_val,
        system_cabinet_limit_violation=system_cabinet_limit_violation_val,
        equal_cabinets_required=equal_cabinets_required_val,
        equal_cabinet_violation_count=equal_violation,
        cabinet_group_rule=group_rule,
        group_equal_cabinet_violation_count=group_violation,
        min_cabinets_per_transformer=min_cabinets_per_transformer,
        min_required_total_cabinets=min_required,
        min_cabinet_violation_count=min_violation,
        total_cabinets=total_cabinets,
        total_power_kw=total_cabinets * CABINET_POWER_KW,
        total_capacity_kwh=total_cabinets * CABINET_CAPACITY_KWH,
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
    summary_path = strategy_path / "capacity_search_summary.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"{summary_path} does not exist; combo_key must be provided")
    summary_df = pd.read_csv(summary_path)
    combo_col = _find_summary_column(summary_df, "combo_key")
    if summary_df.empty or combo_col is None:
        raise ValueError(f"{summary_path} must contain combo_key")
    selected_col = _find_summary_column(summary_df, "selected")
    if selected_col is not None:
        selected = summary_df[summary_df[selected_col].astype(str).str.lower().isin({"true", "1"})]
        if not selected.empty:
            return str(selected.iloc[0][combo_col])
    revenue_col = _find_summary_column(summary_df, "revenue")
    if revenue_col is not None:
        return str(summary_df.sort_values(revenue_col, ascending=False).iloc[0][combo_col])
    return str(summary_df.iloc[0][combo_col])


def simulate_all(
    base_dir: Path,
    strategy_dir: str,
    system_config: SystemConfig,
    max_demand_price: float,
    start_time: datetime,
    end_time: datetime,
    equality_mode: CabinetEqualityMode = CabinetEqualityMode.GROUP,
    load_mode: str = "park_file",
    min_cabinets_per_transformer: int = 1,
) -> pd.DataFrame:
    strategy_path = base_dir / "opt_result" / strategy_dir
    summary_path = strategy_path / "capacity_search_summary.csv"
    if summary_path.exists():
        summary_df = pd.read_csv(summary_path)
        combo_col = _find_summary_column(summary_df, "combo_key")
        if combo_col is None:
            raise ValueError(f"{summary_path} must contain combo_key")
        combo_keys = summary_df[combo_col].astype(str).tolist()
        schedule_files = [strategy_path / f"schedule_result_combo_{key}.csv" for key in combo_keys]
    else:
        schedule_files = sorted(strategy_path.glob("schedule_result_combo_*.csv"))
    if not schedule_files:
        raise FileNotFoundError(f"no schedule_result_combo_*.csv files found in {strategy_path}")
    missing_files = [path for path in schedule_files if not path.exists()]
    if missing_files:
        missing_text = ", ".join(str(path) for path in missing_files[:5])
        raise FileNotFoundError(f"schedule files listed by capacity_search_summary.csv are missing: {missing_text}")

    rows = []
    for schedule_file in schedule_files:
        result = simulate_schedule(
            schedule_file, base_dir, system_config, max_demand_price,
            start_time, end_time, equality_mode, load_mode, min_cabinets_per_transformer,
        )
        rows.append(result.__dict__)
    result_df = pd.DataFrame(rows).sort_values("revenue", ascending=False)

    # 重命名分组列以匹配原版输出格式
    group_rename = {}
    for col in result_df.columns:
        if col.endswith("_group_cabinets") and col not in OUTPUT_COLUMN_CN:
            prefix = col.replace("_group_cabinets", "")
            group_rename[col] = f"{prefix}_group_cabinets"
    if group_rename:
        result_df = result_df.rename(columns=group_rename)

    output_df = with_chinese_output_columns(result_df)
    output_df.to_csv(strategy_path / "simulation_summary.csv", index=False, encoding="utf-8-sig")
    return result_df
