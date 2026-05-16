from typing import Callable, Dict

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.core import (
    get_discharge_load,
    get_discharge_power,
    get_discharge_time_len,
    get_response_time_len,
)
from utils.log_util import logger


def response_period_adjust(
    df_strategy_period: pd.DataFrame,
    time_period: Dict,
    response_capacity: float,
    max_discharge_load: float,
    max_charge_load: float,
) -> pd.DataFrame:
    """Adjust strategy load within the response window."""
    logger.info("debug::需求响应时段内充放电策略的调整...")
    response_time_len = get_response_time_len(response_period=time_period)
    response_load = response_capacity / response_time_len
    response_load = np.nanmin([response_load, max_discharge_load])
    response_load = np.nanmax([response_load, max_charge_load])
    logger.info(f"debug::response_capacity: {response_capacity}")
    logger.info(f"debug::response_time_len: {response_time_len}")
    logger.info(f"debug::response_load: {response_load}")
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    df_strategy_period.loc[period_mask, "strategy_load"] = df_strategy_period.loc[
        period_mask, "strategy_load"
    ].apply(lambda x: x + response_load)
    return df_strategy_period


def response_period_readjust(
    df_strategy_period: pd.DataFrame,
    time_period: Dict,
    peak_discharge_power: float,
    response_discharge_power: float,
    max_discharge_load: float,
    max_charge_load: float,
) -> pd.DataFrame:
    """Readjust response strategy if available discharge is insufficient."""
    discharge_power_remain = peak_discharge_power - response_discharge_power
    if discharge_power_remain < 0.0:
        return response_period_adjust(
            df_strategy_period=df_strategy_period,
            time_period=time_period,
            response_capacity=peak_discharge_power,
            max_discharge_load=max_discharge_load,
            max_charge_load=max_charge_load,
        )
    return df_strategy_period


def get_strategy_info(df_strategy_period: pd.DataFrame, period_map: Dict):
    """Collect discharge stats used by response strategy rules."""
    peak1_discharge_power = get_discharge_power(
        df_strategy_period, period_map["peak1_discharge"]
    )
    logger.info(f"debug::peak1_discharge_power: {peak1_discharge_power} kWh")
    peak1_discharge_load = get_discharge_load(
        df_strategy_period, period_map["peak1_discharge"]
    )
    logger.info(f"debug::peak1_discharge_load: {peak1_discharge_load} kW")
    peak2_discharge_power = get_discharge_power(
        df_strategy_period, period_map["peak2_discharge"]
    )
    logger.info(f"debug::peak2_discharge_power: {peak2_discharge_power} kWh")
    peak2_discharge_load = get_discharge_load(
        df_strategy_period, period_map["peak2_discharge"]
    )
    logger.info(f"debug::peak2_discharge_load: {peak2_discharge_load} kW")
    baseline_coef_period_discharge_power = get_discharge_power(
        df_strategy_period, period_map["baseline_coef"]
    )
    baseline_coef_period_discharge_time_len = get_discharge_time_len(
        df_strategy_period, period_map["baseline_coef"]
    )
    logger.info(
        "debug::baseline_coef_period_discharge_power: "
        f"{baseline_coef_period_discharge_power} kWh"
    )
    logger.info(
        "debug::baseline_coef_period_discharge_time_len: "
        f"{baseline_coef_period_discharge_time_len} h"
    )
    climbing_period_discharge_power = get_discharge_power(
        df_strategy_period, period_map["climbing"]
    )
    climbing_period_discharge_time_len = get_discharge_time_len(
        df_strategy_period, period_map["climbing"]
    )
    logger.info(
        f"debug::climbing_period_discharge_power: {climbing_period_discharge_power} kWh"
    )
    logger.info(
        f"debug::climbing_period_discharge_time_len: {climbing_period_discharge_time_len} h"
    )
    return (
        peak1_discharge_load,
        peak1_discharge_power,
        peak2_discharge_load,
        peak2_discharge_power,
        baseline_coef_period_discharge_power,
        climbing_period_discharge_power,
        period_map,
    )


