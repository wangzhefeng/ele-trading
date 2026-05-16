# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)

from typing import Dict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response.response_profit import (
    calc_profit,
)
from utils.log_util import logger


def judge_response_period(response_period: pd.DataFrame, 
                          adjustable_period: Dict) -> bool:
    """
    判断需求响应时段是否在可调整时段内

    Args:
        response_period (pd.DataFrame): 需求响应时段
        adjustable_period (Dict): 需求响应时段可能的时段

    Returns:
        bool: 需求响应时段是否在可调整时段内
    """
    # 响应时段
    s_time, e_time = response_period["start"], response_period["end"]
    # 响应日期
    response_date = s_time.date()
    # 判断规则
    if (s_time >= adjustable_period["start"] and e_time <= pd.to_datetime(f"{response_date} 19:00:00")) or \
       (s_time >= pd.to_datetime(f"{response_date} 21:00:00") and e_time <= adjustable_period["end"]):
        return True
    else:
        return False

def get_response_time_len(response_period: Dict) -> float:
    """
    计算响应时长(小时, h)

    Args:
        response_period (Dict): 响应时段

    Returns:
        float: 响应时长
    """
    response_time_len = (
        response_period["end"] - response_period["start"]
    ).total_seconds() / 3600

    return response_time_len

def get_remain_power(df_strategy_period: pd.DataFrame, 
                     stop_time: datetime, 
                     battery_capacity: float) -> float:
    """
    计算储能电池截止到某个时刻的剩余电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(储能电池SOC)
        stop_time (datetime): 截止时刻
        battery_capacity (float): 储能电池容量

    Returns:
        float: 剩余电量
    """
    battery_soc = df_strategy_period.loc[
        df_strategy_period["time"] == stop_time, 
        "soc"
    ].values[0]
    if battery_soc > 0.05:
        remain_power = battery_capacity * battery_soc
    else:
        remain_power = 0.0

    return remain_power

# TODO
def get_discharge_power(df_strategy_period: pd.DataFrame, 
                        time_period: Dict, 
                        freq: str="5min") -> float:
    """
    计算某一个时段的放电电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段

    Returns:
        float: 放电电量
    """
    # 该时段时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    # 该时段的放电功率
    discharge_power = df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] > 0.0), 
        "strategy_load"
    ].sum() * (int(freq[:-3]) / 60)

    return discharge_power

# TODO
def get_discharge_power_v2(df_strategy_period: pd.DataFrame, 
                           time_period: Dict, 
                           battery_capacity: float) -> float:
    """
    计算某一个时段的放电电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段

    Returns:
        float: 放电电量
    """
    remain_power_before_discharge = get_remain_power(
        df_strategy_period, time_period["start"], battery_capacity
    )
    remain_power_after_discharge = get_remain_power(
        df_strategy_period, time_period["end"], battery_capacity
    )
    if remain_power_before_discharge >= remain_power_after_discharge:
        discharge_power = remain_power_before_discharge - remain_power_after_discharge
        return discharge_power
    else:
        raise Exception("remain_power_before_8 < remain_power_before_10") 

def get_discharge_time_len(df_strategy_period: pd.DataFrame, 
                           time_period: Dict) -> float:
    """
    计算某一个时段的放电时长

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段

    Returns:
        float: 放电时长
    """
    # 该时段时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    # 该时段的放电时长
    discharge_timestamp = df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] > 0.0), 
        "time"
    ]
    discharge_time_len = (
        discharge_timestamp.max() - discharge_timestamp.min()
    ).total_seconds() / 3600

    return discharge_time_len

# TODO
def get_charge_power(df_strategy_period: pd.DataFrame, 
                     time_period: Dict, 
                     freq: str="5min") -> float:
    """
    计算某一个时段的充电电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段

    Returns:
        float: 充电电量
    """
    # 该时段时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    # 该时段的充电功率
    charge_power = df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] < 0.0), 
        "strategy_load"
    ].sum() * (int(freq[:-3]) / 60)

    return charge_power

# TODO 基线调整系数、爬坡阶段
def get_period_load(df_strategy_period: pd.DataFrame, 
                    time_period: Dict) -> np.ndarray:
    """
    获取某个时段策略功率

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段

    Returns:
        np.ndarray: _description_
    """
    # 该时段时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    # 该时段的放电功率不进行修改
    period_load = df_strategy_period.loc[period_mask, "strategy_load"].values
    
    return period_load
