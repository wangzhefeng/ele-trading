from datetime import datetime
from pathlib import Path
import multiprocessing as mp

import numpy as np
import pandas as pd


PRICE_TYPE_BACKGROUND_STYLES = {
    "低": {"facecolor": "#D9F3F7", "alpha": 0.95},
    "谷": {"facecolor": "#D9F3F7", "alpha": 0.95},
    "平": {"facecolor": "#FFF7BF", "alpha": 0.75},
    "高": {"facecolor": "#FFD9D4", "alpha": 0.75},
    "峰": {"facecolor": "#FFD9D4", "alpha": 0.75},
    "尖": {"facecolor": "#F7D6FF", "alpha": 0.9},
}
DEFAULT_PRICE_TYPE_BACKGROUND_STYLE = {"facecolor": "#E5E7EB", "alpha": 0.55}
PRICE_TYPE_BACKGROUND_LABELS = {
    "低": "price_type_low",
    "谷": "price_type_valley",
    "平": "price_type_flat",
    "高": "price_type_high",
    "峰": "price_type_peak",
    "尖": "price_type_sharp",
}


def with_chinese_output_columns(result_df: pd.DataFrame) -> pd.DataFrame:
    OUTPUT_COLUMN_CN = {
        "revenue": "收益",
        "baseline_cost": "光伏基准净成本",
        "opt_cost": "光伏储能优化净成本",
        "baseline_energy_cost": "光伏基准购电电费",
        "baseline_pv_sell_revenue": "光伏基准上网收益",
        "baseline_max_demand_cost": "光伏基准需量电费",
        "load_only_max_demand_cost": "无光伏无储能需量电费",
        "energy_cost": "优化后购电电费",
        "pv_sell_revenue": "优化后光伏上网收益",
        "max_demand_cost": "优化后需量电费",
        "max_demand_cost_delta": "需量电费变化",
        "grid_import_energy": "电网购电量",
        "grid_to_battery_energy": "电网充储电量",
        "pv_to_battery_energy": "光伏充储电量",
        "battery_discharge_energy": "储能放电量",
        "pv_to_grid_energy": "光伏上网电量",
    }
    rename_map = {
        col: f"{col}_{OUTPUT_COLUMN_CN[col]}"
        for col in result_df.columns
        if col in OUTPUT_COLUMN_CN
    }
    
    return result_df.rename(columns=rename_map)


def calculate_monthly_demand_cost(grid_import: pd.Series, max_demand_price: float) -> float:
    monthly_max = grid_import.resample("ME").max()
    return float(monthly_max.sum() * max_demand_price)


def build_pv_only_baseline(demand_load_df: pd.DataFrame, pv_load_df: pd.DataFrame) -> pd.DataFrame:
    demand = demand_load_df["value"].to_numpy(dtype=float)
    pv = pv_load_df["value"].to_numpy(dtype=float)
    pv_to_load = np.minimum(demand, pv)
    pv_to_grid = np.maximum(pv - demand, 0.0)
    grid_import = np.maximum(demand - pv, 0.0)
    return pd.DataFrame(
        {
            "pv_to_load": pv_to_load,
            "pv_to_grid": pv_to_grid,
            "grid_import": grid_import,
        },
        index=demand_load_df.index,
    )


def build_monthly_demand_power_lines(
    demand_load_df: pd.DataFrame,
    pv_load_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
) -> pd.DataFrame:
    load_only_grid = demand_load_df["value"]
    pv_only_grid = (demand_load_df["value"] - pv_load_df["value"]).clip(lower=0)
    pv_storage_grid = strategy_df["grid_import"]

    return pd.DataFrame(
        {
            "load_only_monthly_max": load_only_grid.resample("ME").max(),
            "pv_only_monthly_max": pv_only_grid.resample("ME").max(),
            "pv_storage_monthly_max": pv_storage_grid.resample("ME").max(),
        }
    )