def strategy_adjust_model_1(
    df_strategy_period: pd.DataFrame,
    response_period: Dict,
    response_before_1h_period: Dict,
    response_after_1h_period: Dict,
    peak_discharge_period: Dict,
    response_capacity: float,
    peak_discharge_load: float,
    max_discharge_load: float,
    max_charge_load: float,
    peak_discharge_power: float,
    freq: str,
    discharge_period_adjust_after_fn: Callable,
    after_response_strategy_fn: Callable,
    get_discharge_power_fn: Callable = get_discharge_power,
):
    df_strategy_period = response_period_adjust(
        df_strategy_period,
        response_period,
        response_capacity,
        max_discharge_load,
        max_charge_load,
    )
    response_discharge_power = get_discharge_power_fn(df_strategy_period, response_period)
    df_strategy_period = discharge_period_adjust_after_fn(
        df_strategy_period,
        peak_discharge_period,
        peak_discharge_load,
        response_discharge_power,
    )
    df_strategy_period = response_period_readjust(
        df_strategy_period,
        response_period,
        peak_discharge_power,
        response_discharge_power,
        max_discharge_load,
        max_charge_load,
    )
    return after_response_strategy_fn(
        df_strategy_period,
        response_before_1h_period,
        response_after_1h_period,
        response_period,
        freq,
    )


def strategy_adjust_model_2(
    df_strategy_period: pd.DataFrame,
    response_period: Dict,
    response_before_1h_period: Dict,
    response_after_1h_period: Dict,
    peak_discharge_period: Dict,
    peak_discharge_load: float,
    peak_discharge_power: float,
    climbing_period_discharge_power: float,
    response_capacity: float,
    max_discharge_load: float,
    max_charge_load: float,
    freq: str,
    discharge_period_adjust_before_fn: Callable,
    discharge_period_adjust_after_fn: Callable,
    after_response_strategy_fn: Callable,
    get_discharge_power_fn: Callable = get_discharge_power,
):
    df_strategy_period = response_period_adjust(
        df_strategy_period,
        response_period,
        response_capacity,
        max_discharge_load,
        max_charge_load,
    )
    response_discharge_power = get_discharge_power_fn(df_strategy_period, response_period)
    logger.info(f"debug::response_discharge_power: {response_discharge_power} kWh")
    logger.info(
        f"debug::climbing_period_discharge_power: {climbing_period_discharge_power} kWh"
    )
    if climbing_period_discharge_power > 0.0:
        logger.info("debug::爬坡时段为放电时段...")
        df_strategy_period = discharge_period_adjust_before_fn(
            df_strategy_period,
            peak_discharge_period,
            peak_discharge_load,
            response_discharge_power,
        )
    else:
        logger.info("debug::爬坡时段不是放电时段...")
        df_strategy_period = discharge_period_adjust_after_fn(
            df_strategy_period,
            peak_discharge_period,
            peak_discharge_load,
            response_discharge_power,
        )
    df_strategy_period = response_period_readjust(
        df_strategy_period,
        response_period,
        peak_discharge_power,
        response_discharge_power,
        max_discharge_load,
        max_charge_load,
    )
    return after_response_strategy_fn(
        df_strategy_period,
        response_before_1h_period,
        response_after_1h_period,
        response_period,
        freq,
    )


def strategy_adjust_model_3(
    df_strategy_period: pd.DataFrame,
    response_period: Dict,
    response_before_1h_period: Dict,
    response_after_1h_period: Dict,
    peak_discharge_period: Dict,
    peak_discharge_load: float,
    response_discharge_power: float,
    remain_power_before_response: float,
    freq: str,
    discharge_period_adjust_after_fn: Callable,
    after_response_strategy_fn: Callable,
):
    peak_adjustable_power = response_discharge_power - remain_power_before_response
    logger.info(f"debug::peak_adjustable_power: {peak_adjustable_power} kWh")
    df_strategy_period = discharge_period_adjust_after_fn(
        df_strategy_period,
        peak_discharge_period,
        peak_discharge_load,
        peak_adjustable_power,
    )
    return after_response_strategy_fn(
        df_strategy_period,
        response_before_1h_period,
        response_after_1h_period,
        response_period,
        freq,
    )


def strategy_adjust_model_4(
    df_strategy_period: pd.DataFrame,
    peak_discharge_period: Dict,
    flat_charge_period: Dict,
    response_period: Dict,
    response_before_1h_period: Dict,
    response_after_1h_period: Dict,
    peak_discharge_load: float,
    response_discharge_power: float,
    freq: str,
    discharge_period_adjust_after_fn: Callable,
    charge_period_adjust_fn: Callable,
    after_response_strategy_fn: Callable,
):
    df_strategy_period = discharge_period_adjust_after_fn(
        df_strategy_period,
        peak_discharge_period,
        peak_discharge_load,
        response_discharge_power,
    )
    df_strategy_period = charge_period_adjust_fn(
        df_strategy_period,
        flat_charge_period,
        response_period,
    )
    return after_response_strategy_fn(
        df_strategy_period,
        response_before_1h_period,
        response_after_1h_period,
        response_period,
        freq,
    )