# -----------------------------------------------------------------------------
def baseline_coef_period_adjust(df_strategy_period: pd.DataFrame, 
                                time_period: Dict) -> pd.DataFrame:
    """
    将基线调整系数样本所在时段内的放电（如果有）修改为待机
    
    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段
    
    Returns:
        pd.DataFrame: 调整后的策略
    """
    # 该时段时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    # 该时段的放电功率修改为 0
    df_strategy_period.loc[period_mask, "strategy_load"] = df_strategy_period.loc[
        period_mask, "strategy_load"
    ].apply(lambda x: x if x <= 0.0 else 0.0)
    
    return df_strategy_period

def climbing_period_adjust(df_strategy_period: pd.DataFrame, 
                           time_period: Dict) -> pd.DataFrame:
    """
    爬坡时段内的放电（如果有）修改为待机

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段
    
    Returns:
        pd.DataFrame: 调整后的策略
    """
    # 该时段时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    # 该时段的放电功率修改为 0
    df_strategy_period.loc[period_mask, "strategy_load"] = df_strategy_period.loc[
        period_mask, "strategy_load"
    ].apply(lambda x: x if x <= 0.0 else 0.0)
    
    return df_strategy_period

def response_period_adjust(df_strategy_period: pd.DataFrame, 
                           time_period: Dict, 
                           response_capacity: float,
                           max_discharge_load: float, 
                           max_charge_load: float) -> pd.DataFrame:
    """
    需求响应时段内充放电策略的调整

    Args:
        df_strategy_period (pd.DataFrame): 需求响应调整前的策略
        time_period (Dict): 响应时段
        response_capacity (float): 需求响应容量(kWh)

    Returns:
        pd.DataFrame: 需求响应调整后的策略
    """
    # 响应时长
    response_time_len = get_response_time_len(response_period=time_period)
    logger.info(f"debug::response_time_len: {response_time_len}")
    # 该时段时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    # 响应功率
    response_load = response_capacity / response_time_len
    response_load = np.nanmin([response_load, max_discharge_load])
    response_load = np.nanmax([response_load, max_charge_load])
    logger.info(f"debug::response_load: {response_load}")
    # 充放电响应储能功率策略调整
    df_strategy_period.loc[period_mask, "strategy_load"] = df_strategy_period.loc[
        period_mask, "strategy_load"
    ].apply(lambda x: x + response_load)

    return df_strategy_period

def response_period_readjust(df_strategy_period: pd.DataFrame, 
                             time_period: Dict, 
                             peak_discharge_power: float, 
                             response_period_discharge_power: float,
                             max_discharge_load: float, 
                             max_charge_load: float) -> pd.DataFrame:
    """
    需求响应时段内充放电策略的调整
    """
    # 需求响应剩余放电量
    discharge_power_remain = peak_discharge_power - response_period_discharge_power
    # 需求响应时段内充放电策略的调整
    if discharge_power_remain < 0.0:  # 不够需求响应放电
        df_strategy_period = response_period_adjust(
            df_strategy_period, time_period, peak_discharge_power, max_discharge_load, max_charge_load,
        )
        return df_strategy_period
    elif discharge_power_remain >= 0.0:  # 需求响应正常放电量足够
        return df_strategy_period

def response_before_1h_period_adjust(df_strategy_period: pd.DataFrame, 
                                     response_period: Dict, 
                                     response_before_1h_period: Dict, freq: str):
    """
    需求响应时段前 1 小时时段内电池冷静期策略调整
    """
    # 需求响应时段前 1 小时前 mask
    before_response_before_1h_period_mask = (
        (df_strategy_period["time"] >= response_before_1h_period["start"] - timedelta(minutes=int(freq[:-3]))) 
        & (df_strategy_period["time"] < response_before_1h_period["start"])
    )
    # 需求响应时段前 1 小时 mask
    response_before_1h_period_mask = (
        (df_strategy_period["time"] >= response_before_1h_period["start"]) & 
        (df_strategy_period["time"] < response_before_1h_period["end"])
    )
    # 需求响应时段 mask
    response_period_mask = (
        (df_strategy_period["time"] > response_period["start"]) & 
        (df_strategy_period["time"] <= response_period["end"])
    )
    # 响应时段功率值
    response_period_values = df_strategy_period.loc[
        response_period_mask, 
        "strategy_load"
    ].values
    logger.info(f"debug::response_period_values: {response_period_values}")
    # 响应时段前 1 小时前功率值
    before_response_before_1h_period_values = df_strategy_period.loc[
        before_response_before_1h_period_mask, "strategy_load"
    ].values
    logger.info(f"debug::before_response_before_1h_period_values: {before_response_before_1h_period_values}")
    # 如果响应时段为放电，响应时段前 1 小时前策略为充电，则将响应时段前 1 小时前策略调整为 0.0
    if (response_period_values.all() > 0.0 and before_response_before_1h_period_values[0] < 0.0) or \
       (response_period_values.all() < 0.0 and before_response_before_1h_period_values[0] > 0.0):
        df_strategy_period.loc[response_before_1h_period_mask, "strategy_load"] = 0.0

    return df_strategy_period