def build_price_type_spans(ele_price_df: pd.DataFrame, default_freq_minutes: int = 15) -> list[dict]:
    if ele_price_df.empty or "type" not in ele_price_df.columns:
        return []

    price_types = ele_price_df["type"].astype(str).str.strip()
    index = ele_price_df.index
    if len(index) > 1:
        step = index.to_series().diff().dropna().median()
    else:
        step = pd.Timedelta(minutes=default_freq_minutes)
    if pd.isna(step) or step <= pd.Timedelta(0):
        step = pd.Timedelta(minutes=default_freq_minutes)

    spans = []
    current_type = price_types.iloc[0]
    start_time = index[0]
    for i in range(1, len(ele_price_df)):
        if price_types.iloc[i] != current_type:
            spans.append(
                {
                    "start": start_time,
                    "end": index[i],
                    "type": current_type,
                }
            )
            current_type = price_types.iloc[i]
            start_time = index[i]
    spans.append(
        {
            "start": start_time,
            "end": index[-1] + step,
            "type": current_type,
        }
    )

    return spans


def add_price_type_background(ax, ele_price_df: pd.DataFrame):
    from matplotlib.patches import Patch

    spans = build_price_type_spans(ele_price_df)
    legend_handles = []
    plotted_types = set()
    for span in spans:
        price_type = span["type"]
        style = PRICE_TYPE_BACKGROUND_STYLES.get(price_type, DEFAULT_PRICE_TYPE_BACKGROUND_STYLE)
        ax.axvspan(
            span["start"],
            span["end"],
            facecolor=style["facecolor"],
            alpha=style["alpha"],
            zorder=0,
        )
        if price_type not in plotted_types:
            legend_handles.append(
                Patch(
                    facecolor=style["facecolor"],
                    edgecolor="none",
                    alpha=style["alpha"],
                    label=PRICE_TYPE_BACKGROUND_LABELS.get(price_type, "price_type_other"),
                )
            )
            plotted_types.add(price_type)

    return legend_handles


def configure_matplotlib_chinese_font():
    import matplotlib as mpl
    from matplotlib import font_manager as fm

    preferred_fonts = [
        "Arial Unicode MS",
        "PingFang SC",
        "Heiti TC",
        "Songti SC",
        "SimHei",
        "Noto Sans CJK SC",
    ]
    available_fonts = {font.name for font in fm.fontManager.ttflist}
    for font_name in preferred_fonts:
        if font_name in available_fonts:
            current_fonts = mpl.rcParams.get("font.sans-serif", [])
            mpl.rcParams["font.sans-serif"] = [font_name, *current_fonts]
            mpl.rcParams["axes.unicode_minus"] = False
            return font_name

    return None


def calculate_opt_cost(
    strategy_df: pd.DataFrame,
    ele_price_df: pd.DataFrame,
    max_demand_price: float,
    pv_sell_price: float,
    time_ratio: float,
) -> dict:
    energy_cost = float((strategy_df["grid_import"] * ele_price_df["value"]).sum() * time_ratio)
    pv_sell_revenue = float(strategy_df["pv_to_grid"].sum() * pv_sell_price * time_ratio)
    max_demand_cost = calculate_monthly_demand_cost(strategy_df["grid_import"], max_demand_price)
    return {
        "energy_cost": energy_cost,
        "pv_sell_revenue": pv_sell_revenue,
        "max_demand_cost": max_demand_cost,
        "net_cost": energy_cost + max_demand_cost - pv_sell_revenue,
    }


def validate_strategy_detail(
    demand_load_df: pd.DataFrame,
    pv_load_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
    tol: float = 1e-3,
) -> None:
    demand = demand_load_df["value"].to_numpy(dtype=float)
    pv = pv_load_df["value"].to_numpy(dtype=float)

    np.testing.assert_allclose(
        strategy_df["pv_to_load"] + strategy_df["pv_to_battery"] + strategy_df["pv_to_grid"],
        pv,
        atol=tol,
    )
    np.testing.assert_allclose(
        strategy_df["pv_to_load"] + strategy_df["battery_discharge"] + strategy_df["grid_to_load"],
        demand,
        atol=tol,
    )
    np.testing.assert_allclose(
        strategy_df["grid_import"],
        strategy_df["grid_to_load"] + strategy_df["grid_to_battery"],
        atol=tol,
    )

    charge_window = strategy_df.index.map(lambda x: (0 <= x.hour < 6) or (12 <= x.hour < 14))
    discharge_window = strategy_df.index.map(lambda x: (6 <= x.hour < 12) or (16 <= x.hour < 24))
    standby_window = strategy_df.index.map(lambda x: 14 <= x.hour < 16)

    if not (strategy_df.loc[charge_window, "battery_discharge"].abs() <= tol).all():
        raise AssertionError("battery_discharge appears in charge windows")
    if not (strategy_df.loc[discharge_window, "battery_charge"].abs() <= tol).all():
        raise AssertionError("battery_charge appears in discharge windows")
    if not (strategy_df.loc[standby_window, "battery_charge"].abs() <= tol).all():
        raise AssertionError("battery_charge appears in standby window")
    if not (strategy_df.loc[standby_window, "battery_discharge"].abs() <= tol).all():
        raise AssertionError("battery_discharge appears in standby window")


