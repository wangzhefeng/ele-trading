# -*- coding: utf-8 -*-

# ***************************************************
# * File        : main.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2025-10-28
# * Version     : 1.0.102809
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import os
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import copy
from typing import Dict, List
import multiprocessing as mp
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif']=['SimHei']    # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False    # 用来显示负号
pd.set_option('display.float_format', lambda x: '%.2f' % x)

from models.EsArbitraryRangeScheduler_withMaxDemand import EsArbitraryRangeScheduler_withMaxDemand
from models.EssSimulation_withoutMaxDemand import EssSimulationModel
from utils.time_process import (
    generate_hourly_datetime_pairs, 
    get_month_range, 
    generate_day_pairs,
)

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
from utils.log_util import logger


# ------------------------------
# 工具函数
# ------------------------------
def flat_valley_price_diff(ele_price_df: pd.DataFrame):
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

def get_max_value_by_month(df: pd.DataFrame, target_time: datetime) -> float:
    # 提取目标月份和年份
    target_year = target_time.year
    target_month = target_time.month
    # 筛选出 time 列中年份和月份匹配的行
    mask = (df['time'].dt.year == target_year) & (df['time'].dt.month == target_month)
    filtered_df = df[mask]
    # 如果没有匹配的数据，返回 None
    if filtered_df.empty:
        return None
    # 返回 value 列的最大值
    return filtered_df['value'].max()

def get_max_value_by_day(df: pd.DataFrame, target_time: datetime) -> float:
    # 筛选出 time 列中年份和月份匹配的行
    if target_time == df["time"].min():
        filtered_df = df.loc[(df["time"] >= target_time) & (df["time"] < target_time + timedelta(days=1))]
    else:
        filtered_df = df.loc[(df["time"] >= target_time - timedelta(days=1)) & (df["time"] < target_time)]
    # 如果没有匹配的数据，返回 None
    if filtered_df.empty:
        return None
    # 返回 value 列的最大值
    return filtered_df['value'].max()

# ------------------------------
# input data
# ------------------------------
def load_demand_load(data_dir: str, exp_name: str, node_name: str, time_to_index: bool=False):
    demand_load_df = pd.read_csv(data_dir.joinpath(f"{exp_name}/{node_name}/demand_load.csv"))
    demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
    if time_to_index:
        demand_load_df.set_index("time", inplace=True)
    logger.info(f"demand_load_df: \n{demand_load_df}")

    return demand_load_df

def load_ele_price(data_dir: str, exp_name: str, node_name: str, time_to_index: bool=False):
    data_path = data_dir.joinpath(f"{exp_name}/{node_name}/ele_price.csv")
    ele_price_df = pd.read_csv(data_path)
    ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])
    if time_to_index:
        ele_price_df.set_index("time", inplace=True)
    logger.info(f"ele_price_df: \n{ele_price_df}")

    return ele_price_df

def load_strategy_path(data_dir: str, exp_name: str, node_name: str, demand_break_ratio: float, demand_load_by_day: bool=True):
    if demand_load_by_day:
        strategy_dir = data_dir.joinpath(f"{exp_name}/{node_name}/opt_result/day") 
    else:
        strategy_dir = data_dir.joinpath(f"{exp_name}/{node_name}/opt_result/month")
    strategy_dir.mkdir(parents=True, exist_ok=True)
    strategy_path = strategy_dir.joinpath(f"schedule_result_no_exceed_break{int(demand_break_ratio)}percent.csv")

    return strategy_dir, strategy_path

def load_strategy(strategy_path: Path, time_to_index: bool=True):
    strategy_df = pd.read_csv(strategy_path)
    strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
    strategy_df['time'] = pd.to_datetime(strategy_df['time'])
    if time_to_index:
        strategy_df.set_index('time', inplace=True)
    logger.info(f"strategy_df: \n{strategy_df}")

    return strategy_df