def response_after_1h_period_adjust(df_strategy_period: pd.DataFrame, 
                                    response_period: Dict, 
                                    response_after_1h_period: Dict, freq: str):
    """
    需求响应时段前 1 小时时段内电池冷静期策略调整
    """
    # 需求响应时段 mask
    response_period_mask = (
        (df_strategy_period["time"] > response_period["start"]) & 
        (df_strategy_period["time"] <= response_period["end"])
    )
    # 需求响应时段后 1 小时 mask
    response_after_1h_period_mask = (
        (df_strategy_period["time"] >= response_after_1h_period["start"]) & 
        (df_strategy_period["time"] <= response_after_1h_period["end"])
    )
    # 需求响应时段后 1 小时后 mask
    after_response_after_1h_period_mask = (
        (df_strategy_period["time"] > response_after_1h_period["end"]) & 
        (df_strategy_period["time"] <= response_after_1h_period["end"] + timedelta(minutes=int(freq[:-3])))
    )
    # 响应时段功率值
    response_period_values = df_strategy_period.loc[
        response_period_mask, "strategy_load"
    ].values
    # 响应时段后 1 小时后功率值
    after_response_after_1h_period_values = df_strategy_period.loc[
        after_response_after_1h_period_mask, "strategy_load"
    ].values
    # 如果响应时段为放电，响应时段后 1 小时后策略为充电，则将响应时段后 1 小时后策略调整为 0.0
    if (response_period_values.all() > 0.0 and after_response_after_1h_period_values[0] < 0.0) or \
        (response_period_values.all() < 0.0 and after_response_after_1h_period_values[0] > 0.0):
        df_strategy_period.loc[response_after_1h_period_mask, "strategy_load"] = df_strategy_period.loc[
            response_after_1h_period_mask, "strategy_load"
        ].apply(lambda x: x if x >= 0.0 else 0.0)

    return df_strategy_period

# TODO 是否平均放电？
def discharge_period_adjust(df_strategy_period: pd.DataFrame, 
                            time_period: Dict,
                            peak_discharge_power: float,
                            response_period_discharge_power: float,
                            baseline_coef_period_discharge_power: float,
                            baseline_coef_period_discharge_time_len: float):
    """
    峰时放电时段策略调整
    """
    discharge_power_remain = peak_discharge_power - (
        response_period_discharge_power - baseline_coef_period_discharge_power
    )
    discharge_time_len_remain = (
        time_period["start"] - time_period["end"]
    ).total_seconds() / 3600 - baseline_coef_period_discharge_time_len
    if discharge_time_len_remain == 0.0:
        discharge_load = 0.0
    else:
        discharge_load = discharge_power_remain / discharge_time_len_remain
    # 该时段可调整时间索引
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["start"] + timedelta(hours=discharge_time_len_remain))
    )
    df_strategy_period.loc[period_mask, "strategy_load"] = discharge_load
    
    return df_strategy_period

def charge_period_adjust(df_strategy_period: pd.DataFrame, time_period: Dict):
    """
    平时充电时段策略调整
    """
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"]) & 
        (df_strategy_period["time"] <= time_period["end"])
    )
    df_strategy_period.loc[period_mask, "strategy_load"] = 0.0

    return df_strategy_period

def before_response_strategy(df_strategy_period: pd.DataFrame, 
                             baseline_coef_period: Dict, 
                             response_period: Dict, 
                             response_capacity: float,
                             max_discharge_load: float, 
                             max_charge_load: float):
    """
    需求响应时段理论可放电量策略调整、基线调整系数修正
    """
    # 基线调整系数样本所在时段内策略的调整
    df_strategy_period = baseline_coef_period_adjust(
        df_strategy_period, baseline_coef_period
    )
    # 爬坡时段策略调整
    # df_strategy_period = climbing_period_adjust(
    #     df_strategy_period, climbing_period,
    # )
    # 需求响应时段内充放电策略的调整
    df_strategy_period = response_period_adjust(
        df_strategy_period, response_period, 
        response_capacity, max_discharge_load, max_charge_load
    )

    return df_strategy_period

