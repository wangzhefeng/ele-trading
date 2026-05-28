import copy
import multiprocessing as mp
from datetime import datetime
from typing import Tuple

import pandas as pd

from .config import AlgorithmProfile, PipelineParams, get_profile
from .scheduler import EsArbitraryRangeScheduler
from .time_splitting import get_time_ranges


def flat_valley_price_diff(ele_price_df):
    flat_ele_price_df = copy.deepcopy(ele_price_df)
    if flat_ele_price_df["type"].isin(["谷"]).any():
        v_index = flat_ele_price_df[flat_ele_price_df["type"] == "谷"].index[-1]
    else:
        v_index = -1
    if flat_ele_price_df["type"].isin(["深谷"]).any():
        dv_index = flat_ele_price_df[flat_ele_price_df["type"] == "深谷"].index[-1]
    else:
        dv_index = -1

    flat_price_index = max(v_index, dv_index)
    flat_price = flat_ele_price_df.loc[flat_price_index, "value"]

    flat_ele_price_df.loc[flat_ele_price_df["type"] == "谷", "value"] = flat_price
    flat_ele_price_df.loc[flat_ele_price_df["type"] == "深谷", "value"] = flat_price

    return flat_ele_price_df


def one_process(
    es_scale: float,
    node_name: str,
    params: PipelineParams,
    profile: AlgorithmProfile,
) -> Tuple[float, str, pd.DataFrame]:
    devices_info = [
        {
            "usable_depth": 0.90,
            "charge_loss": 0.92,
            "discharge_loss": 0.95,
            "es_charge_max": es_scale,
            "es_charge_min": -es_scale,
            "es_capacity_max": es_scale * 2,
            "es_capacity_min": 0,
        }
    ]

    demand_load_df = pd.read_csv(f"./data/{params.exp_name}/{node_name}/demand_load.csv")
    demand_load_df["time"] = pd.to_datetime(demand_load_df["time"])
    ele_price_df = pd.read_csv(f"./data/{params.exp_name}/{node_name}/ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])

    time_ranges = get_time_ranges(params.start_time, params.end_time, profile.time_splitting)
    strategy_list = []
    for vs_time, ve_time in time_ranges:
        print(f"start_time~end_time: {vs_time}~{ve_time}")
        mask = (demand_load_df["time"] >= vs_time) & (demand_load_df["time"] < ve_time)
        step_demand_load_df = demand_load_df.loc[mask]
        mask = (ele_price_df["time"] >= vs_time) & (ele_price_df["time"] < ve_time)
        step_ele_price_df = ele_price_df.loc[mask]

        scheduler_model = EsArbitraryRangeScheduler(
            step_demand_load_df["time"].to_list(),
            step_demand_load_df["value"].to_list(),
            step_ele_price_df["value"].to_list(),
            step_ele_price_df["type"].to_list(),
            devices_info,
            [params.current_soc],
            params.max_demand_price,
            params.freq_minutes,
            profile,
            params.transform_capacity,
        )
        opt_list = scheduler_model.run()
        strategy_list.append(opt_list[0])

    result_df = pd.concat(strategy_list)
    result_df["time"] = result_df.index
    return es_scale, node_name, result_df


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
        transform_capacity=63000,
        num_processes=8,
        strategy_dir=f"es_scale_experiment_{version}",
        current_soc=0.0,
    )

    profile = get_profile(version)
    print(f"start! {params.exp_name}, version={version}")

    mp_input_list = [
        (x, y, params, profile)
        for x in params.es_scale_list
        for y in params.node_name_list
    ]
    mp_result_list = []
    with mp.Pool(processes=params.num_processes) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)

    for result_i in mp_result_list:
        es_scale = result_i[0]
        node_name = result_i[1]
        result_i[2].to_csv(
            f"./data/{params.exp_name}/{node_name}/opt_result/{params.strategy_dir}/schedule_result_scale_{es_scale}.csv"
        )
