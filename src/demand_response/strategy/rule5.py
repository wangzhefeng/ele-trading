from typing import Callable, Dict, Optional, Tuple

import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.core import (
    get_charge_power,
    get_discharge_power,
    get_remain_power,
)
from model.model_packages.Demand_Response_optim.strategy.result import (
    compare_strategy_profit,
)
from model.model_packages.Demand_Response_optim.strategy.rules import (
    response_period_adjust,
    response_period_readjust,
    strategy_adjust_model_3,
)
from utils.log_util import logger


def prepare_rule5_state(
    df_strategy_period: pd.DataFrame,
    period_map: Dict,
    response_capacity: float,
    max_discharge_load: float,
    max_charge_load: float,
    remain_power_source_df: pd.DataFrame,
) -> Tuple[pd.DataFrame, float, float, float]:
    df_strategy_period_new = response_period_adjust(
        df_strategy_period,
        period_map["response"],
        response_capacity,
        max_discharge_load,
        max_charge_load,
    )
    response_discharge_power = get_discharge_power(
        df_strategy_period_new, period_map["response"]
    )
    logger.info(f"debug::response_discharge_power: {response_discharge_power} kWh")
    response_charge_power = get_charge_power(
        df_strategy_period_new, period_map["response"]
    )
    logger.info(f"debug::response_charge_power: {response_charge_power} kWh")
    remain_power_period = {
        "start": period_map["strategy"]["start"],
        "end": period_map["response_before_1h"]["start"] - pd.Timedelta(minutes=5),
    }
    remain_power_before_response = get_remain_power(
        remain_power_source_df, remain_power_period
    )
    logger.info(
        f"debug::remain_power_before_response: {remain_power_before_response} kWh"
    )
    return (
        df_strategy_period_new,
        response_discharge_power,
        response_charge_power,
        remain_power_before_response,
    )


def handle_rule5_charge_response(
    df_strategy_period_new: pd.DataFrame,
    response_charge_power: float,
    peak1_discharge_load: float,
    period_map: Dict,
    freq: str,
    after_response_strategy_fn: Callable,
) -> Optional[Tuple[pd.DataFrame, float]]:
    if response_charge_power >= 0.0:
        return None
    logger.info("debug::进行充电响应...")
    df_strategy_period_new = after_response_strategy_fn(
        df_strategy_period_new,
        period_map["response_before_1h"],
        period_map["response_after_1h"],
        period_map["response"],
        freq,
    )
    logger.info("debug::需求响应策略调整完成!!!")
    return (df_strategy_period_new, peak1_discharge_load)


def compare_rule5_alternative_strategies(
    df_strategy_period_raw: pd.DataFrame,
    response_capacity: float,
    clearing_price: float,
    build_strategy_1_fn: Callable[[], pd.DataFrame],
    build_strategy_2_fn: Callable[[], pd.DataFrame],
) -> pd.DataFrame:
    logger.info(
        "debug::策略 1: 第一个峰时放电全部修改为待机，并且平时不能充电：可调负荷为峰时放电量..."
    )
    df_strategy_period_1 = build_strategy_1_fn()
    logger.info(
        "debug::策略 2: 第一个峰时放电部分修改为待机，并且平时进行部分充电："
        "待机取消的放电量+充电电量=响应所需电量..."
    )
    df_strategy_period_2 = build_strategy_2_fn()
    logger.info("debug::计算上述两种策略的收益提升，并作出决策...")
    return compare_strategy_profit(
        df_strategy_period_raw,
        df_strategy_period_1,
        df_strategy_period_2,
        response_capacity,
        clearing_price,
    )


def handle_rule5_discharge_with_sufficient_remain(
    df_strategy_period_new: pd.DataFrame,
    period_map: Dict,
    peak1_discharge_power: float,
    response_discharge_power: float,
    max_discharge_load: float,
    max_charge_load: float,
    freq: str,
    after_response_strategy_fn: Callable,
    apply_readjust: bool,
) -> pd.DataFrame:
    logger.info("debug::剩余电量够需求响应...")
    logger.info(
        "debug::第一个峰时放电部分全部不做修改，并且平时进行部分充电，充电功量能够达到`响应所需电量`..."
    )
    if apply_readjust:
        df_strategy_period_new = response_period_readjust(
            df_strategy_period_new,
            period_map["response"],
            peak1_discharge_power,
            response_discharge_power,
            max_discharge_load,
            max_charge_load,
        )
    return after_response_strategy_fn(
        df_strategy_period_new,
        period_map["response_before_1h"],
        period_map["response_after_1h"],
        period_map["response"],
        freq,
    )


def handle_rule5_discharge_partial_peak_cancel(
    df_strategy_period_new: pd.DataFrame,
    period_map: Dict,
    peak1_discharge_load: float,
    response_discharge_power: float,
    remain_power_before_response: float,
    freq: str,
    discharge_period_adjust_after_fn: Callable,
    after_response_strategy_fn: Callable,
) -> pd.DataFrame:
    logger.info("debug::剩余电量不够需求响应...")
    logger.info(
        "debug::第一个峰时放电部分修改为待机，并且平时进行部分充电："
        "待机取消的放电量+充电电量=响应所需电量..."
    )
    return strategy_adjust_model_3(
        df_strategy_period_new,
        period_map["response"],
        period_map["response_before_1h"],
        period_map["response_after_1h"],
        period_map["peak1_discharge"],
        peak1_discharge_load,
        response_discharge_power,
        remain_power_before_response,
        freq,
        discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
        after_response_strategy_fn=after_response_strategy_fn,
    )


def dispatch_rule5_discharge(
    response_discharge_power: float,
    peak1_discharge_power: float,
    remain_power_before_response: float,
    on_less_enough_fn: Callable[[], pd.DataFrame],
    on_less_not_enough_fn: Callable[[], pd.DataFrame],
    on_ge_enough_fn: Callable[[], pd.DataFrame],
    on_ge_not_enough_fn: Callable[[], pd.DataFrame],
) -> pd.DataFrame | None:
    if response_discharge_power <= 0.0:
        return None
    logger.info("debug::进行放电响应...")
    if peak1_discharge_power > response_discharge_power:
        logger.info("debug::需求响应放电所需电量 < 第一个峰时放电电量...")
        if remain_power_before_response >= response_discharge_power:
            return on_less_enough_fn()
        return on_less_not_enough_fn()
    logger.info("debug::需求响应放电所需电量 >= 第一个峰时放电电量...")
    if remain_power_before_response >= response_discharge_power:
        return on_ge_enough_fn()
    return on_ge_not_enough_fn()
