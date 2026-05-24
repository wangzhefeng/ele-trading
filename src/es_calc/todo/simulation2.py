import multiprocessing as mp
from datetime import datetime

import pandas as pd
import numpy as np

from src.simulation.EssSimulation_withoutMaxDemand import EssSimulationModel

exp_name = "estimate_zhangjiakou1103"


def get_monthly_max_load(df: pd.DataFrame):
    """
    从一个以时间索引的 DataFrame 中，提取每个月 'load' 列的最大值。

    参数:
        df (pd.DataFrame): 输入的 DataFrame，其 index 必须是时间对象 (DatetimeIndex)。

    返回:
        List[float]: 一个包含每个月 'load' 列最大值的列表，按时间顺序排列。

    异常:
        KeyError: 如果 DataFrame 中不存在 'load' 列。
        TypeError: 如果 DataFrame 的 index 不是 DatetimeIndex。
    """
    # 检查 'load' 列是否存在
    if 'total_load' not in df.columns:
        raise KeyError("DataFrame must have a 'load' column.")

    # 检查 index 是否为 DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame index must be a DatetimeIndex.")

    # 使用 resample 方法按月分组，并获取每个月 'load' 列的最大值
    # 'M' 表示按月的末尾进行分组
    monthly_total_load_max = df['total_load'].resample('M').max()
    monthly_demand_load_max = df['demand_load'].resample('M').max()
    monthly_diff = monthly_total_load_max - monthly_demand_load_max

    # 将结果转换为列表并返回
    return monthly_total_load_max.tolist(), monthly_demand_load_max.tolist()


def one_process(es_scale, route_num_str):
    es_info = {"transform_capacity": 8883000,
                "invertband": 0,
                "soc_redundant_ratio": 0,
                "usable_depth": 0.95,
                "charge_loss": 0.92,
                "discharge_loss": 0.95,
                "es_charge_max": es_scale,
                "es_charge_min": -es_scale,
                "es_capacity_max": es_scale * 2,
                "es_capacity_min": 0}
    
    
    save_range_start = datetime(2024, 10, 1, 0, 0, 0)
    save_range_end = datetime(2025, 10, 1, 0, 0, 0)
    
    node_name = "route_{}".format(route_num_str)
    demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load.csv")
    demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
    demand_load_df.set_index('time', inplace=True)
    demand_load_df = demand_load_df[(demand_load_df.index >= save_range_start) & (demand_load_df.index < save_range_end)]

    ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
    ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])
    ele_price_df.set_index('time', inplace=True)
    ele_price_df = ele_price_df[(ele_price_df.index >= save_range_start) & (ele_price_df.index < save_range_end)]
    
    strategy_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/es_scale_experiment/schedule_result_scale_{es_scale}.csv")
    strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
    strategy_df['time'] = pd.to_datetime(strategy_df['time'])
    strategy_df.set_index('time', inplace=True)
    strategy_df = strategy_df[(strategy_df.index >= save_range_start) & (strategy_df.index < save_range_end)]

    max_demand_price = 34.6

    simulation_model = EssSimulationModel(es_info)
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(demand_load_df, strategy_df, 0)
    origin_balance, opt_balance = simulation_model.revenue_calculation(demand_load_df, es_charge_df, ele_price_df, max_demand_price)

    opt_max_demand_load_list, ori_max_demand_load_list = get_monthly_max_load(total_load_df)
    opt_max_demand_cost = max_demand_price * sum(opt_max_demand_load_list)
    ori_max_demand_cost = max_demand_price * sum(ori_max_demand_load_list)

    max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost

    revenue = origin_balance - opt_balance - max_demand_rise_cost

    total_energy = demand_load_df["value"].sum() / 12
    
    ori_cost = origin_balance + ori_max_demand_cost
    opt_cost = opt_balance + opt_max_demand_cost
    
    return es_scale, route_num_str, revenue, max_demand_rise_cost, total_energy, ori_cost, opt_cost

if __name__ == '__main__':
    print("start!", exp_name)
    es_scale_list = list(range(7900, 31000, 1000))
    route_list = ["A"]
    mp_input_list = [(x, y) for x in es_scale_list for y in route_list]
    mp_result_list = []

    with mp.Pool(processes=8) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)
    

    scale_result_df = pd.DataFrame(data=np.nan, index=es_scale_list, columns=['A',
                                                                            'A_demand',
                                                                            'A_ori_energy',
                                                                            'A_ori_cost',
                                                                            'A_opt_cost',
                                                                            'B', 
                                                                            'B_demand',
                                                                            'B_ori_energy',
                                                                            'B_ori_cost',
                                                                            'B_opt_cost',])

    
    for result_i in mp_result_list:
        scale_i = result_i[0]
        route_num_str = result_i[1]

        scale_result_df.loc[scale_i, route_num_str] = result_i[2]
        scale_result_df.loc[scale_i, f"{route_num_str}_demand"] = result_i[3]
        scale_result_df.loc[scale_i, f"{route_num_str}_ori_energy"] = result_i[4]
        scale_result_df.loc[scale_i, f"{route_num_str}_ori_cost"] = result_i[5]
        scale_result_df.loc[scale_i, f"{route_num_str}_opt_cost"] = result_i[6]
    

    scale_result_df.to_csv(f"./data/{exp_name}/estimate_result_scale_all.csv")

    