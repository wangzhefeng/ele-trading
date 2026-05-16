from datetime import datetime, timedelta
from typing import Callable, Dict

import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.core import (
    get_charge_power,
    get_remain_power,
)
from utils.log_util import logger


def apply_canceled_peak_response(
    df_strategy_raw: pd.DataFrame,
    df_strategy_copy: pd.DataFrame,
    peak_period: Dict,
    current_time: datetime,
    period_map: Dict,
    max_discharge_load: float,
    max_charge_load: float,
    freq: str,
    discharge_period_adjust_fn: Callable,
    response_period_adjust_discharge_fn: Callable,
    response_period_adjust_standby_discharge_fn: Callable,
    after_response_strategy_fn: Callable,
) -> pd.DataFrame:
    """Reuse canceled peak discharge energy for the response window."""
    df_strategy_new_temp, _ = discharge_period_adjust_fn(
        df_strategy_raw,
        peak_period,
        current_time,
    )
    df_strategy_new_temp = response_period_adjust_discharge_fn(
        df_strategy_new_temp,
        period_map["response"],
    )
    df_strategy_new_temp = after_response_strategy_fn(
        df_strategy_new_temp,
        period_map["response_before_1h"],
        period_map["response_after_1h"],
        period_map["response"],
        freq,
    )
    remain_power_period = {
        "start": period_map["strategy"]["start"],
        "end": period_map["response"]["start"] - timedelta(minutes=5),
    }
    remain_power_before_response = get_remain_power(
        df_strategy_new_temp, remain_power_period
    )
    logger.info(f"debug::remain_power_before_response: {remain_power_before_response} kWh")
    logger.info("debug::使用剩余的电量进行需求响应...")
    df_strategy_new, canceled_discharge_power = discharge_period_adjust_fn(
        df_strategy_copy,
        peak_period,
        current_time,
    )
    df_strategy_new = response_period_adjust_standby_discharge_fn(
        df_strategy_new,
        period_map["response"],
        canceled_discharge_power,
        max_discharge_load,
        max_charge_load,
    )
    return after_response_strategy_fn(
        df_strategy_new,
        period_map["response_before_1h"],
        period_map["response_after_1h"],
        period_map["response"],
        freq,
    )


def calc_midday_response_remain_power(
    df_strategy_new_temp: pd.DataFrame,
    response_date,
    response_start,
) -> float:
    remain_power_period = {
        "start": pd.to_datetime(f"{response_date} 11:00:00"),
        "end": response_start - timedelta(minutes=5),
    }
    remain_power_before_response = get_charge_power(
        df_strategy_new_temp, remain_power_period
    )
    remain_power_before_response = abs(remain_power_before_response)
    logger.info(f"debug::remain_power_before_response: {remain_power_before_response} kWh")
    return remain_power_before_response
