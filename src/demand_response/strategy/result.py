from datetime import timedelta
from typing import Callable, Dict

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response_optim.engine.response_profit import calc_profit
from model.model_packages.Demand_Response_optim.strategy.core import (
    get_discharge_power,
    get_remain_power,
)
from utils.log_util import logger


def compare_strategy_profit(
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_1: pd.DataFrame,
    df_strategy_period_2: pd.DataFrame,
    response_capacity: float,
    clearing_price: float,
):
    profit_df_1 = calc_profit(
        df_strategy_period_raw,
        df_strategy_period_1,
        response_capacity,
        response_capacity,
        clearing_price,
    )
    profit_improve_1 = profit_df_1["加入需求响应后的收益提升"].values[0]
    logger.info(f"debug::profit_improve_1: {profit_improve_1}")

    profit_df_2 = calc_profit(
        df_strategy_period_raw,
        df_strategy_period_2,
        response_capacity,
        response_capacity,
        clearing_price,
    )
    profit_improve_2 = profit_df_2["加入需求响应后的收益提升"].values[0]
    logger.info(f"debug::profit_improve_2: {profit_improve_2}")

    return df_strategy_period_2 if profit_improve_2 >= profit_improve_1 else df_strategy_period_1


def find_first_strategy_change_time(df_strategy_period_raw: pd.DataFrame, df_strategy_period_new: pd.DataFrame):
    df_compare = df_strategy_period_raw[["time"]].copy()
    df_compare["strategy_load_raw"] = df_compare["time"].map(
        df_strategy_period_raw.set_index("time")["strategy_load"]
    )
    df_compare["strategy_load_new"] = df_compare["time"].map(
        df_strategy_period_new.set_index("time")["strategy_load"]
    )
    df_compare["diff"] = df_compare.apply(lambda x: x["strategy_load_raw"] - x["strategy_load_new"], axis=1)
    changed_times = df_compare.loc[df_compare["diff"] != 0, "time"].values
    if len(changed_times) == 0:
        logger.info("debug::first_strategy_change_time: None")
        return None
    first_strategy_change_time = np.nanmin(changed_times)
    logger.info(f"debug::first_strategy_change_time: {first_strategy_change_time}")
    return first_strategy_change_time


def profit_output(
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_new: pd.DataFrame,
    period_map: Dict,
    peak1_discharge_load: float,
    response_capacity: float,
    clearing_price: float,
    discharge_period_adjust_after_fn: Callable,
):
    df_strategy_period_temp = df_strategy_period_new.copy()
    if period_map["response"]["start"] < period_map["peak2_discharge"]["start"]:
        remain_power_period = {
            "start": period_map["strategy"]["start"],
            "end": period_map["peak2_discharge"]["start"] - timedelta(minutes=5),
        }
        remain_power_before_peak2 = get_remain_power(df_strategy_period_temp, remain_power_period)
        logger.info(f"debug::remain_power_before_peak2: {remain_power_before_peak2} kWh")
        peak2_discharge_power_new = get_discharge_power(df_strategy_period_temp, period_map["peak2_discharge"])
        logger.info(f"debug::peak2_discharge_power_new: {peak2_discharge_power_new} kWh")
        if remain_power_before_peak2 < peak2_discharge_power_new:
            df_strategy_period_temp = discharge_period_adjust_after_fn(
                df_strategy_period_temp,
                period_map["peak2_discharge"],
                peak1_discharge_load,
                peak2_discharge_power_new - remain_power_before_peak2,
            )
    return calc_profit(
        df_strategy_period_raw,
        df_strategy_period_temp,
        response_capacity,
        response_capacity,
        clearing_price,
    )


def simulate_peak2_discharge_for_profit(
    df_strategy_period_temp: pd.DataFrame,
    period_map: Dict,
    peak1_discharge_load: float,
    remain_power_before_peak2: float,
    discharge_period_adjust_after_fn: Callable,
    peak2_min_floor: float = None,
):
    peak2_discharge_power_new = get_discharge_power(df_strategy_period_temp, period_map["peak2_discharge"])
    if peak2_min_floor is not None:
        peak2_discharge_power_new = max(peak2_discharge_power_new, peak2_min_floor)
    logger.info(f"debug::peak2_discharge_power_new: {peak2_discharge_power_new} kWh")
    if remain_power_before_peak2 < peak2_discharge_power_new:
        logger.info("debug::remain_power_before_peak2 < peak2_discharge_power_new, 进行第二个峰时放电时段模拟策略调整...")
        return discharge_period_adjust_after_fn(
            df_strategy_period_temp,
            period_map["peak2_discharge"],
            peak1_discharge_load,
            peak2_discharge_power_new - remain_power_before_peak2,
        )
    logger.info("debug::remain_power_before_peak2 >= peak2_discharge_power_new, 不进行第二个峰时放电时段模拟策略调整...")
    return df_strategy_period_temp
