import pandas as pd
import multiprocessing as mp
import copy
from datetime import datetime

from models.optimization.EsArbitraryRangeScheduler_withMaxDemand_optim_pv_v3 import (
    EsArbitraryRangeScheduler_withMaxDemand
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


def generate_month_ranges(start_time, end_time):
    if start_time >= end_time:
        return []
    
    result = []
    # 将当前时间定位到 start_time 所在月的第一天 00:00:00
    current = start_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current < end_time:
        # 计算下一个月的第一天，即当前月的结束时间点 (代表 "24:00:00")
        if current.month == 12:
            next_month_start = current.replace(
                year=current.year + 1, month=1, day=1, 
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            next_month_start = current.replace(
                month=current.month + 1, day=1,
                hour=0, minute=0, second=0, microsecond=0
            )
        # 将 (本月开始, 下月开始) 添加到结果列表
        # 这里的 next_month_start 逻辑上代表 "本月最后一天的24:00:00"
        result.append((current, next_month_start))
        # 当前时间跳到下一个月
        current = next_month_start
        # 如果下一个月已经大于等于 end_time，则停止循环
        if current >= end_time:
            break
    
    return result


def one_process(es_scale, node_name, max_demand_price, start_time, end_time, exp_name, freq_minutes):
    # params
    devices_info = [{
        "usable_depth": 0.90,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": es_scale,
        "es_charge_min": -es_scale,
        "es_capacity_max": es_scale * 2,
        "es_capacity_min": 0,
        "transform_capacity": 1600,
    }]
    
    # data
    demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load.csv")
    demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
    ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
    ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])
    pv_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/pv_load.csv")
    pv_load_df['time'] = pd.to_datetime(pv_load_df['time'])
    
    # model
    validation_day_list = generate_month_ranges(start_time, end_time)
    days_strategy_list = []
    for time_pair in validation_day_list:
        vs_time, ve_time = time_pair[0], time_pair[1]
        print(f"start_time~end_time: {vs_time}~{ve_time}")
        mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
        step_demand_load_df = demand_load_df.loc[mask]
        mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
        step_ele_price_df = ele_price_df.loc[mask]
        mask = (pv_load_df['time'] >= vs_time) & (pv_load_df['time'] < ve_time)
        step_pv_load_df = pv_load_df.loc[mask]
        # step_ele_price_df = flat_valley_price_diff(step_ele_price_df)
        scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(
            step_demand_load_df["time"].to_list(), 
            step_demand_load_df["value"].to_list(), 
            step_ele_price_df["value"].to_list(), 
            step_ele_price_df["type"].to_list(),
            step_pv_load_df["value"].to_list(),
            devices_info,
            [0],
            max_demand_price,
            freq_minutes,
        )
        opt_list = scheduler_model.run()
        days_strategy_list.append(opt_list[0])

    result_df = pd.concat(days_strategy_list)
    result_df["time"] = result_df.index
    
    return es_scale, node_name, result_df




if __name__ == '__main__':
    exp_name = "hongtaiyang"
    print("start!", exp_name)

    # params
    start_time = datetime(2025, 1, 1, 0, 0, 0)
    end_time = datetime(2026, 1, 1, 0, 0, 0)
    freq_minutes = 15
    es_scale_list = list(range(0, 3750, 150))
    node_name_list = ["route_B"]
    max_demand_price = 33.8
    
    # model
    mp_input_list = [
        (x, y, max_demand_price, start_time, end_time, exp_name, freq_minutes) 
        for x in es_scale_list 
        for y in node_name_list
    ]
    mp_result_list = []
    with mp.Pool(processes=min(len(mp_input_list), 4)) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)
    
    # result
    for result_i in mp_result_list:
        es_scale = result_i[0]
        node_name = result_i[1]
        result_i[2].to_csv(f"./data/{exp_name}/{node_name}/opt_result/es_scale_experiment_optim/schedule_result_scale_{es_scale}.csv")
