from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.es_calc.es_calc_distribution_version.optimization_optim_dist_v3 import (
    CABINET_CAPACITY_KWH,
    CABINET_POWER_KW,
    CONSTRAINT_TOLERANCE_KW,
    SYSTEMS,
    SystemConfig,
    calculate_system_power_limit,
    combo_key,
    group_cabinet_count,
    group_equal_cabinet_violation_count,
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
    "equal_cabinets_required": "要求各变压器柜数相等",
    "equal_cabinet_violation_count": "等柜数约束违规次数",
    "min_cabinets_per_transformer": "单变压器最小柜数",
    "min_required_total_cabinets": "系统最小必需柜数",
    "min_cabinet_violation_count": "最小柜数违规台数",
    "total_cabinets": "总储能柜数",
    "total_power_kw": "储能总功率",
    "total_capacity_kwh": "储能总电容量",
    "cabinet_group_rule": "储能柜分组规则",
    "338_group_cabinets": "338分组柜数",
    "342_group_cabinets": "342分组柜数",
    "group_equal_cabinet_violation_count": "分组等柜数违规次数",
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
    equal_cabinets_required: bool
    equal_cabinet_violation_count: int
    min_cabinets_per_transformer: int
    min_required_total_cabinets: int
    min_cabinet_violation_count: int
    total_cabinets: int
    total_power_kw: float
    total_capacity_kwh: float
    cabinet_group_rule: str
    group_equal_cabinet_violation_count: int
    group_338_cabinets: int
    group_342_cabinets: int


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
    """读取系统仿真所需的局部负荷、园区总负荷和电价，并校验时间轴。"""

    local_load_dfs = {
        cfg.name: load_timeseries(base_dir / cfg.load_file, start_time, end_time)
        for cfg in system_config.transformers
    }
    park_load_df = load_timeseries(base_dir / system_config.park_load_file, start_time, end_time)
    ele_price_df = pd.read_csv(base_dir / "ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])
    ele_price_df["value"] = pd.to_numeric(ele_price_df["value"], errors="raise")
    ele_price_df = ele_price_df[(ele_price_df["time"] >= start_time) & (ele_price_df["time"] < end_time)]
    ele_price_df = ele_price_df.set_index("time").sort_index()

    expected_index = next(iter(local_load_dfs.values())).index
    for name, frame in local_load_dfs.items():
        if not frame.index.equals(expected_index):
            raise ValueError(f"{name} load time index does not match the system index")
    if not park_load_df.index.equals(expected_index):
        raise ValueError(f"{system_config.park_load_file} time index does not match system load index")
    if not ele_price_df.index.equals(expected_index):
        raise ValueError("ele_price.csv time index does not match system load index")

    return park_load_df["value"], local_load_dfs, ele_price_df


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
    dt_hours = (system_load.index[1] - system_load.index[0]).total_seconds() / 3600

    cabinet_counts = parse_cabinet_counts_from_schedule(schedule_df.reset_index(), schedule_path)
    combo = combo_key(cabinet_counts, system_config.transformers)
    system_power_limit_kw = calculate_system_power_limit(system_load)
    total_cabinets = sum(cabinet_counts)
    min_required_total_cabinets = len(system_config.transformers) * min_cabinets_per_transformer
    min_cabinet_violation_count = sum(
        int(count < min_cabinets_per_transformer)
        for count in cabinet_counts
    )
    group_violation_count = group_equal_cabinet_violation_count(cabinet_counts, system_config)
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
        equal_cabinets_required=True,
        equal_cabinet_violation_count=group_violation_count,
        min_cabinets_per_transformer=min_cabinets_per_transformer,
        min_required_total_cabinets=min_required_total_cabinets,
        min_cabinet_violation_count=min_cabinet_violation_count,
        total_cabinets=total_cabinets,
        total_power_kw=total_cabinets * CABINET_POWER_KW,
        total_capacity_kwh=total_cabinets * CABINET_CAPACITY_KWH,
        cabinet_group_rule="338_equal__342_equal",
        group_equal_cabinet_violation_count=group_violation_count,
        group_338_cabinets=group_cabinet_count(cabinet_counts, system_config, "338"),
        group_342_cabinets=group_cabinet_count(cabinet_counts, system_config, "342"),
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
        combo_col = _find_summary_column(summary_df, "combo_key")
        if combo_col is None:
            raise ValueError(f"{summary_path} must contain combo_key")
        combo_keys = summary_df[combo_col].astype(str).tolist()
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
    result_df = pd.DataFrame(rows).rename(
        columns={
            "group_338_cabinets": "338_group_cabinets",
            "group_342_cabinets": "342_group_cabinets",
        }
    ).sort_values("revenue", ascending=False)
    output_df = with_chinese_output_columns(result_df)
    output_df.to_csv(strategy_path / "simulation_summary.csv", index=False, encoding="utf-8-sig")
    return result_df


def _combo_keys_from_strategy(strategy_path: Path) -> list[str]:
    """按优化 summary 顺序读取 combo；无 summary 时回退扫描 schedule 文件。"""

    summary_path = strategy_path / "capacity_search_summary.csv"
    if summary_path.exists():
        summary_df = pd.read_csv(summary_path)
        combo_col = _find_summary_column(summary_df, "combo_key")
        if combo_col is None:
            raise ValueError(f"{summary_path} must contain combo_key")
        return summary_df[combo_col].astype(str).tolist()

    prefix = "schedule_result_combo_"
    combo_keys = []
    for schedule_file in sorted(strategy_path.glob(f"{prefix}*.csv")):
        combo_keys.append(schedule_file.stem[len(prefix):])
    if not combo_keys:
        raise FileNotFoundError(f"no schedule_result_combo_*.csv files found in {strategy_path}")
    return combo_keys


def _default_plot_windows(month: str = "06") -> list[tuple[str, str]]:
    return [
        (f"2025-{month}-01 00:00:00", f"2025-{month}-05 23:45:00"),
        (f"2025-{month}-06 00:00:00", f"2025-{month}-10 23:45:00"),
        (f"2025-{month}-11 00:00:00", f"2025-{month}-15 23:45:00"),
        (f"2025-{month}-16 00:00:00", f"2025-{month}-20 23:45:00"),
        (f"2025-{month}-21 00:00:00", f"2025-{month}-25 23:45:00"),
        (f"2025-{month}-26 00:00:00", f"2025-{month}-30 23:45:00"),
    ]


def run_simulation_and_plots(
    base_dir: Path,
    system_name: str,
    max_demand_price: float,
    start_time: datetime,
    end_time: datetime,
    min_cabinets_per_transformer: int,
) -> dict[str, pd.DataFrame]:
    selected_systems = ["park"] if system_name == "all" else [system_name]
    results = {}
    for name in selected_systems:
        strategy_dir = f"es_scale_experiment_optim_dist_{name}-v3"
        strategy_path = base_dir / "opt_result" / strategy_dir
        summary = simulate_all(
            base_dir=base_dir,
            strategy_dir=strategy_dir,
            system_config=SYSTEMS[name],
            max_demand_price=max_demand_price,
            start_time=start_time,
            end_time=end_time,
            min_cabinets_per_transformer=min_cabinets_per_transformer,
        )
        results[name] = summary
        print(f"system={name}")
        print(summary.head(10).to_string(index=False))

        combo_keys = _combo_keys_from_strategy(strategy_path)
        for combo_key in combo_keys:
            for plot_s_time, plot_e_time in _default_plot_windows("06"):
                plot_strategy_power_detail(
                    system_name=name,
                    combo_key_value=combo_key,
                    strategy_dir=strategy_dir,
                    start_time=plot_s_time,
                    end_time=plot_e_time,
                )
    return results


def _select_combo_key(strategy_path: Path, combo_key_value: str | None) -> str:
    """未显式指定组合时，优先选择优化 summary 中 selected=True 的组合。"""

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


def _find_summary_column(summary_df: pd.DataFrame, english_key: str) -> str | None:
    """兼容纯英文表头和 english_中文说明 表头。"""

    if english_key in summary_df.columns:
        return english_key
    prefix = f"{english_key}_"
    for col in summary_df.columns:
        if col.startswith(prefix):
            return col
    return None


def _slice_by_date_or_range(
    frame: pd.DataFrame,
    date: str | list[str] | tuple[str, ...] | set[str] | None,
    start_time: str | datetime | None,
    end_time: str | datetime | None,
) -> pd.DataFrame:
    """支持按单日/多日或任意时间段截取绘图数据。"""

    if date is not None:
        date_values = date if isinstance(date, (list, tuple, set)) else [date]
        mask = pd.Series(False, index=frame.index)
        for date_i in date_values:
            day = pd.to_datetime(date_i).normalize()
            mask |= frame.index.normalize() == day
        return frame.loc[mask.to_numpy(dtype=bool)]

    start = pd.to_datetime(start_time) if start_time is not None else frame.index.min()
    end = pd.to_datetime(end_time) if end_time is not None else frame.index.max()
    return frame[(frame.index >= start) & (frame.index <= end)]


def _safe_plot_filename(value: str) -> str:
    return (
        str(value)
        .replace(":", "")
        .replace(" ", "_")
        .replace("/", "-")
        .replace("\\", "-")
    )


def _build_soc_frame(
    schedule_df: pd.DataFrame,
    cabinet_counts: tuple[int, ...],
    system_config: SystemConfig,
    freq_minutes: int = 15,
) -> pd.DataFrame:
    """读取或重构 SOC；新 schedule 优先使用优化器直接输出的 soc_<transformer> 列。"""

    soc_cols = [f"soc_{cfg.name}" for cfg in system_config.transformers]
    if all(col in schedule_df.columns for col in soc_cols):
        soc_df = schedule_df[soc_cols].copy()
        soc_df["soc_total"] = soc_df.sum(axis=1)
        return soc_df

    dt_hours = freq_minutes / 60
    charge_eff = 0.92
    discharge_eff = 0.95
    soc_data = {}
    for idx, cfg in enumerate(system_config.transformers):
        power = schedule_df[f"power_{cfg.name}"].astype(float)
        charge_power = (-power.clip(upper=0.0))
        discharge_power = power.clip(lower=0.0)
        delta = charge_power * charge_eff * dt_hours - discharge_power / discharge_eff * dt_hours
        usable_capacity = cabinet_counts[idx] * CABINET_CAPACITY_KWH * 0.90
        soc = delta.groupby(delta.index.to_period("M")).cumsum().clip(lower=0.0, upper=usable_capacity)
        soc_data[f"soc_{cfg.name}"] = soc

    soc_df = pd.DataFrame(soc_data, index=schedule_df.index)
    soc_df["soc_total"] = soc_df.sum(axis=1)
    return soc_df


def plot_strategy_power_detail(
    system_name: str = "338",
    combo_key_value: str | None = None,
    exp_name: str = "hongtaiyang",
    node_name: str = "route_A",
    strategy_dir: str | None = None,
    date: str | list[str] | tuple[str, ...] | set[str] | None = None,
    start_time: str | datetime | None = None,
    end_time: str | datetime | None = None,
    show: bool = False,
    result_name: str = "opt_result",
):
    """绘制 dist v3 模型的园区负荷、储能出力、电价背景和月最大需量线。"""

    import matplotlib.pyplot as plt
    from simulation_pv import add_price_type_background, configure_matplotlib_chinese_font

    configure_matplotlib_chinese_font()
    if system_name not in SYSTEMS:
        raise ValueError(f"unsupported system_name: {system_name}; expected one of {sorted(SYSTEMS)}")

    system_config = SYSTEMS[system_name]
    base_dir = Path("data") / exp_name / node_name
    strategy_name = strategy_dir or f"es_scale_experiment_optim_dist_{system_name}-v3"
    strategy_path = base_dir / result_name / strategy_name
    selected_combo_key = _select_combo_key(strategy_path, combo_key_value)
    schedule_path = strategy_path / f"schedule_result_combo_{selected_combo_key}.csv"
    if not schedule_path.exists():
        raise FileNotFoundError(schedule_path)

    schedule_df = pd.read_csv(schedule_path)
    schedule_df["time"] = pd.to_datetime(schedule_df["time"])
    schedule_df = schedule_df.set_index("time").sort_index()
    cabinet_counts = parse_cabinet_counts_from_key(selected_combo_key)
    full_soc_df = _build_soc_frame(schedule_df, cabinet_counts, system_config)
    full_schedule_df = schedule_df.copy()
    if len(schedule_df.index) > 1:
        step = schedule_df.index.to_series().diff().dropna().median()
    else:
        step = pd.Timedelta(minutes=15)
    system_load, local_load_dfs, ele_price_df = load_base_data(
        base_dir,
        system_config,
        schedule_df.index.min().to_pydatetime(),
        (schedule_df.index.max() + step).to_pydatetime(),
    )

    system_load_df = pd.DataFrame({"value": system_load})
    system_load_df = _slice_by_date_or_range(system_load_df, date, start_time, end_time)
    ele_price_df = _slice_by_date_or_range(ele_price_df, date, start_time, end_time)
    schedule_df = _slice_by_date_or_range(schedule_df, date, start_time, end_time)
    soc_df = _slice_by_date_or_range(full_soc_df, date, start_time, end_time)
    local_load_dfs = {
        name: _slice_by_date_or_range(frame, date, start_time, end_time)
        for name, frame in local_load_dfs.items()
    }
    if schedule_df.empty:
        raise ValueError("No strategy data found for the selected time range or date")

    monthly_lines = pd.DataFrame(
        {
            "system_load_monthly_max": system_load.resample("ME").max(),
            "grid_import_monthly_max": full_schedule_df["grid_import_total"].resample("ME").max(),
        }
    )

    transformer_count = len(system_config.transformers)
    fig, axes = plt.subplots(
        transformer_count + 1,
        1,
        figsize=(18, 7 + 4 * transformer_count),
        sharex=True,
    )
    axes = list(axes)
    ax_power = axes[0]
    label_suffix = f"Date {date}" if date is not None else f"{schedule_df.index.min()} ~ {schedule_df.index.max()}"
    fig.suptitle(
        f"{exp_name}/{node_name}/{system_name} - dist v3 strategy {selected_combo_key} - {label_suffix}",
        fontsize=14,
    )
    price_background_handles = add_price_type_background(ax_power, ele_price_df)

    system_load_line = ax_power.plot(
        system_load_df.index,
        system_load_df["value"],
        label="system_load(kW)",
        color="#111827",
        linewidth=2.0,
        zorder=3,
    )[0]
    grid_import_line = ax_power.plot(
        schedule_df.index,
        schedule_df["grid_import_total"],
        label="grid_import_total(kW)",
        color="#0057B8",
        linewidth=2.0,
        zorder=3,
    )[0]
    power_total_line = ax_power.plot(
        schedule_df.index,
        schedule_df["power_total"],
        label="storage_power_total(kW, +discharge/-charge)",
        color="#DC2626",
        linewidth=1.9,
        zorder=3,
    )[0]
    ax_soc = ax_power.twinx()
    soc_total_line = ax_soc.plot(
        soc_df.index,
        soc_df["soc_total"],
        label="soc_total(kWh)",
        color="#64748B",
        linewidth=1.6,
        alpha=0.9,
        zorder=3,
    )[0]
    ax_soc.set_ylabel("SOC(kWh)")

    local_load_lines = []
    storage_power_lines = []
    grid_buy_lines = []
    cross_storage_in_lines = []
    cross_storage_out_lines = []
    transformer_after_storage_lines = []
    color_cycle = ["#166534", "#581C87", "#0E7490", "#92400E", "#9D174D"]
    for idx, cfg in enumerate(system_config.transformers):
        color = color_cycle[idx % len(color_cycle)]
        local_load_lines.append(
            ax_power.plot(
                local_load_dfs[cfg.name].index,
                local_load_dfs[cfg.name]["value"],
                label=f"load_{cfg.name}(kW)",
                color=color,
                linewidth=1.8,
                alpha=0.85,
                zorder=3,
            )[0]
        )
        storage_power_lines.append(
            ax_power.plot(
                schedule_df.index,
                schedule_df[f"power_{cfg.name}"],
                label=f"storage_power_{cfg.name}(kW)",
                color=color,
                linewidth=1.4,
                linestyle="--",
                alpha=0.75,
                zorder=3,
            )[0]
        )

    monthly_handles = []
    plotted_monthly_labels = set()
    monthly_styles = {
        "system_load_monthly_max": {
            "label": "system_load_monthly_max(kW)",
            "color": "#111827",
            "linestyle": ":",
        },
        "grid_import_monthly_max": {
            "label": "grid_import_monthly_max(kW)",
            "color": "#003B8E",
            "linestyle": ":",
        },
    }

    def _plot_monthly_lines(target_ax, include_legend: bool = False):
        handles = []
        for month, month_df in schedule_df.groupby(schedule_df.index.to_period("M")):
            month_end = month.to_timestamp(how="end").normalize()
            if month_end not in monthly_lines.index:
                continue
            for col, style in monthly_styles.items():
                label = style["label"] if include_legend and col not in plotted_monthly_labels else "_nolegend_"
                handles.extend(
                    target_ax.plot(
                        [month_df.index.min(), month_df.index.max()],
                        [monthly_lines.loc[month_end, col], monthly_lines.loc[month_end, col]],
                        label=label,
                        color=style["color"],
                        linestyle=style["linestyle"],
                        linewidth=1.5,
                        alpha=0.9,
                        zorder=2,
                    )
                )
                if include_legend:
                    plotted_monthly_labels.add(col)
        return handles

    monthly_handles = _plot_monthly_lines(ax_power, include_legend=True)

    ax_power.axhline(0, color="black", linewidth=0.8, alpha=0.5, zorder=2)
    ax_power.set_ylabel("Power(kW)")
    ax_power.grid(True, alpha=0.3)

    for idx, cfg in enumerate(system_config.transformers):
        transformer_ax = axes[idx + 1]
        add_price_type_background(transformer_ax, ele_price_df)
        transformer_soc_ax = transformer_ax.twinx()
        color = color_cycle[idx % len(color_cycle)]
        local_load = local_load_dfs[cfg.name]["value"].reindex(schedule_df.index)
        storage_power = schedule_df[f"power_{cfg.name}"]
        charge_power = (-storage_power).clip(lower=0.0)
        cross_storage_in = pd.Series(0.0, index=schedule_df.index)
        storage_to_local_load = pd.Series(0.0, index=schedule_df.index)
        for other_cfg in system_config.transformers:
            allocation_col = f"allocation_{other_cfg.name}_to_{cfg.name}"
            if allocation_col in schedule_df.columns:
                storage_to_local_load = storage_to_local_load + schedule_df[allocation_col]
                if other_cfg.name != cfg.name:
                    cross_storage_in = cross_storage_in + schedule_df[allocation_col]
        cross_storage_out = schedule_df.get(
            f"transformer_export_{cfg.name}",
            pd.Series(0.0, index=schedule_df.index),
        )
        grid_buy = (local_load - storage_to_local_load + charge_power).clip(lower=0.0)
        transformer_after_storage = grid_buy + cross_storage_in
        transformer_ax.plot(
            local_load_dfs[cfg.name].index,
            local_load_dfs[cfg.name]["value"],
            label=f"load_{cfg.name}(kW)",
            color=color,
            linewidth=1.9,
            alpha=0.9,
            zorder=4,
        )
        grid_buy_lines.append(
            transformer_ax.plot(
                schedule_df.index,
                grid_buy,
                label=f"grid_buy_{cfg.name}(kW)",
                color="#1D4ED8",
                linewidth=1.5,
                alpha=0.85,
                zorder=4,
            )[0]
        )
        cross_storage_in_lines.append(
            transformer_ax.plot(
                schedule_df.index,
                cross_storage_in,
                label=f"cross_storage_in_{cfg.name}(kW)",
                color="#B45309",
                linewidth=1.5,
                linestyle="-.",
                alpha=0.85,
                zorder=4,
            )[0]
        )
        cross_storage_out_lines.append(
            transformer_ax.plot(
                schedule_df.index,
                cross_storage_out,
                label=f"cross_storage_out_{cfg.name}(kW)",
                color="#A21CAF",
                linewidth=1.5,
                linestyle=":",
                alpha=0.85,
                zorder=4,
            )[0]
        )
        transformer_after_storage_lines.append(
            transformer_ax.plot(
                schedule_df.index,
                transformer_after_storage,
                label=f"transformer_power_after_storage_{cfg.name}(kW)",
                color="#0F766E",
                linewidth=1.8,
                alpha=0.85,
                zorder=4,
            )[0]
        )
        transformer_ax.plot(
            schedule_df.index,
            schedule_df[f"power_{cfg.name}"],
            label=f"storage_power_{cfg.name}(kW)",
            color=color,
            linewidth=1.5,
            linestyle="--",
            alpha=0.8,
            zorder=4,
        )
        transformer_soc_ax.plot(
            soc_df.index,
            soc_df[f"soc_{cfg.name}"],
            label=f"soc_{cfg.name}(kWh)",
            color="#64748B",
            linewidth=1.4,
            alpha=0.85,
            zorder=3,
        )
        transformer_ax.axhline(0, color="black", linewidth=0.8, alpha=0.45, zorder=2)
        transformer_ax.set_ylabel(f"{cfg.name}\nPower(kW)")
        transformer_soc_ax.set_ylabel("SOC(kWh)")
        transformer_ax.grid(True, alpha=0.3)

    def _short_legend_label(handle):
        label = handle.get_label()
        return (
            label.replace("(kW, +discharge/-charge)", "")
            .replace("(kWh)", "")
            .replace("(kW)", "")
            .replace("storage_power_total", "storage_total")
            .replace("storage_power_", "storage_")
            .replace("grid_import_total", "grid_total")
            .replace("system_load_monthly_max", "load_monthly_max")
            .replace("grid_import_monthly_max", "grid_monthly_max")
            .replace("transformer_power_after_storage_", "after_storage_")
            .replace("cross_storage_in_", "cross_in_")
            .replace("cross_storage_out_", "cross_out_")
            .replace("grid_buy_", "grid_buy_")
        )

    legend_groups = [
        (
            "变压器侧",
            [*grid_buy_lines, *cross_storage_in_lines, *cross_storage_out_lines, *transformer_after_storage_lines],
            (0.28, 0.095),
            6,
        ),
        ("优化结果", [grid_import_line, power_total_line, *storage_power_lines], (0.62, 0.095), 5),
        ("系统负荷", [system_load_line, *local_load_lines], (0.14, 0.025), 4),
        ("月最大需量", [line for line in monthly_handles if line.get_label() != "_nolegend_"], (0.36, 0.025), 2),
        ("电池 SOC", [soc_total_line], (0.52, 0.025), 1),
        ("电价", price_background_handles, (0.72, 0.025), 4),
    ]
    for legend_title, handles, anchor, ncol in legend_groups:
        if not handles:
            continue
        fig.legend(
            handles=handles,
            labels=[_short_legend_label(handle) for handle in handles],
            title=legend_title,
            loc="lower center",
            bbox_to_anchor=anchor,
            borderaxespad=0.0,
            frameon=True,
            ncol=ncol,
            fontsize=6.8,
            title_fontsize=7.5,
            columnspacing=0.8,
            handlelength=1.7,
            handletextpad=0.35,
        )
    for ax in axes:
        ax.tick_params(axis="x", labelrotation=0)
    axes[-1].set_xlabel("Time")
    fig.tight_layout(rect=(0, 0.165, 0.98, 0.95))

    if date is not None:
        range_label = _safe_plot_filename(date if isinstance(date, str) else "_".join(str(i) for i in date))
    else:
        range_label = f"{_safe_plot_filename(schedule_df.index.min())}-{_safe_plot_filename(schedule_df.index.max())}"
    save_path = strategy_path / f"strategy_power_detail_{selected_combo_key}_{range_label}.png"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(save_path, dpi=160)

    if show:
        plt.show()
    else:
        plt.close(fig)
    return save_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate distributed ESS schedules for the park bus.")
    parser.add_argument("--system", choices=["park", "all"], default="park")
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
    run_simulation_and_plots(
        base_dir=base_dir,
        system_name=args.system,
        max_demand_price=max_demand_price,
        start_time=save_range_start,
        end_time=save_range_end,
        min_cabinets_per_transformer=args.min_cabinets_per_transformer,
    )