def one_process(
    es_scale,
    route_num_str,
    max_demand_price,
    pv_sale_price,
    save_range_start,
    save_range_end,
    exp_name,
    strategy_dir,
    result_name,
):
    node_name = f"route_{route_num_str}"
    time_ratio = 15 / 60
    base_dir = Path(f"./data/{exp_name}/{node_name}")

    demand_load_df = pd.read_csv(base_dir / "demand_load.csv")
    demand_load_df["time"] = pd.to_datetime(demand_load_df["time"])
    demand_load_df.set_index("time", inplace=True)
    demand_load_df = demand_load_df[(demand_load_df.index >= save_range_start) & (demand_load_df.index < save_range_end)]

    ele_price_df = pd.read_csv(base_dir / "ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])
    ele_price_df.set_index("time", inplace=True)
    ele_price_df = ele_price_df[(ele_price_df.index >= save_range_start) & (ele_price_df.index < save_range_end)]

    pv_load_df = pd.read_csv(base_dir / "pv_load.csv")
    pv_load_df["time"] = pd.to_datetime(pv_load_df["time"])
    pv_load_df.set_index("time", inplace=True)
    pv_load_df = pv_load_df[(pv_load_df.index >= save_range_start) & (pv_load_df.index < save_range_end)]

    strategy_df = pd.read_csv(base_dir / result_name / strategy_dir / f"schedule_result_scale_{es_scale}.csv")
    strategy_df["time"] = pd.to_datetime(strategy_df["time"])
    strategy_df.set_index("time", inplace=True)
    strategy_df = strategy_df[(strategy_df.index >= save_range_start) & (strategy_df.index < save_range_end)]

    validate_strategy_detail(demand_load_df, pv_load_df, strategy_df)

    baseline_df = build_pv_only_baseline(demand_load_df, pv_load_df)
    load_only_max_demand_cost = calculate_monthly_demand_cost(demand_load_df["value"], max_demand_price)
    baseline_cost = calculate_opt_cost(
        baseline_df,
        ele_price_df,
        max_demand_price,
        pv_sale_price,
        time_ratio,
    )
    opt_cost = calculate_opt_cost(
        strategy_df,
        ele_price_df,
        max_demand_price,
        pv_sale_price,
        time_ratio,
    )

    revenue = baseline_cost["net_cost"] - opt_cost["net_cost"]

    return {
        "es_scale": es_scale,
        "node_name": node_name,
        "revenue": revenue,
        "baseline_cost": baseline_cost["net_cost"],
        "opt_cost": opt_cost["net_cost"],
        "baseline_energy_cost": baseline_cost["energy_cost"],
        "baseline_pv_sell_revenue": baseline_cost["pv_sell_revenue"],
        "baseline_max_demand_cost": baseline_cost["max_demand_cost"],
        "load_only_max_demand_cost": load_only_max_demand_cost,
        "energy_cost": opt_cost["energy_cost"],
        "pv_sell_revenue": opt_cost["pv_sell_revenue"],
        "max_demand_cost": opt_cost["max_demand_cost"],
        "max_demand_cost_delta": opt_cost["max_demand_cost"] - baseline_cost["max_demand_cost"],
        "grid_import_energy": float(strategy_df["grid_import"].sum() * time_ratio),
        "grid_to_battery_energy": float(strategy_df["grid_to_battery"].sum() * time_ratio),
        "pv_to_battery_energy": float(strategy_df["pv_to_battery"].sum() * time_ratio),
        "battery_discharge_energy": float(strategy_df["battery_discharge"].sum() * time_ratio),
        "pv_to_grid_energy": float(strategy_df["pv_to_grid"].sum() * time_ratio),
    }


def plot_strategy_power_detail(
    es_scale,
    route_num_str="B",
    exp_name="hongtaiyang",
    strategy_dir="es_scale_experiment_optim",
    date=None,
    start_time=None,
    end_time=None,
    show=False,
    result_name=None,
):
    """
    绘制指定储能容量下的负荷、光伏、电价和优化策略功率曲线。
    """
    import matplotlib.pyplot as plt
    configure_matplotlib_chinese_font()
    # ------------------------------
    # data
    # ------------------------------
    node_name = f"route_{route_num_str}"
    base_dir = Path(f"./data/{exp_name}/{node_name}")

    demand_load_df = pd.read_csv(base_dir / "demand_load.csv")
    demand_load_df["time"] = pd.to_datetime(demand_load_df["time"])
    demand_load_df.set_index("time", inplace=True)

    ele_price_df = pd.read_csv(base_dir / "ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])
    ele_price_df.set_index("time", inplace=True)

    pv_load_df = pd.read_csv(base_dir / "pv_load.csv")
    pv_load_df["time"] = pd.to_datetime(pv_load_df["time"])
    pv_load_df.set_index("time", inplace=True)

    strategy_path = base_dir / result_name / strategy_dir / f"schedule_result_scale_{es_scale}.csv"
    strategy_df = pd.read_csv(strategy_path)
    strategy_df["time"] = pd.to_datetime(strategy_df["time"])
    strategy_df.set_index("time", inplace=True)
    monthly_demand_power_lines = build_monthly_demand_power_lines(
        demand_load_df,
        pv_load_df,
        strategy_df,
    )

    def _date_mask(index):
        date_values = date if isinstance(date, (list, tuple, set)) else [date]
        mask = pd.Series(False, index=index)
        for date_i in date_values:
            day = pd.to_datetime(date_i).normalize()
            mask |= index.normalize() == day
        return mask.to_numpy(dtype=bool)

    if date is not None:
        date_label = date if isinstance(date, str) else ",".join(str(i) for i in (date if isinstance(date, (list, tuple, set)) else [date]))

        def _slice(df):
            return df.loc[_date_mask(df.index)]
    else:
        start_time = pd.to_datetime(start_time) if start_time is not None else strategy_df.index.min()
        end_time = pd.to_datetime(end_time) if end_time is not None else strategy_df.index.max()
        date_label = None

        def _slice(df):
            return df[(df.index >= start_time) & (df.index <= end_time)]

    demand_load_df = _slice(demand_load_df)
    ele_price_df = _slice(ele_price_df)
    pv_load_df = _slice(pv_load_df)
    strategy_df = _slice(strategy_df)

    if strategy_df.empty:
        raise ValueError("No strategy data found for the selected time range or date")

    battery_power = strategy_df["battery_discharge"] - strategy_df["battery_charge"]

    fig, ax_power = plt.subplots(1, 1, figsize=(18, 8))
    title = f"{exp_name}/{node_name} - ES {es_scale} kW Strategy Detail"
    if date_label is not None:
        title = f"{title} - Date {date_label}"
    fig.suptitle(title, fontsize=14)
    price_background_handles = add_price_type_background(ax_power, ele_price_df)

    # 固定颜色与线型，避免多条功率曲线依赖 Matplotlib 默认色循环后难以区分。
    demand_load_line = ax_power.plot(
        demand_load_df.index,
        demand_load_df["value"],
        label="demand_load(kW)",
        color="#111827",
        linewidth=2.0,
        linestyle="-",
        alpha=0.98,
        zorder=3,
    )[0]
    pv_load_line = ax_power.plot(
        pv_load_df.index,
        pv_load_df["value"],
        label="pv_load(kW)",
        color="#F59E0B",
        linewidth=1.9,
        linestyle="-",
        alpha=0.98,
        zorder=3,
    )[0]
    grid_import_line = ax_power.plot(
        strategy_df.index,
        strategy_df["grid_import"],
        label="grid_import(kW)",
        color="#0057B8",
        linewidth=2.0,
        linestyle="-",
        alpha=0.98,
        zorder=3,
    )[0]
    pv_to_load_line = ax_power.plot(
        strategy_df.index,
        strategy_df["pv_to_load"],
        label="pv_to_load(kW)",
        color="#16A34A",
        linewidth=1.7,
        linestyle="-",
        alpha=0.95,
        zorder=3,
    )[0]
    pv_to_battery_line = ax_power.plot(
        strategy_df.index,
        strategy_df["pv_to_battery"],
        label="pv_to_battery(kW)",
        color="#7C3AED",
        linewidth=1.8,
        linestyle="--",
        alpha=0.96,
        zorder=3,
    )[0]
    pv_to_grid_line = ax_power.plot(
        strategy_df.index,
        strategy_df["pv_to_grid"],
        label="pv_to_grid(kW)",
        color="#0891B2",
        linewidth=1.8,
        linestyle=":",
        alpha=0.96,
        zorder=3,
    )[0]
    battery_power_line = ax_power.plot(
        strategy_df.index,
        battery_power,
        label="battery_power(kW, +discharge/-charge)",
        color="#DC2626",
        linewidth=1.9,
        linestyle="-",
        alpha=0.98,
        zorder=3,
    )[0]
    monthly_line_styles = {
        "load_only_monthly_max": {
            "label": "load_only_monthly_max(kW)",
            "color": "#111827",
            "linestyle": "--",
            "linewidth": 1.5,
            "alpha": 0.90,
        },
        "pv_only_monthly_max": {
            "label": "pv_only_monthly_max(kW)",
            "color": "#9A3412",
            "linestyle": "--",
            "linewidth": 1.5,
            "alpha": 0.90,
        },
        "pv_storage_monthly_max": {
            "label": "pv_storage_monthly_max(kW)",
            "color": "#003B8E",
            "linestyle": "--",
            "linewidth": 1.5,
            "alpha": 0.90,
        },
    }
    monthly_lines = []
    plotted_monthly_labels = set()
    for month, month_df in strategy_df.groupby(strategy_df.index.to_period("M")):
        month_end = month.to_timestamp(how="end").normalize()
        if month_end not in monthly_demand_power_lines.index:
            continue
        x_start = month_df.index.min()
        x_end = month_df.index.max()
        for col, style in monthly_line_styles.items():
            label = style["label"] if col not in plotted_monthly_labels else "_nolegend_"
            monthly_lines.extend(
                ax_power.plot(
                    [x_start, x_end],
                    [
                        monthly_demand_power_lines.loc[month_end, col],
                        monthly_demand_power_lines.loc[month_end, col],
                    ],
                    label=label,
                    color=style["color"],
                    linestyle=style["linestyle"],
                    linewidth=style["linewidth"],
                    alpha=style["alpha"],
                    zorder=2,
                )
            )
            plotted_monthly_labels.add(col)
    ax_power.axhline(0, color="black", linewidth=0.8, alpha=0.5, zorder=2)
    ax_power.set_ylabel("Power(kW)")
    ax_power.grid(True, alpha=0.3)

    ax_soc = ax_power.twinx()
    soc_line = ax_soc.plot(
        strategy_df.index,
        strategy_df["soc"],
        label="soc(kWh)",
        color="#64748B",
        alpha=0.85,
        linewidth=1.5,
        linestyle="-",
        zorder=3,
    )[0]
    ax_soc.set_ylabel("SOC(kWh)")

    raw_data_handles = [demand_load_line, pv_load_line]
    demand_handles = [
        grid_import_line,
        *[line for line in monthly_lines if line.get_label() != "_nolegend_"],
    ]
    pv_handles = [
        pv_to_load_line,
        pv_to_battery_line,
        pv_to_grid_line,
    ]
    battery_handles = [battery_power_line, soc_line]
    legend_groups = [
        ("原始数据", raw_data_handles, (0.04, 0.02), 1),
        ("需量负荷", demand_handles, (0.18, 0.02), 2),
        ("电价", price_background_handles, (0.43, 0.02), 2),
        ("光伏发电分配", pv_handles, (0.58, 0.02), 2),
        ("电池相关负荷", battery_handles, (0.82, 0.02), 1),
    ]
    for legend_title, handles, anchor, ncol in legend_groups:
        if not handles:
            continue
        fig.legend(
            handles=handles,
            labels=[handle.get_label() for handle in handles],
            title=legend_title,
            loc="lower left",
            bbox_to_anchor=anchor,
            borderaxespad=0.0,
            frameon=True,
            ncol=ncol,
            fontsize=8,
            title_fontsize=9,
        )
    ax_power.set_xlabel("Time")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.18, 0.96, 0.95))
    
    # 绘图保存
    save_path=f"./data/{exp_name}/route_{route_num_str}/{result_name}/result_{es_scale}_{start_time}-{end_time}.png"
    if save_path is not None:
        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=160)
    
    if show:
        plt.show()
    elif save_path is not None:
        plt.close(fig)

    return save_path if save_path is not None else fig




