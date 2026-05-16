from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from optimization_optim_dist import (
    CABINET_CAPACITY_KWH,
    CABINET_POWER_KW,
    CONSTRAINT_TOLERANCE_KW,
    SYSTEMS,
    SystemConfig,
    calculate_system_max_cabinets,
    combo_key,
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
    "min_cabinets_per_transformer": "单变压器最小柜数",
    "min_required_total_cabinets": "系统最小必需柜数",
    "min_cabinet_violation_count": "最小柜数违规台数",
    "total_cabinets": "总储能柜数",
    "system_cabinet_limit_violation": "系统柜数上限违规",
    "total_power_kw": "储能总功率",
    "total_capacity_kwh": "储能总电容量",
}


def with_chinese_output_columns(result_df: pd.DataFrame) -> pd.DataFrame:
    """只在最终 CSV 导出层追加中文字段名，内部计算继续使用英文 key。"""
    rename_map = {
        col: f"{col}_{OUTPUT_COLUMN_CN[col]}"
        for col in result_df.columns
        if col in OUTPUT_COLUMN_CN
    }
    return result_df.rename(columns=rename_map)


@dataclass(frozen=True)
class SimulationResult:
    """单个柜数组合的系统级仿真收益与容量校验结果。"""

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
    system_max_cabinets: int
    min_cabinets_per_transformer: int
    min_required_total_cabinets: int
    min_cabinet_violation_count: int
    total_cabinets: int
    system_cabinet_limit_violation: int
    total_power_kw: float
    total_capacity_kwh: float


def monthly_max_cost(load: pd.Series, max_demand_price: float) -> float:
    """按月最大负荷计算需量电费。"""

    return float(load.resample("ME").max().sum() * max_demand_price)


def parse_cabinet_counts_from_key(key: str) -> tuple[int, ...]:
    """从 schedule 文件名中的组合编码还原柜数。"""

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


def load_timeseries(path: Path, start_time: datetime, end_time: datetime) -> pd.DataFrame:
    data = pd.read_csv(path)
    data["time"] = pd.to_datetime(data["time"])
    data["value"] = pd.to_numeric(data["value"], errors="raise")
    data = data[(data["time"] >= start_time) & (data["time"] < end_time)]
    return data.set_index("time").sort_index()


def load_base_data(
    base_dir: Path,
    system_config: SystemConfig,
    start_time: datetime,
    end_time: datetime,
):
    """读取系统仿真所需的局部负荷和电价，并校验时间轴。"""

    local_load_dfs = {
        cfg.name: load_timeseries(base_dir / cfg.load_file, start_time, end_time)
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

    system_load = pd.concat([frame["value"] for frame in local_load_dfs.values()], axis=1).sum(axis=1)
    return system_load, local_load_dfs, ele_price_df


def simulate_schedule(
    schedule_path: Path,
    base_dir: Path,
    system_config: SystemConfig,
    max_demand_price: float,
    start_time: datetime,
    end_time: datetime,
    min_cabinets_per_transformer: int = 1,
) -> SimulationResult:
    """仿真一个系统级多列调度文件。

    母线共享模型已经在优化结果中给出系统总购电和各变压器 import/export；
    仿真阶段只做收益复算和容量一致性校验。
    """

    system_load, _, ele_price_df = load_base_data(base_dir, system_config, start_time, end_time)
    schedule_df = pd.read_csv(schedule_path)
    schedule_df["time"] = pd.to_datetime(schedule_df["time"])
    schedule_df = schedule_df[(schedule_df["time"] >= start_time) & (schedule_df["time"] < end_time)]
    schedule_df = schedule_df.set_index("time").sort_index()
    if not schedule_df.index.equals(system_load.index):
        raise ValueError(f"{schedule_path} time index does not match system load index")
    if "grid_import_total" not in schedule_df.columns:
        raise ValueError(f"{schedule_path} missing grid_import_total")

    cabinet_counts = parse_cabinet_counts_from_schedule(schedule_df.reset_index(), schedule_path)
    combo = combo_key(cabinet_counts, system_config.transformers)
    system_power_limit_kw, system_max_cabinets = calculate_system_max_cabinets(system_load)
    total_cabinets = sum(cabinet_counts)
    min_required_total_cabinets = len(system_config.transformers) * min_cabinets_per_transformer
    min_cabinet_violation_count = sum(
        int(count < min_cabinets_per_transformer)
        for count in cabinet_counts
    )
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
    dt_hours = (system_load.index[1] - system_load.index[0]).total_seconds() / 3600
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
        ori_energy=float(system_load.sum()),
        ori_cost=ori_cost,
        opt_cost=opt_cost,
        charge_energy=charge_energy,
        discharge_energy=discharge_energy,
        charge_balance=charge_balance,
        discharge_balance=discharge_balance,
        transformer_violation_count=transformer_violation_count,
        system_power_limit_kw=system_power_limit_kw,
        system_max_cabinets=system_max_cabinets,
        min_cabinets_per_transformer=min_cabinets_per_transformer,
        min_required_total_cabinets=min_required_total_cabinets,
        min_cabinet_violation_count=min_cabinet_violation_count,
        total_cabinets=total_cabinets,
        system_cabinet_limit_violation=int(total_cabinets > system_max_cabinets),
        total_power_kw=total_cabinets * CABINET_POWER_KW,
        total_capacity_kwh=total_cabinets * CABINET_CAPACITY_KWH,
    )


