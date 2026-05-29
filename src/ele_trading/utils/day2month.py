# -*- coding: utf-8 -*-

# ***************************************************
# * File        : day2month.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-01-26
# * Version     : 1.0.012616
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
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import pandas as pd

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
from .log_util import logger


def generate_month_ranges(start_time: datetime, end_time: datetime) -> List[Tuple[datetime, datetime]]:
    """
    生成每个月的开始、结束时间戳

    Args:
        start_time (datetime):  数据开始时间
        end_time (datetime): _description_

    Returns:
        _type_: _description_
    """
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


def __calc_month_statistics(step_strategy_df: pd.DataFrame, 
                            target_col: str, 
                            vs_time: datetime, 
                            transfer_data: float):
    """
    每个月不同时间段充电时间、放电时间统计
    """
    df_pivot = step_strategy_df.pivot_table(
        index="time_col", 
        columns="date_col", 
        values=target_col,
        aggfunc="first",
    )
    df_pivot = df_pivot.reset_index()
    df_pivot["year"] = vs_time.year
    df_pivot["month"] = vs_time.month
    
    df = df_pivot[[col for col in df_pivot.columns if col != "time_col"]].groupby(["year", "month"]).sum() * transfer_data
    df["time_len"] = df[[col for col in df.columns if col not in ["year", "month"]]].mean(axis=1)
    df = df.reset_index()
    df = df[["year", "month", "time_len"]]

    return df


def calc_statistics(strategy_df: pd.DataFrame, start_time: datetime, end_time: datetime, device_info: Dict=None):
    # 可用电量
    # power_total = float(device_info["es_capacity_max"]) * float(device_info["usable_depth"])
    # logger.info(f"power_total: {power_total}")
    
    # 数据频率(小时)
    freq_hours = (strategy_df.index[1] - strategy_df.index[0]).total_seconds() / (60*60)
    logger.info(f"freq_hours: {freq_hours}")
    # 数据预处理
    strategy_df = strategy_df.copy()
    strategy_df["date_col"] = strategy_df.index.date
    strategy_df["time_col"] = strategy_df.index.time
    strategy_df["charge_count"] = strategy_df["value"].apply(lambda x: 1 if x < 0.0 else 0)
    strategy_df["discharge_count"] = strategy_df["value"].apply(lambda x: 1 if x > 0.0 else 0)
    strategy_df["charge_load"] = strategy_df["value"].apply(lambda x: x if x < 0.0 else 0)
    strategy_df["discharge_load"] = strategy_df["value"].apply(lambda x: x if x > 0.0 else 0)
    strategy_df.reset_index(inplace=True)
    logger.info(f"strategy_df: \n{strategy_df}")
    # 分月统计
    strategy_load_df = pd.DataFrame()
    # 生成每个月的开始、结束时间戳
    validation_day_list = generate_month_ranges(start_time, end_time)
    logger.info(f"validation_day_list: \n{validation_day_list}")
    for time_pair in validation_day_list:
        # 每个月时间范围
        vs_time, ve_time = time_pair[0], time_pair[1]
        logger.info(f"vs_time: {vs_time}, ve_time: {ve_time}")
        # 每个月 demand_load 数据
        mask = (strategy_df['time'] >= vs_time) & (strategy_df['time'] < ve_time)
        step_strategy_df = strategy_df.loc[mask]
        # ------------------------------
        # 每个月不同时间段充放电功率统计
        # ------------------------------
        df_load_pivot = step_strategy_df.pivot_table(
            index="time_col", 
            columns="date_col", 
            values="value",
            aggfunc="first",
        )
        df_load_pivot["strategy_load_avg"] = df_load_pivot.mean(axis=1)
        df_load_pivot = df_load_pivot.reset_index()
        df_load_pivot["year"] = vs_time.year
        df_load_pivot["month"] = vs_time.month
        df_load_pivot["hour"] = df_load_pivot["time_col"].apply(lambda x: f"{x.hour:02d}")
        df_load_pivot = df_load_pivot[["year", "month", "hour", "time_col", "strategy_load_avg"]]
        # 结果收集
        strategy_load_df = pd.concat([strategy_load_df, df_load_pivot], axis=0)
    return {
        # "actual_charge_time_len": actual_charge_time_len,
        # "actual_discharge_time_len": actual_discharge_time_len,
        # "equivalent_charge_time_len": equivalent_charge_time_len,
        # "equivalent_discharge_time_len": equivalent_discharge_time_len,
        "strategy_load_df": strategy_load_df,
    }




# 测试代码 main 函数
def main():
    exp_name = "chengdu"
    node_name = "route_A"
    es_scale = 1000
    data_path = f"./data/{exp_name}/{node_name}/opt_result/es_scale_experiment_optim/schedule_result_scale_{es_scale}.csv"
    df = pd.read_csv(data_path)
    df["time"] = pd.to_datetime(df["time"])
    df = df[["time", "value"]]
    df.set_index("time", inplace=True)
    print(df)

    result = calc_statistics(
        strategy_df=df, 
        start_time=pd.to_datetime("2025-01-01 00:00:00"), 
        end_time=pd.to_datetime("2026-01-01 23:59:59")
    )
    result["strategy_load_df"].to_csv("strategy_load_df.csv", index=False, encoding="utf-8")
    print(result["strategy_load_df"])

if __name__ == "__main__":
    main()
