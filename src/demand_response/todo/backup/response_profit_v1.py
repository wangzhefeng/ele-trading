from typing import Dict
from datetime import datetime

import numpy as np
import pandas as pd


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
        df_strategy_period["time"] == stop_time, "soc"
    ].values[0]
    remain_power = battery_capacity * battery_soc

    return remain_power

def get_response_price_adjust_coef(actual_response_capacity: float, 
                                   target_response_capacity: float) -> float:
    """
    响应量偏差补偿价格调整表（开关型）

    Args:
        actual_response_capacity (float): 实际响应量
        plan_response_capacity (float): 申报响应规模(出清响应规模)

    Returns:
        float: 价格调整系数
    """
    # 响应量系数
    response_capacity_coef = actual_response_capacity / target_response_capacity
    # 价格调整系数
    if response_capacity_coef < 0.6:
        response_price_adjust_coef = 0.0
    elif response_capacity_coef >= 0.6 and response_capacity_coef < 0.8:
        response_price_adjust_coef = 0.8
    elif response_capacity_coef >= 0.8 and response_capacity_coef <= 1.2:
        response_price_adjust_coef = 1.0
    elif response_capacity_coef > 1.2 and response_capacity_coef <= 1.4:
        response_price_adjust_coef = 0.8
    elif response_capacity_coef > 1.4:  # 响应量按照邀约量 140% 计算
        response_price_adjust_coef = 0.6

    return response_price_adjust_coef

def response_success_ratio(response_capacity: float, 
                           response_time_len: float, 
                           df_baseline_load: pd.DataFrame, 
                           df_response_load: pd.DataFrame) -> float:
    """
    计算需求响应准确率
    
    Args:
        df_baseline_load (pd.DataFrame): 基线负荷
        df_response_load (pd.DataFrame): 响应时段负荷
        response_capacity (float): 响应容量
        response_time_len (float): 响应时长

    Returns:
        float: 需求响应准确率
    """
    target_response_load = response_capacity / response_time_len
    success_ratio = (df_baseline_load["value"] - df_response_load["value"]) / target_response_load

    return success_ratio

def __calc_demand_power(df_load: pd.DataFrame):
    """
    根据外表/内表功率计算需量功率
    """
    demand_power = df_load["value"].max()

    return demand_power

def calc_demand_cost(df_demand_load_inner: float, 
                     df_demand_load_outer: float, 
                     demand_load_price: float=38.4):
    """
    计算需量抬升成本
    """
    # 外表(关口表/AIDC用电总负荷)最大负荷
    demand_load_outer = __calc_demand_power(df_demand_load_outer)
    # 内表(AIDC 不包含储能功率的用电负荷)最大负荷
    demand_load_inner = __calc_demand_power(df_demand_load_inner)
    # 抬升成本
    demand_cost = (demand_load_outer - demand_load_inner) * demand_load_price

    return demand_cost

def calc_strategy_benefit(df_strategy_period: pd.DataFrame, 
                          load_col: str, 
                          freq: str) -> pd.DataFrame:
    """
    计算无需求响应/需求响应干预的 "响应日前一日22:00:00~响应日22:00:00" 时段的储能削峰填谷收益

    Args:
        df_strategy_period (pd.DataFrame): 储能策略功率
        freq (str): 数据频率

    Returns:
        _type_: _description_
    """
    # strategy_period_df = pd.DataFrame({
    #     "time": pd.date_range(f"{response_date - timedelta(days=1)} 22:00:00", f"{response_date} 21:59:59", freq=freq)
    # })
    df = df_strategy_period.copy()
    df["charge_cost"] = df.apply(
        lambda x: (int(freq[:-3]) / 60) * np.array(x[load_col]) * np.array(x["ele_price"]) 
        if x[load_col] < 0.0 else 0.0, 
        axis=1
    )
    df["discharge_benefit"] = df.apply(
        lambda x: (int(freq[:-3]) / 60) * np.array(x[load_col]) * np.array(x["ele_price"]) 
        if x[load_col] > 0.0 else 0.0, 
        axis=1
    )
    df["strategy_benefit"]  = df.apply(
        lambda x: x["charge_cost"] + x["discharge_benefit"], 
        axis=1
    )

    return df

def calc_ES_benefit(df_strategy_period: pd.DataFrame, 
                    load_col: str, 
                    freq: str="5min", 
                    demand_load_price: float=None):
    """
    计算储能收益
    """
    # 储能充放电收益
    df_strategy_benefit = calc_strategy_benefit(
        df_strategy_period[["time", load_col, "ele_price"]], 
        load_col,
        freq=freq
    )
    # df_strategy_benefit["date"] = pd.to_datetime(df_strategy_benefit["time"]).dt.date
    # res = df_strategy_benefit.groupby(["date"]).sum(["strategy_benefit"])
    # res = res["strategy_benefit"]

    # 需量抬升成本
    if demand_load_price is None:
        demand_cost = 0.0
    else:
        demand_cost = calc_demand_cost(
            df_strategy_period[["time", "demand_load"]], 
            df_strategy_period[["time", "aidc_load"]], 
            demand_load_price=38.4,
        )
    
    # 储能总收益
    benefit = df_strategy_benefit["strategy_benefit"].sum() - demand_cost
    
    return benefit

def calc_DR_profit(actual_response_capacity: float, 
                   target_response_capacity: float, 
                   clearing_price: float):
    # 价格调整系数(价格浮动系数)
    price_adjust_coef = get_response_price_adjust_coef(
        actual_response_capacity, 
        target_response_capacity
    )
    # 预估需求响应总收益
    response_profit = actual_response_capacity * clearing_price * price_adjust_coef
    
    return response_profit

def calc_profit(df_strategy_period: pd.DataFrame, 
                df_strategy_period_response: pd.DataFrame, 
                # response_period: Dict,
                # battery_capacity: float,
                # peak_ele_price: float,
                actual_response_capacity: float, 
                target_response_capacity: float,
                clearing_price: float) -> float:
    """
    储能需求响应前后收益提升

    Args:
        df_strategy_period (pd.DataFrame): 无需求响应策略、无需求响应电价
        df_strategy_period_response (pd.DataFrame): 需求响应策略、无需求响应电价
        actual_response_capacity (float): 实际响应容量
        target_response_capacity (float): 目标响应容量
        clearing_price (float): 统一出清价格

    Returns:
        float: 储能需求响应前后收益提升
    """
    # 削峰填谷理论收益
    strategy_profit = calc_ES_benefit(
        df_strategy_period, load_col="strategy_load", freq="5min"
    )
    # 需求响应收益
    response_profit = calc_DR_profit(
        actual_response_capacity, target_response_capacity, clearing_price
    )
    # 需求响应后削峰填谷理论收益# 响应日期
    # response_date = pd.to_datetime(response_period["start"]).date()
    # remain_power = get_remain_power(
    #     df_strategy_period_response, 
    #     pd.to_datetime(f"{response_date} 22:00:00"), 
    #     battery_capacity
    # )
    # remain_power_cost = remain_power * peak_ele_price
    response_strategy_profit = calc_ES_benefit(
        df_strategy_period_response, load_col="strategy_load", freq="5min"
    ) # TODO remain_power_cost
    # 收益提升
    profit_improve = (response_profit + response_strategy_profit) - strategy_profit

    return (
        strategy_profit, 
        response_profit, 
        response_strategy_profit, 
        profit_improve
    )




# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