def after_response_strategy(df_strategy_period: pd.DataFrame, 
                            response_before_1h_period: Dict, 
                            response_after_1h_period: Dict, 
                            response_period: Dict, 
                            freq: float):
    """
    响应策略基本调整
    """
    # 需求响应时段前 1 小时时段内电池冷静期策略调整
    df_strategy_period = response_before_1h_period_adjust(
        df_strategy_period, response_period, response_before_1h_period, freq
    )
    # 需求响应时段后 1 小时时段内电池冷静期策略调整
    df_strategy_period = response_after_1h_period_adjust(
        df_strategy_period, response_period, response_after_1h_period, freq
    )
    
    return df_strategy_period
# -----------------------------------------------------------------------------
def get_strategy_info(response_date: datetime, 
                      df_strategy_period: pd.DataFrame,
                      baseline_coef_period: Dict,
                      climbing_period: Dict,
                      response_before_1h_period: Dict,
                      battery_capacity: float):
    # ------------------------------
    # 响应前：计算某时段的放电量、剩余电量、放电时长
    # ------------------------------
    # 计算 08:00~10:00 时段的可放电量
    peak1_time_period = {
        "start": pd.to_datetime(f"{response_date} 08:00:00"), 
        "end": pd.to_datetime(f"{response_date} 10:00:00"),
    }
    # TODO peak1_discharge_power = get_discharge_power(df_strategy_period, peak1_time_period)
    peak1_discharge_power = get_discharge_power_v2(df_strategy_period, peak1_time_period, battery_capacity)
    logger.info(f"debug::peak1_discharge_power: {peak1_discharge_power} kWh")
    
    # 计算 19:00~21:00 时段的可放电量
    peak2_time_period = {
        "start": pd.to_datetime(f"{response_date} 19:00:00"), 
        "end": pd.to_datetime(f"{response_date} 21:00:00"),
    }
    # TODO peak2_discharge_power = get_discharge_power(df_strategy_period, peak2_time_period)
    peak2_discharge_power = get_discharge_power_v2(df_strategy_period, peak2_time_period, battery_capacity)
    logger.info(f"debug::peak2_discharge_power: {peak2_discharge_power} kWh")
    
    # 计算基线调整系数样本所在时段的放电量、放电时长
    # TODO baseline_coef_period_discharge_power = get_discharge_power(df_strategy_period, baseline_coef_period)
    baseline_coef_period_discharge_power = get_discharge_power_v2(df_strategy_period, baseline_coef_period, battery_capacity)
    baseline_coef_period_discharge_time_len = get_discharge_time_len(df_strategy_period, baseline_coef_period)
    logger.info(f"debug::baseline_coef_period_discharge_power: {baseline_coef_period_discharge_power} kWh")
    logger.info(f"debug::baseline_coef_period_discharge_time_len: {baseline_coef_period_discharge_time_len} h")
    
    # 计算爬坡时段的放电量、放电时长
    # TODO climbing_period_discharge_power = get_discharge_power(df_strategy_period, climbing_period)
    climbing_period_discharge_power = get_discharge_power_v2(df_strategy_period, climbing_period, battery_capacity)
    climbing_period_discharge_time_len = 0.5 #get_discharge_time_len(df_strategy_period, climbing_period)
    logger.info(f"debug::climbing_period_discharge_power: {climbing_period_discharge_power} kWh")
    logger.info(f"debug::climbing_period_discharge_time_len: {climbing_period_discharge_time_len} h")
    # ------------------------------
    # 响应前：计算某时段的充电量、剩余电量、充电时长
    # ------------------------------
    flat_time_period = {
        "start": pd.to_datetime(f"{response_date} 11:00:00"), 
        "end": response_before_1h_period["start"],
    }
    # TODO 平时充电时段至响应时段开始前一小时时刻的充电量
    flat_charge_power = get_charge_power(df_strategy_period, flat_time_period)
    # 计算响应时段开始前一小时时刻的电池剩余电量
    remain_power_before_response = get_remain_power(df_strategy_period, flat_time_period["end"], battery_capacity)
    logger.info(f"debug::flat_charge_power: {flat_charge_power} kWh")
    logger.info(f"debug::remain_power_before_response: {remain_power_before_response} kWh")
    
    return (
        peak1_time_period, peak1_discharge_power, 
        peak2_time_period, peak2_discharge_power,
        baseline_coef_period_discharge_power, baseline_coef_period_discharge_time_len,
        climbing_period_discharge_power, climbing_period_discharge_time_len,
        flat_time_period, flat_charge_power,
        remain_power_before_response,
    )

