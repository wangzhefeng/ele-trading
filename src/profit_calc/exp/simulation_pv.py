from datetime import datetime
from pathlib import Path
import multiprocessing as mp

import numpy as np
import pandas as pd

PV_SELL_PRICE = 0.319438

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


def with_chinese_output_columns(result_df: pd.DataFrame) -> pd.DataFrame:
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
    save_range_start,
    save_range_end,
    exp_name,
    strategy_dir,
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

    strategy_df = pd.read_csv(base_dir / "opt_result" / strategy_dir / f"schedule_result_scale_{es_scale}.csv")
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
        PV_SELL_PRICE,
        time_ratio,
    )
    opt_cost = calculate_opt_cost(
        strategy_df,
        ele_price_df,
        max_demand_price,
        PV_SELL_PRICE,
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
    save_path=None,
    show=False,
):
    """绘制指定储能容量下的负荷、光伏、电价和优化策略功率曲线。"""
    import matplotlib.pyplot as plt

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

    strategy_path = base_dir / "opt_result" / strategy_dir / f"schedule_result_scale_{es_scale}.csv"
    strategy_df = pd.read_csv(strategy_path)
    strategy_df["time"] = pd.to_datetime(strategy_df["time"])
    strategy_df.set_index("time", inplace=True)

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

    # 固定颜色与线型，避免多条功率曲线依赖 Matplotlib 默认色循环后难以区分。
    power_lines = [
        ax_power.plot(
            demand_load_df.index,
            demand_load_df["value"],
            label="demand_load(kW)",
            color="#111827",
            linewidth=1.8,
            linestyle="-",
            alpha=0.95,
        )[0],
        ax_power.plot(
            pv_load_df.index,
            pv_load_df["value"],
            label="pv_load(kW)",
            color="#f59e0b",
            linewidth=1.7,
            linestyle="-",
            alpha=0.95,
        )[0],
        ax_power.plot(
            strategy_df.index,
            strategy_df["grid_import"],
            label="grid_import(kW)",
            color="#2563eb",
            linewidth=1.8,
            linestyle="-",
            alpha=0.95,
        )[0],
        ax_power.plot(
            strategy_df.index,
            strategy_df["pv_to_load"],
            label="pv_to_load(kW)",
            color="#16a34a",
            linewidth=1.4,
            linestyle="-",
            alpha=0.9,
        )[0],
        ax_power.plot(
            strategy_df.index,
            strategy_df["pv_to_battery"],
            label="pv_to_battery(kW)",
            color="#84cc16",
            linewidth=1.5,
            linestyle="--",
            alpha=0.95,
        )[0],
        ax_power.plot(
            strategy_df.index,
            strategy_df["pv_to_grid"],
            label="pv_to_grid(kW)",
            color="#06b6d4",
            linewidth=1.5,
            linestyle=":",
            alpha=0.95,
        )[0],
        ax_power.plot(
            strategy_df.index,
            battery_power,
            label="battery_power(kW, +discharge/-charge)",
            color="#9333ea",
            linewidth=1.8,
            linestyle="-",
            alpha=0.95,
        )[0],
    ]
    ax_power.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax_power.set_ylabel("Power(kW)")
    ax_power.grid(True, alpha=0.3)

    ax_soc = ax_power.twinx()
    soc_line = ax_soc.plot(
        strategy_df.index,
        strategy_df["soc"],
        label="soc(kWh)",
        color="#0f766e",
        alpha=0.9,
        linewidth=1.8,
        linestyle="-",
    )[0]
    ax_soc.set_ylabel("SOC(kWh)")

    ax_price = ax_power.twinx()
    ax_price.spines["right"].set_position(("axes", 1.08))
    price_line = ax_price.step(
        ele_price_df.index,
        ele_price_df["value"],
        label="ele_price",
        color="#dc2626",
        alpha=0.85,
        linewidth=1.6,
        linestyle="-.",
        where="post",
    )[0]
    ax_price.set_ylabel("Price")

    lines = power_lines + [soc_line, price_line]
    labels = [line.get_label() for line in lines]
    ax_power.legend(lines, labels, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=5)
    ax_power.set_xlabel("Time")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.08, 0.94, 0.95))

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
    exp_name = "hongtaiyang"
    print("start!", exp_name)
    
    save_range_start = datetime(2025, 1, 1, 0, 0, 0)
    save_range_end = datetime(2026, 1, 1, 0, 0, 0)
    es_scale_list = list(range(0, 3750, 150))
    route_list = ["B"]
    max_demand_price = 33.8
    strategy_dir = "es_scale_experiment_optim"

    mp_input_list = [
        (x, y, max_demand_price, save_range_start, save_range_end, exp_name, strategy_dir)
        for x in es_scale_list
        for y in route_list
    ]
    with mp.Pool(processes=min(25, len(mp_input_list))) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)

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
        output_df.to_csv(f"./data/{exp_name}/{k}/opt_result/estimate_result_scale_all_optim.csv")
    
    plot_strategy_power_detail(es_scale=0, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{0}.png")
    plot_strategy_power_detail(es_scale=150, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{150}.png")
    plot_strategy_power_detail(es_scale=300, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{300}.png")
    plot_strategy_power_detail(es_scale=450, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{450}.png")
    plot_strategy_power_detail(es_scale=600, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{600}.png")
    plot_strategy_power_detail(es_scale=750, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{750}.png")
    plot_strategy_power_detail(es_scale=900, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{900}.png")
    plot_strategy_power_detail(es_scale=1050, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{1050}.png")
    plot_strategy_power_detail(es_scale=1200, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{1200}.png")
    plot_strategy_power_detail(es_scale=1350, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{1350}.png")
    plot_strategy_power_detail(es_scale=1500, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{1500}.png")
    plot_strategy_power_detail(es_scale=1650, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{1650}.png")
    plot_strategy_power_detail(es_scale=1800, date="2025-01-01", save_path=f"./data/{exp_name}/route_B/opt_result/result_{1800}.png")
