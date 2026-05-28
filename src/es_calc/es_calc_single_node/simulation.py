from datetime import datetime
import multiprocessing as mp
from typing import Tuple

import pandas as pd
import numpy as np

from .simulation_model import EssSimulationModel
from .config import PipelineParams


def get_monthly_max_load(df: pd.DataFrame):
    if "total_load" not in df.columns:
        raise KeyError("DataFrame must have a 'total_load' column.")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame index must be a DatetimeIndex.")

    monthly_total_load_max = df["total_load"].resample("ME").max()
    monthly_demand_load_max = df["demand_load"].resample("ME").max()
    return monthly_total_load_max.tolist(), monthly_demand_load_max.tolist()


def one_process(
    es_scale: float,
    node_name: str,
    params: PipelineParams,
) -> Tuple:
    es_info = {
        "transform_capacity": 8883000,
        "invertband": 0,
        "soc_redundant_ratio": 0,
        "usable_depth": 0.90,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": es_scale,
        "es_charge_min": -es_scale,
        "es_capacity_max": es_scale * 2,
        "es_capacity_min": 0,
    }

    print(f"save_range_start~save_range_end: {params.start_time}~{params.end_time}")

    demand_load_df = pd.read_csv(f"./data/{params.exp_name}/{node_name}/demand_load.csv")
    demand_load_df["time"] = pd.to_datetime(demand_load_df["time"])
    demand_load_df.set_index("time", inplace=True)
    demand_load_df = demand_load_df[
        (demand_load_df.index >= params.start_time)
        & (demand_load_df.index < params.end_time)
    ]
    demand_load_df["value"] = pd.to_numeric(demand_load_df["value"], errors="coerce")

    ele_price_df = pd.read_csv(f"./data/{params.exp_name}/{node_name}/ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])
    ele_price_df.set_index("time", inplace=True)
    ele_price_df = ele_price_df[
        (ele_price_df.index >= params.start_time)
        & (ele_price_df.index < params.end_time)
    ]

    strategy_df = pd.read_csv(
        f"./data/{params.exp_name}/{node_name}/opt_result/{params.strategy_dir}/schedule_result_scale_{es_scale}.csv"
    )
    if "power_opt" in strategy_df.columns:
        strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
    strategy_df["time"] = pd.to_datetime(strategy_df["time"])
    strategy_df.set_index("time", inplace=True)
    strategy_df = strategy_df[
        (strategy_df.index >= params.start_time)
        & (strategy_df.index < params.end_time)
    ]

    simulation_model = EssSimulationModel(es_info)
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(
        demand_load_df, strategy_df, 0
    )
    origin_balance, opt_balance = simulation_model.revenue_calculation(
        demand_load_df, es_charge_df, ele_price_df, params.max_demand_price
    )

    opt_max_demand_load_list, ori_max_demand_load_list = get_monthly_max_load(total_load_df)
    opt_max_demand_cost = params.max_demand_price * sum(opt_max_demand_load_list)
    ori_max_demand_cost = params.max_demand_price * sum(ori_max_demand_load_list)

    max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost
    revenue = origin_balance - opt_balance - max_demand_rise_cost
    total_energy = demand_load_df["value"].sum()

    ori_cost = origin_balance + ori_max_demand_cost
    opt_cost = opt_balance + opt_max_demand_cost

    es_charge_df["price"] = ele_price_df["value"]
    es_charge_df["balance"] = es_charge_df["value"] * es_charge_df["price"]
    charge_energy = -es_charge_df.loc[es_charge_df["value"] < 0, "value"].sum()
    discharge_energy = es_charge_df.loc[es_charge_df["value"] > 0, "value"].sum()
    charge_balance = -es_charge_df.loc[es_charge_df["balance"] < 0, "balance"].sum()
    discharge_balance = es_charge_df.loc[es_charge_df["balance"] > 0, "balance"].sum()

    return (
        es_scale,
        node_name,
        revenue,
        max_demand_rise_cost,
        total_energy,
        ori_cost,
        opt_cost,
        charge_energy,
        discharge_energy,
        charge_balance,
        discharge_balance,
    )


if __name__ == "__main__":
    # 选择算法版本: "without_demand" / "basic" / "optim"
    version = "optim"

    params = PipelineParams(
        exp_name="profit_calc/zijie",
        start_time=datetime(2025, 4, 1, 0, 0, 0),
        end_time=datetime(2026, 4, 1, 0, 0, 0),
        freq_minutes=60,
        es_scale_list=list(range(0, 21000, 1000)),
        node_name_list=["route_A"],
        max_demand_price=40.8,
        strategy_dir=f"es_scale_experiment_{version}",
    )

    print(f"start! {params.exp_name}")

    mp_input_list = [
        (x, y, params)
        for x in params.es_scale_list
        for y in params.node_name_list
    ]
    mp_result_list = []
    with mp.Pool(processes=params.num_processes) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)

    result_df_dict = {}
    for node_name in params.node_name_list:
        result_df_dict[node_name] = pd.DataFrame(
            data=np.nan,
            index=params.es_scale_list,
            columns=[
                "revenue",
                "max_demand_rise_cost",
                "ori_energy",
                "ori_cost",
                "opt_cost",
                "charge_energy",
                "discharge_energy",
                "charge_balance",
                "discharge_balance",
            ],
        )

    for result_i in mp_result_list:
        scale_i = result_i[0]
        node_name = result_i[1]
        result_df_dict[node_name].loc[scale_i, "revenue"] = result_i[2]
        result_df_dict[node_name].loc[scale_i, "max_demand_rise_cost"] = result_i[3]
        result_df_dict[node_name].loc[scale_i, "ori_energy"] = result_i[4]
        result_df_dict[node_name].loc[scale_i, "ori_cost"] = result_i[5]
        result_df_dict[node_name].loc[scale_i, "opt_cost"] = result_i[6]
        result_df_dict[node_name].loc[scale_i, "charge_energy"] = result_i[7]
        result_df_dict[node_name].loc[scale_i, "discharge_energy"] = result_i[8]
        result_df_dict[node_name].loc[scale_i, "charge_balance"] = result_i[9]
        result_df_dict[node_name].loc[scale_i, "discharge_balance"] = result_i[10]

    for k, v in result_df_dict.items():
        v.to_csv(f"./data/{params.exp_name}/{k}/opt_result/estimate_result_scale_all_{params.strategy_dir}.csv")