def strategy_adjust_model_1(df_strategy_period: pd.DataFrame, 
                            baseline_coef_period: Dict, 
                            response_period: Dict,
                            response_before_1h_period: Dict,
                            response_after_1h_period: Dict,
                            response_capacity: float, 
                            max_discharge_load: float,
                            max_charge_load: float,
                            peak_discharge_power: float,
                            freq: str):
    # 需求响应时段理论可放电量策略调整、基线调整系数修正
    df_strategy_period = before_response_strategy(
        df_strategy_period, baseline_coef_period, response_period, 
        response_capacity, max_discharge_load, max_charge_load,
    )
    # 计算需求响应时段的放电量(根据响应容量计算的)
    response_period_discharge_power = get_discharge_power(df_strategy_period, response_period)
    # 需求响应时段实际可放电量估计调整
    df_strategy_period = response_period_readjust(
        df_strategy_period, response_period, peak_discharge_power, 
        response_period_discharge_power, max_discharge_load, max_charge_load,
    )
    # 需求响应时段前、后 1 小时时段内电池冷静期策略调整
    df_strategy_period = after_response_strategy(
        df_strategy_period, response_before_1h_period, response_after_1h_period, response_period, freq
    )
    return df_strategy_period

def strategy_adjust_model_2(df_strategy_period: pd.DataFrame, 
                            baseline_coef_period: Dict, 
                            climbing_period: Dict,
                            response_period: Dict,
                            response_before_1h_period: Dict,
                            response_after_1h_period: Dict,
                            response_capacity: float, 
                            max_discharge_load: float,
                            max_charge_load: float,
                            peak_time_period: float,
                            peak_discharge_power: float,
                            climbing_period_discharge_power: float,
                            baseline_coef_period_discharge_power: float,
                            baseline_coef_period_discharge_time_len: float,
                            freq: str):
    # 爬坡时段为放电时段
    if climbing_period_discharge_power > 0:
        # 需求响应时段理论可放电量策略调整、基线调整系数修正
        df_strategy_period = df_strategy_period = before_response_strategy(
            df_strategy_period, baseline_coef_period, response_period, 
            response_capacity, max_discharge_load, max_charge_load,
        )
        # with pd.option_context('display.max_rows', None, 'display.max_columns', None):
        #     logger.info(f'debug::df_strategy_period: \n{df_strategy_period[["time", "strategy_load"]]}')

        # TODO 计算需求响应时段的放电量(根据响应容量计算的)
        response_period_discharge_power = get_discharge_power(df_strategy_period, response_period)
        logger.info(f"debug::response_period_discharge_power: {response_period_discharge_power}")

        # 根据需求响应时段的所需电量，调整响应策略
        logger.info(f"debug::baseline_coef_period_discharge_power: {baseline_coef_period_discharge_power}")
        if response_period_discharge_power <= baseline_coef_period_discharge_power:
            # 需求响应时段前、后 1 小时时段内电池冷静期策略调整
            df_strategy_period = after_response_strategy(
                df_strategy_period, response_before_1h_period, response_after_1h_period, response_period, freq,
            )
            return df_strategy_period
        elif response_period_discharge_power > baseline_coef_period_discharge_power:
            logger.info(f"debug::需求响应时段的所需电量大于基线调整系数时段的放电量")
            # 将爬坡阶段的充电策略修改为待机
            df_strategy_period = climbing_period_adjust(
                df_strategy_period, climbing_period,
            )
            # with pd.option_context('display.max_rows', None, 'display.max_columns', None):
            #     logger.info(f'debug::df_strategy_period: \n{df_strategy_period[["time", "strategy_load"]]}')
            # 需求响应时段前、后 1 小时时段内电池冷静期策略调整
            logger.info(f"debug::response_before_1h_period: {response_before_1h_period}")
            logger.info(f"debug::response_after_1h_period: {response_after_1h_period}")
            df_strategy_period = after_response_strategy(
                df_strategy_period, response_before_1h_period, response_after_1h_period, response_period, freq,
            )
            return df_strategy_period
    # 爬坡时段不是放电时段
    else:
        # 需求响应时段理论可放电量策略调整、基线调整系数修正
        df_strategy_period = before_response_strategy(
            df_strategy_period, baseline_coef_period, response_period, 
            response_capacity, max_discharge_load, max_charge_load,
        )
        # 计算需求响应时段的放电量(根据响应容量计算的)
        response_period_discharge_power = get_discharge_power(df_strategy_period, response_period)
        # 根据需求响应时段的所需电量，调整响应策略
        if response_period_discharge_power <= baseline_coef_period_discharge_power:
            # 需求响应时段前、后 1 小时时段内电池冷静期策略调整
            df_strategy_period = after_response_strategy(
                df_strategy_period, response_before_1h_period, response_after_1h_period, response_period, freq,
            )
            return df_strategy_period
        elif response_period_discharge_power > baseline_coef_period_discharge_power:
            # 第一个峰时放电时段策略调整
            df_strategy_period = discharge_period_adjust(
                df_strategy_period, 
                peak_time_period,
                peak_discharge_power,
                response_period_discharge_power,
                baseline_coef_period_discharge_power,
                baseline_coef_period_discharge_time_len,
            )
            # 需求响应时段前、后 1 小时时段内电池冷静期策略调整
            df_strategy_period = after_response_strategy(
                df_strategy_period, response_before_1h_period, response_after_1h_period, response_period, freq,
            )
            return df_strategy_period