if __name__ == "__main__":
    # ------------------------------
    # 参数
    # ------------------------------
    exp_name = "hongtaiyang"
    save_range_start = datetime(2025, 1, 1, 0, 0, 0)
    save_range_end = datetime(2026, 1, 1, 0, 0, 0)
    es_scale_list = list(range(0, 150, 10)) + list(range(150, 3750, 150))
    route_list = ["B"]
    max_demand_price = 33.8
    pv_sale_price = 0.319438
    strategy_dir = "es_scale_experiment_optim"
    result_name = "opt_result-v5"
    print("start!", exp_name)
    # ------------------------------
    # 计算
    # ------------------------------
    mp_input_list = [
        (x, y, max_demand_price, pv_sale_price, save_range_start, save_range_end, exp_name, strategy_dir, result_name)
        for x in es_scale_list
        for y in route_list
    ]
    with mp.Pool(processes=min(25, len(mp_input_list))) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)
    # ------------------------------
    # 结果解析    
    # ------------------------------
    result_df_dict = {}
    for route_i in route_list:
        route_name = f"route_{route_i}"
        result_df_dict[route_name] = pd.DataFrame(
            data=np.nan,
            index=es_scale_list,
            columns=[
                "revenue",
                "baseline_cost",
                "opt_cost",
                "baseline_energy_cost",
                "baseline_pv_sell_revenue",
                "baseline_max_demand_cost",
                "load_only_max_demand_cost",
                "energy_cost",
                "pv_sell_revenue",
                "max_demand_cost",
                "max_demand_cost_delta",
                "grid_import_energy",
                "grid_to_battery_energy",
                "pv_to_battery_energy",
                "battery_discharge_energy",
                "pv_to_grid_energy",
            ],
        )

    for result_i in mp_result_list:
        node_name = result_i["node_name"]
        scale_i = result_i["es_scale"]
        for key, value in result_i.items():
            if key not in {"node_name", "es_scale"}:
                result_df_dict[node_name].loc[scale_i, key] = value

    for k, v in result_df_dict.items():
        output_df = with_chinese_output_columns(v)
        output_df.to_csv(f"./data/{exp_name}/{k}/{result_name}/estimate_result_scale_all_optim.csv")
    # ------------------------------
    # 结果可视化
    # ------------------------------
    for s_es_scale, e_es_scale in zip(range(0, 3750, 150), range(150, 3900, 150)):
        print(s_es_scale, e_es_scale)
        month = "04"
        for es_scale in range(s_es_scale, e_es_scale, 150):
            for day in [["01", "05"], ["06", "10"], ["11", "15"], ["16", "20"], ["21", "25"], ["26", "30"]]:
                plot_s_time, plot_e_time = f"2025-{month}-{day[0]} 00:00:00", f"2025-{month}-{day[1]} 23:45:00"
                plot_strategy_power_detail(
                    es_scale=es_scale, 
                    # date="2025-01-01", 
                    start_time=plot_s_time,
                    end_time=plot_e_time,
                    result_name=result_name
                )