def simulate_all(
    base_dir: Path,
    strategy_dir: str,
    system_config: SystemConfig,
    max_demand_price: float,
    start_time: datetime,
    end_time: datetime,
    min_cabinets_per_transformer: int = 1,
) -> pd.DataFrame:
    """批量仿真优化阶段输出的所有柜数组合调度文件。"""

    strategy_path = base_dir / "opt_result" / strategy_dir
    summary_path = strategy_path / "capacity_search_summary.csv"
    if summary_path.exists():
        summary_df = pd.read_csv(summary_path)
        combo_keys = summary_df["combo_key"].astype(str).tolist()
        schedule_files = [
            strategy_path / f"schedule_result_combo_{key}.csv"
            for key in combo_keys
        ]
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
            schedule_file,
            base_dir,
            system_config,
            max_demand_price,
            start_time,
            end_time,
            min_cabinets_per_transformer,
        )
        rows.append(result.__dict__)
    result_df = pd.DataFrame(rows).sort_values("revenue", ascending=False)
    output_df = with_chinese_output_columns(result_df)
    output_df.to_csv(strategy_path / "simulation_summary.csv", index=False, encoding="utf-8-sig")
    return result_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate distributed ESS schedules for 338/342 systems.")
    parser.add_argument("--system", choices=["338", "342", "all"], default="342")
    parser.add_argument(
        "--min-cabinets-per-transformer",
        type=int,
        default=1,
        help="Minimum cabinet count required for each transformer in the selected system.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    exp_name = "hongtaiyang"
    node_name = "route_A"
    print("start!", exp_name, node_name, args.system)

    save_range_start = datetime(2025, 1, 1, 0, 0, 0)
    save_range_end = datetime(2026, 1, 1, 0, 0, 0)
    max_demand_price = 33.8
    base_dir = Path("data") / exp_name / node_name

    selected_systems = list(SYSTEMS) if args.system == "all" else [args.system]
    for system_name in selected_systems:
        strategy_dir = f"es_scale_experiment_optim_dist_{system_name}"
        summary = simulate_all(
            base_dir=base_dir,
            strategy_dir=strategy_dir,
            system_config=SYSTEMS[system_name],
            max_demand_price=max_demand_price,
            start_time=save_range_start,
            end_time=save_range_end,
            min_cabinets_per_transformer=args.min_cabinets_per_transformer,
        )
        print(f"system={system_name}")
        print(summary.head(10).to_string(index=False))
