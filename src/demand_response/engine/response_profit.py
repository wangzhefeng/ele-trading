from datetime import datetime
from typing import Dict

import numpy as np
import pandas as pd

from utils.log_util import logger


def get_response_price_adjust_coef(actual_response_capacity: float, target_response_capacity: float) -> float:
    """
    根据完成率计算结算价格折扣系数。
    """
    response_capacity_coef = actual_response_capacity / target_response_capacity
    if response_capacity_coef < 0.6:
        response_price_adjust_coef = 0.0
    elif response_capacity_coef >= 0.6 and response_capacity_coef < 0.8:
        response_price_adjust_coef = 0.8
    elif response_capacity_coef >= 0.8 and response_capacity_coef <= 1.2:
        response_price_adjust_coef = 1.0
    elif response_capacity_coef > 1.2 and response_capacity_coef <= 1.4:
        response_price_adjust_coef = 0.8
    else:
        response_price_adjust_coef = 0.6
    return response_price_adjust_coef


def get_response_price(response_type: str, current_time: datetime, response_period: Dict) -> float:
    """
    根据响应类型和通知提前量计算需求响应价格。
    """
    if response_type.strip() == "削峰":
        response_base_price = 3.0
    elif response_type.strip() == "填谷":
        response_base_price = 1.2
    else:
        response_base_price = 3.0

    notice_time = (response_period["start"] - current_time).total_seconds() / 3600
    logger.info(f"debug::notice_time: {notice_time} h")
    if notice_time > 0.0 and notice_time <= 0.5:
        price_coef = 2.0
    elif notice_time > 0.5 and notice_time <= 2.0:
        price_coef = 1.5
    elif notice_time > 2.0 and notice_time <= 8.0:
        price_coef = 1.0
    elif notice_time > 8.0 and notice_time <= 24.0:
        price_coef = 0.9
    elif notice_time > 24.0:
        price_coef = 0.8
    else:
        price_coef = 3.0

    response_price = np.round(response_base_price * price_coef, 2)
    logger.info(f"debug::response_price: {response_price} 元/kWh")
    return response_price


def response_success_ratio(
    response_capacity: float,
    response_time_len: float,
    df_baseline_load: pd.DataFrame,
    df_response_load: pd.DataFrame,
) -> float:
    """
    计算每个时段的响应完成率。
    """
    target_response_load = response_capacity / response_time_len
    return (df_baseline_load["value"] - df_response_load["value"]) / target_response_load


def _calc_demand_power(df_load: pd.DataFrame):
    """
    提取需量电费结算口径下的最大功率。
    """
    return df_load["value"].max()


def calc_demand_cost(df_demand_load_inner: float, df_demand_load_outer: float, demand_load_price: float = 38.4):
    """
    估算策略变化带来的需量电费变化。
    """
    demand_load_outer = _calc_demand_power(df_demand_load_outer)
    demand_load_inner = _calc_demand_power(df_demand_load_inner)
    return (demand_load_outer - demand_load_inner) * demand_load_price


def calc_strategy_benefit(df_strategy_period: pd.DataFrame, load_col: str, freq: str) -> pd.DataFrame:
    """
    按时段计算储能策略自身的充电成本和放电收益。
    """
    df = df_strategy_period.copy()
    df["charge_cost"] = df.apply(
        lambda x: (int(freq[:-3]) / 60) * np.array(x[load_col]) * np.array(x["ele_price"])
        if x[load_col] < 0.0
        else 0.0,
        axis=1,
    )
    df["discharge_benefit"] = df.apply(
        lambda x: (int(freq[:-3]) / 60) * np.array(x[load_col]) * np.array(x["ele_price"])
        if x[load_col] > 0.0
        else 0.0,
        axis=1,
    )
    df["strategy_benefit"] = df.apply(lambda x: x["charge_cost"] + x["discharge_benefit"], axis=1)
    return df


def calc_ES_benefit(
    df_strategy_period: pd.DataFrame,
    load_col: str,
    freq: str = "5min",
    demand_load_price: float = None,
):
    """
    计算储能策略在电能收益和需量成本后的综合收益。
    """
    df_strategy_benefit = calc_strategy_benefit(
        df_strategy_period[["time", load_col, "ele_price"]],
        load_col,
        freq=freq,
    )
    if demand_load_price is None:
        demand_cost = 0.0
    else:
        demand_cost = calc_demand_cost(
            df_strategy_period[["time", "demand_load"]],
            df_strategy_period[["time", "aidc_load"]],
            demand_load_price=38.4,
        )
    return df_strategy_benefit["strategy_benefit"].sum() - demand_cost


def calc_DR_profit(actual_response_capacity: float, target_response_capacity: float, clearing_price: float):
    """
    计算需求响应本身的结算收益。
    """
    price_adjust_coef = get_response_price_adjust_coef(actual_response_capacity, target_response_capacity)
    return actual_response_capacity * (clearing_price * price_adjust_coef)


def calc_profit(
    df_strategy_period: pd.DataFrame,
    df_strategy_period_response: pd.DataFrame,
    actual_response_capacity: float,
    target_response_capacity: float,
    clearing_price: float,
) -> float:
    """
    汇总原策略收益、响应后策略收益和需求响应收益。
    """
    strategy_profit = calc_ES_benefit(df_strategy_period, load_col="strategy_load", freq="5min")
    response_profit = calc_DR_profit(actual_response_capacity, target_response_capacity, clearing_price)
    response_strategy_profit = calc_ES_benefit(
        df_strategy_period_response, load_col="strategy_load", freq="5min"
    )
    profit_improve = (response_profit + response_strategy_profit) - strategy_profit
    profit_df = pd.DataFrame(
        {
            "无需求响应策略收益": np.round(strategy_profit, 4),
            "需求响应调整后策略收益": np.round(response_strategy_profit, 4),
            "需求响应收益": np.round(response_profit, 4),
            "加入需求响应后的收益提升": np.round(profit_improve, 4),
        },
        index=range(1),
    )
    logger.info(f"debug::profit_df: \n{profit_df}")
    return profit_df