# ------------------------------
# 调度策略
# ------------------------------
def scheduler(exp_name, node_name, demand_load_df, ele_price_df, devices_info, 
              demand_break_ratio, data_dir, demand_load_by_day: bool=True):
    # 时间区间
    save_range_start = datetime(2024, 3, 1, 0, 0, 0)
    save_range_end = datetime(2025, 3, 1, 0, 0, 0)
    validation_day_list = generate_day_pairs(save_range_start, save_range_end)
    # 调度策略生成
    days_strategy_list = []
    for time_pair in validation_day_list:
        vs_time, ve_time = time_pair[0], time_pair[1]
        logger.info(f"vs_time-ve_time: {vs_time}-{ve_time}")
        # demand load
        step_demand_load_df = demand_load_df.loc[(demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time), :]
        # ele price
        step_ele_price_df = ele_price_df.loc[(ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time), :]
        # max demand line
        if demand_load_by_day:
            max_demand_line = get_max_value_by_day(demand_load_df, vs_time)
        else:
            max_demand_line = get_max_value_by_month(demand_load_df, vs_time)
        max_demand_line = max_demand_line * (1.00 + demand_break_ratio / 100.0)
        # scheduler model
        scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(
            schedule_time_range = step_demand_load_df["time"].to_list(),
            demand_load = step_demand_load_df["value"].to_list(), 
            ele_prices = step_ele_price_df["value"].to_list(), 
            ele_types = step_ele_price_df["type"].to_list(),
            devices_info = devices_info,
            current_soc_list = [0],
            max_demand_line = max_demand_line,
        )
        opt_list = scheduler_model.run()
        # results
        days_strategy_list.append(opt_list[0])
    # 策略结果处理
    result_df = pd.concat(days_strategy_list)
    result_df["time"] = result_df.index
    save_result_df = result_df.loc[(result_df['time'] >= save_range_start) & (result_df['time'] < save_range_end), :]
    # 策略结果保存
    strategy_dir, strategy_path = load_strategy_path(data_dir, exp_name, node_name, demand_break_ratio, demand_load_by_day)
    save_result_df.to_csv(strategy_path, encoding="utf-8", index=False)

def run_scheduler(exp_name, node_name, demand_load_df, ele_price_df, devices_info, 
                  demand_break_ratios, data_dir, demand_load_by_day: bool=True):
    # simulation
    mp_input_list = [(
        exp_name, node_name, demand_load_df, ele_price_df, 
        devices_info, demand_break_ratio, data_dir, demand_load_by_day,
    ) for demand_break_ratio in demand_break_ratios]
    with mp.Pool(processes=8) as pool:
        pool.starmap(scheduler, mp_input_list)