def strategy_adjust_model_3(df_strategy_period: pd.DataFrame, 
                            peak_time_period: Dict,
                            response_period: Dict,
                            response_before_1h_period: Dict,
                            response_after_1h_period: Dict,
                            peak_discharge_power: float,
                            response_period_discharge_power: float,
                            remain_power_before_response: float,
                            baseline_coef_period_discharge_power: float,
                            baseline_coef_period_discharge_time_len: float,
                            freq: str):
    # 待机需要取消的放电量
    peak1_adjustable_power = response_period_discharge_power - remain_power_before_response
    # 第一个峰时放电时段策略调整
    df_strategy_period = discharge_period_adjust(
        df_strategy_period,
        peak_time_period,
        peak_discharge_power,
        peak1_adjustable_power,
        baseline_coef_period_discharge_power,  # 0
        baseline_coef_period_discharge_time_len,  # 0
    )
    # 需求响应时段前、后 1 小时时段内电池冷静期策略调整
    df_strategy_period = after_response_strategy(
        df_strategy_period, response_before_1h_period, response_after_1h_period, response_period, freq,
    )

    return df_strategy_period

def compare_strategy_profit(df_strategy_period_raw: pd.DataFrame, 
                            df_strategy_period_1: pd.DataFrame, 
                            df_strategy_period_2: pd.DataFrame, 
                            response_capacity: float, 
                            clearing_price: float):
    # 计算上述两种策略的收益提升，并作出决策
    strategy_profit_1, response_profit_1, \
    response_strategy_profit_1, profit_improve_1 = calc_profit(
        df_strategy_period_raw, 
        df_strategy_period_1, 
        response_capacity, 
        response_capacity,
        clearing_price,
    )
    strategy_profit_2, response_profit_2, \
    response_strategy_profit_2, profit_improve_2 = calc_profit(
        df_strategy_period_raw, 
        df_strategy_period_2, 
        response_capacity, 
        response_capacity,
        clearing_price,
    )
    df_strategy_period = df_strategy_period_1 \
        if profit_improve_1 >= profit_improve_2 else df_strategy_period_2 
    
    return df_strategy_period
