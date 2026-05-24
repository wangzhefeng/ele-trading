import copy
from datetime import datetime
import multiprocessing as mp

import pandas as pd

from src.es_calc.es_calc_basic_version.optimization.EsArbitraryRangeScheduler_withMaxDemand_basic import EsArbitraryRangeScheduler_withMaxDemand
from utils.time_process import (
    generate_hourly_datetime_pairs, 
    get_month_range, 
    generate_day_pairs
)


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
    
    flat_ele_price_df.loc[flat_ele_price_df['type'] == '谷', 'value'] = flat_price
    flat_ele_price_df.loc[flat_ele_price_df['type'] == '深谷', 'value'] = flat_price
    
    return flat_ele_price_df


def one_process(es_scale, route_num_str, max_demand_price, start_time, end_time, exp_name):
    devices_info = [{
        "usable_depth": 0.90,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": es_scale,
        "es_charge_min": -es_scale,
        "es_capacity_max": es_scale * 2,
        "es_capacity_min": 0,
    }]
    
    node_name = f"route_{route_num_str}"
    demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load.csv")
    demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
    ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
    ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])

    validation_day_list = generate_day_pairs(start_time, end_time)

    days_strategy_list = []
    for time_pair in validation_day_list:
        vs_time = time_pair[0]
        ve_time = time_pair[1]
        mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
        step_demand_load_df = demand_load_df.loc[mask]
        mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
        step_ele_price_df = ele_price_df.loc[mask]
        # step_ele_price_df = flat_valley_price_diff(step_ele_price_df)
        scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(
            step_demand_load_df["time"].to_list(), 
            step_demand_load_df["value"].to_list(), 
            step_ele_price_df["value"].to_list(), 
            step_ele_price_df["type"].to_list(),
            devices_info,
            [0],
            max_demand_price,
            60,
        )
        opt_list = scheduler_model.run()
        days_strategy_list.append(opt_list[0])

    result_df = pd.concat(days_strategy_list)
    result_df["time"] = result_df.index
    
    return es_scale, node_name, result_df




if __name__ == '__main__':
    exp_name = "pinghu"
    print("start!", exp_name)

    # params
    start_time = datetime(2024, 10, 1, 0, 0, 0)
    end_time = datetime(2025, 10, 1, 0, 0, 0)
    es_scale_list = list(range(500, 80500, 500))
    route_list = ["A"]
    max_demand_price = 41.6
    # max_demand_price = 38.3

    # model
    mp_input_list = [(x, y, max_demand_price, start_time, end_time) for x in es_scale_list for y in route_list]
    mp_result_list = []
    with mp.Pool(processes=8) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)
    
    # result
    for result_i in mp_result_list:
        es_scale = result_i[0]
        node_name = result_i[1]
        result_path = Path(f"./data/{exp_name}/{node_name}/opt_result/es_scale_experiment_basic")
        result_path.mkdir(parents=True, exist_ok=True)
        result_i[2].to_csv(f"/schedule_result_scale_{es_scale}.csv")