# ------------------------------
# 策略模拟和收益计算
# ------------------------------
def get_monthly_max_load(df: pd.DataFrame):
    """
    从一个以时间索引的 DataFrame 中，提取每个月 'load' 列的最大值
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

def simulation(exp_name, node_name, demand_load_df, ele_price_df, max_demand_price, es_info: Dict, 
               demand_break_ratio: List, data_dir: Path, demand_load_by_day: bool=True):
    # 策略数据
    strategy_dir, strategy_path = load_strategy_path(data_dir, exp_name, node_name, demand_break_ratio, demand_load_by_day)
    strategy_df = load_strategy(strategy_path, time_to_index=True)
    # simulation model
    simulation_model = EssSimulationModel(es_info)
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(
        demand_load = demand_load_df, 
        es_strategy = strategy_df, 
        last_soc = 0,
    )
    origin_balance, opt_balance = simulation_model.revenue_calculation(
        demand_load = demand_load_df, 
        es_load = es_charge_df, 
        ele_price = ele_price_df, 
        max_demand_price = max_demand_price,
    )
    # demand load
    opt_max_demand_load_list, ori_max_demand_load_list = get_monthly_max_load(total_load_df)
    opt_max_demand_cost = max_demand_price * sum(opt_max_demand_load_list)
    ori_max_demand_cost = max_demand_price * sum(ori_max_demand_load_list)
    # demand rise cost
    max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost
    # revenue
    revenue = origin_balance - opt_balance - max_demand_rise_cost
    # save result
    es_charge_df.to_csv(
        strategy_dir.joinpath(f"simulation_result_optcharge_dod95-break{int(demand_break_ratio)}percent.csv"),
        encoding="utf-8", index=False,
    )
    
    return revenue, max_demand_rise_cost

def run_simulation(exp_name, node_name, demand_load_df, ele_price_df, max_demand_price: int, es_info: Dict, 
                   demand_break_ratios: List, data_dir: Path, demand_load_by_day: bool=True):
    # data process
    demand_load_df.set_index("time", inplace=True)
    ele_price_df.set_index("time", inplace=True)
    # simulation
    mp_input_list = [(
        exp_name, node_name, demand_load_df, ele_price_df, max_demand_price, 
        es_info, demand_break_ratio, data_dir, demand_load_by_day,
    ) for demand_break_ratio in demand_break_ratios]
    with mp.Pool(processes=8) as pool:
        simulation_result = pool.starmap(simulation, mp_input_list)
    # results collect
    revenue_list = []
    max_demand_rise_cost_list = []
    for res in simulation_result:
        revenue_list.append(res[0])
        max_demand_rise_cost_list.append(res[1])
    results = pd.DataFrame({
        f"revenue_{node_name.split('_')[-1]}": revenue_list,
        f"max_demand_rise_cost_{node_name.split('_')[-1]}": max_demand_rise_cost_list,
    })
    
    return results




# 测试代码 main 函数
def main():
    # ------------------------------
    # params
    # ------------------------------
    # project/node name
    exp_name = "estimate1016"
    node_names = ["route_A", "route_B"]
    # input/output data dir
    data_dir = Path(__file__).parent.joinpath("./data")
    logger.info(f"data_dir: {data_dir}")
    # battery devices info
    devices_info = [{
        "usable_depth": 0.95,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": 12500,
        "es_charge_min": -12500,
        "es_capacity_max": 25000,
        "es_capacity_min": 0,
    }]
    # simulation info
    es_info = {
        "transform_capacity": 630000,
        "invertband": 0,
        "soc_redundant_ratio": 0,
        "usable_depth": 0.95,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": 12500,
        "es_charge_min": -12500,
        "es_capacity_max": 25000,
        "es_capacity_min": 0,
    }
    # demand load
    max_demand_price = 37
    demand_break_ratios = range(0, 11, 1)
    demand_load_by_day = True
    # ------------------------------
    # calc
    # ------------------------------
    revenue_results = pd.DataFrame({"demand_break_ratio": demand_break_ratios})
    for node_name in node_names:
        # data
        demand_load_df = load_demand_load(data_dir, exp_name, node_name, time_to_index=False)
        ele_price_df = load_ele_price(data_dir, exp_name, node_name, time_to_index=False)
        # scheduler
        # run_scheduler(
        #     exp_name = exp_name,
        #     node_name = node_name,
        #     demand_load_df = demand_load_df,
        #     ele_price_df = ele_price_df,
        #     devices_info = devices_info,
        #     demand_break_ratios = demand_break_ratios,
        #     data_dir = data_dir,
        #     demand_load_by_day = demand_load_by_day,
        # )
        # simulation
        node_results = run_simulation(
            exp_name = exp_name,
            node_name = node_name,
            demand_load_df = demand_load_df,
            ele_price_df = ele_price_df,
            max_demand_price = max_demand_price,
            es_info = es_info,
            demand_break_ratios = demand_break_ratios,
            data_dir = data_dir,
            demand_load_by_day = demand_load_by_day,
        )
        revenue_results = pd.concat([revenue_results, node_results], axis=1)
    # 收益结果收集及保存
    revenue_results["revenue_total"] = revenue_results["revenue_A"] + revenue_results["revenue_B"]
    logger.info(f"revenue_results: \n{revenue_results}")
    if demand_load_by_day:
        revenue_results.to_csv(
            data_dir.joinpath(f"{exp_name}/revenue_results_day.csv"), 
            encoding="utf-8", index=False
        )
    else:
        revenue_results.to_csv(
            data_dir.joinpath(f"{exp_name}/revenue_results_month.csv"), 
            encoding="utf-8", index=False
        )

if __name__ == "__main__":
    main()