# -----------------------------------------------------------------------------
def strategy_adjust_model(df_strategy_period: pd.DataFrame, 
                          baseline_coef_period: Dict, 
                          climbing_period: Dict, 
                          response_period: Dict,
                          response_before_1h_period: Dict,
                          response_after_1h_period: Dict,
                          response_capacity: float, 
                          battery_capacity: float, 
                          clearing_price: float,
                          max_discharge_load: float,
                          max_charge_load: float,
                          freq: str):
    """
    放电响应策略
    """
    # ------------------------------
    # 获取无需求响应时段的信息
    # ------------------------------
    # 无需求响应策略
    df_strategy_period_raw = df_strategy_period[["time", "strategy_load"]].copy()
    # with pd.option_context('display.max_rows', None, 'display.max_columns', None):
    #     logger.info(f"debug::df_strategy_period_raw: \n{df_strategy_period_raw}")
    # 响应日期
    response_date = pd.to_datetime(response_period["start"]).date()
    logger.info(f"debug::response_date: {response_date}")
    # 获取无需求响应时段的信息
    (
        peak1_time_period, peak1_discharge_power, 
        peak2_time_period, peak2_discharge_power,
        baseline_coef_period_discharge_power, baseline_coef_period_discharge_time_len,
        climbing_period_discharge_power, climbing_period_discharge_time_len,
        flat_time_period, flat_charge_power,
        remain_power_before_response,
    ) = get_strategy_info(
        response_date, 
        df_strategy_period,
        baseline_coef_period,
        climbing_period,
        response_before_1h_period,
        battery_capacity,
    )
    # ------------------------------
    # 规则准备
    # ------------------------------
    # 第一个峰时段可放电量 减去 基线调整系数样本所在时段的放电量
    delta_discharge_power_1 = peak1_discharge_power - baseline_coef_period_discharge_power
    logger.info(f"debug::delta_discharge_power_1: {delta_discharge_power_1}")
    # 第二个峰时段可放电量 减去 基线调整系数样本所在时段的放电量
    delta_discharge_power_2 = peak2_discharge_power - baseline_coef_period_discharge_power
    logger.info(f"debug::delta_discharge_power_2: {delta_discharge_power_2}")
    # ------------------------------
    # 规则
    # ------------------------------
    # 1. 基线调整系数样本所在时段的放电量 == 峰时段放电量
    if (response_period["start"] >= pd.to_datetime(f"{response_date} 10:00:00") and 
        response_period["start"] <= pd.to_datetime(f"{response_date} 11:00:00")) \
        and delta_discharge_power_1 == 0:
        logger.info(f"debug::{'-' * 60}")
        logger.info(f"debug::1.【基线调整系数样本所在时段的放电量 == 峰时段放电量】")
        logger.info(f"debug::{'-' * 60}")
        df_strategy_period = strategy_adjust_model_1(
            df_strategy_period,
            baseline_coef_period, response_period,
            response_before_1h_period, response_after_1h_period,
            response_capacity, max_discharge_load, max_charge_load,
            peak1_discharge_power, freq,
        )
        return df_strategy_period
    # 2. 基线调整系数样本所在时段的放电量 == 峰时段放电量
    if (response_period["start"] >= pd.to_datetime(f"{response_date} 21:00:00") and 
        response_period["start"] <= pd.to_datetime(f"{response_date} 22:00:00")) \
        and delta_discharge_power_2 == 0:
        logger.info(f"debug::{'-' * 60}")
        logger.info(f"debug::2.【基线调整系数样本所在时段的放电量 == 峰时段放电量】")
        logger.info(f"debug::{'-' * 60}")
        df_strategy_period = strategy_adjust_model_1(
            df_strategy_period,
            baseline_coef_period, response_period,
            response_before_1h_period, response_after_1h_period,
            response_capacity, max_discharge_load, max_charge_load,
            peak2_discharge_power, freq,
        )
        return df_strategy_period
    # -----------------------------------------------------------------------------
    # 3. 0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量
    if (response_period["start"] >= pd.to_datetime(f"{response_date} 10:00:00") and 
        response_period["start"] <= pd.to_datetime(f"{response_date} 12:30:00")) \
        and (delta_discharge_power_1 > 0 and (delta_discharge_power_1 < peak1_discharge_power)):
        logger.info(f"debug::{'-' * 60}")
        logger.info(f"debug::3.【0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量】")
        logger.info(f"debug::{'-' * 60}")
        df_strategy_period = strategy_adjust_model_2(
            df_strategy_period,
            baseline_coef_period,
            climbing_period,
            response_period,
            response_before_1h_period,
            response_after_1h_period,
            response_capacity,
            max_discharge_load,
            max_charge_load,
            peak1_time_period,
            peak1_discharge_power,
            climbing_period_discharge_power,
            baseline_coef_period_discharge_power,
            baseline_coef_period_discharge_time_len,
            freq,
        )
        return df_strategy_period
    # 4. 0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量
    if (response_period["start"] >= pd.to_datetime(f"{response_date} 21:00:00") and 
        response_period["start"] <= pd.to_datetime(f"{response_date} 22:00:00")) \
       and (delta_discharge_power_2 > 0 and (delta_discharge_power_2 < peak2_discharge_power)):
        logger.info(f"debug::{'-' * 60}")
        logger.info(f"debug::4.【0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量】")
        logger.info(f"debug::{'-' * 60}")
        df_strategy_period = strategy_adjust_model_2(
            df_strategy_period,
            baseline_coef_period,
            climbing_period,
            response_period,
            response_before_1h_period,
            response_after_1h_period,
            response_capacity,
            max_discharge_load,
            max_charge_load,
            peak2_time_period,
            peak2_discharge_power,
            climbing_period_discharge_power,
            baseline_coef_period_discharge_power,
            baseline_coef_period_discharge_time_len,
            freq,
        )
        return df_strategy_period
    # -----------------------------------------------------------------------------
    # 5. 基线调整系数样本所在时段的放电量 == 0
    if delta_discharge_power_1 == peak1_discharge_power:
        logger.info(f"debug::{'-' * 60}")
        logger.info(f"debug::5.【基线调整系数样本所在时段的放电量 == 0】")
        logger.info(f"debug::{'-' * 60}")
        # 3.1 需求响应时段理论可放电量策略调整、基线调整系数修正
        df_strategy_period = before_response_strategy(
            df_strategy_period, baseline_coef_period, response_period, 
            response_capacity, max_discharge_load, max_charge_load,
        )
        # 计算需求响应时段的放电量(根据响应容量计算的)
        response_period_discharge_power = get_discharge_power(df_strategy_period, response_period)
        # 计算需求响应时段的充电量(根据响应容量计算的)
        response_period_charge_power = get_charge_power(df_strategy_period, response_period)
        
        # 3.2 需求响应时段前、后 1 小时时段内电池冷静期策略调整
        df_strategy_period = after_response_strategy(
            df_strategy_period, response_before_1h_period, response_after_1h_period, response_period, freq,
        )
        # 计算响应时段开始前一小时时刻的电池剩余电量
        remain_power_before_response = get_remain_power(df_strategy_period, response_before_1h_period["start"], battery_capacity)
        
        # 3.3 充电响应
        if response_period_charge_power < 0.0:
            return df_strategy_period
        # 3.4 放电响应
        elif response_period_discharge_power > 0.0:
            if peak1_discharge_power > response_period_discharge_power:
                if remain_power_before_response >= response_period_discharge_power:
                    # (1) 第一个峰时放电部分全部不做修改，并且平时进行部分充电，充电功量能够达到`响应所需电量`
                    return df_strategy_period
                elif remain_power_before_response < response_period_discharge_power:
                    # (2)第一个峰时放电部分修改为待机，并且平时进行部分充电：待机取消的放电量+充电电量=响应所需电量
                    df_strategy_period = strategy_adjust_model_3(
                        df_strategy_period, 
                        peak1_time_period,
                        response_period,
                        response_before_1h_period,
                        response_after_1h_period,
                        peak1_discharge_power,
                        response_period_discharge_power,
                        remain_power_before_response,
                        baseline_coef_period_discharge_power,
                        baseline_coef_period_discharge_time_len,
                        freq
                    )
                    return df_strategy_period
            elif peak1_discharge_power == response_period_discharge_power:
                if remain_power_before_response >= response_period_discharge_power:
                    # (1) 第一个峰时放电部分全部不做修改，并且平时进行部分充电，充电功量能够达到`响应所需电量`
                    return df_strategy_period
                elif remain_power_before_response < response_period_discharge_power:
                    # (2) 第一个峰时放电全部修改为待机，并且平时不能充电：可调负荷为峰时放电量
                    # 第一个峰时放电时段策略调整
                    df_strategy_period_1 = discharge_period_adjust(
                        df_strategy_period,
                        peak1_time_period,
                        peak1_discharge_power,
                        response_period_discharge_power,
                        baseline_coef_period_discharge_power,  # 0
                        baseline_coef_period_discharge_time_len,  # 0
                    )
                    #平时充电时段策略调整
                    df_strategy_period_1 = charge_period_adjust(df_strategy_period_1, flat_time_period)
                    # 需求响应时段前、后 1 小时时段内电池冷静期策略调整
                    df_strategy_period_1 = after_response_strategy(
                        df_strategy_period_1, response_before_1h_period, response_after_1h_period, response_period, freq,
                    )

                    # (3) 第一个峰时放电部分修改为待机，并且平时进行部分充电：待机取消的放电量+充电电量=响应所需电量
                    df_strategy_period_2 = strategy_adjust_model_3(
                        df_strategy_period, 
                        peak1_time_period,
                        response_period,
                        response_before_1h_period,
                        response_after_1h_period,
                        peak1_discharge_power,
                        response_period_discharge_power,
                        remain_power_before_response,
                        baseline_coef_period_discharge_power,
                        baseline_coef_period_discharge_time_len,
                        freq
                    )

                    # 计算上述两种策略的收益提升，并作出决策
                    df_strategy_period = compare_strategy_profit(
                        df_strategy_period_raw, 
                        df_strategy_period_1, 
                        df_strategy_period_2, 
                        response_capacity, 
                        clearing_price,
                    )
                    return df_strategy_period
            elif peak1_discharge_power < response_period_discharge_power:
                # 需求响应时段实际可放电量估计调整
                df_strategy_period = response_period_readjust(
                    df_strategy_period, response_period, peak1_discharge_power, 
                    response_period_discharge_power, max_discharge_load, max_charge_load,
                )
                return df_strategy_period




# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
