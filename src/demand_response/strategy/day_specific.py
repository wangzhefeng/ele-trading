from datetime import timedelta
from typing import Callable, Dict

import pandas as pd

from model.model_packages.Demand_Response_optim.engine.response_profit import calc_profit
from model.model_packages.Demand_Response_optim.strategy.core import (
    get_cancel_charge_power,
    get_charge_power,
    get_discharge_load,
    get_discharge_power,
    get_remain_power,
)
from model.model_packages.Demand_Response_optim.strategy.result import (
    simulate_peak2_discharge_for_profit,
)
from model.model_packages.Demand_Response_optim.strategy.rule5 import (
    compare_rule5_alternative_strategies,
    dispatch_rule5_discharge,
    handle_rule5_charge_response,
    handle_rule5_discharge_partial_peak_cancel,
    handle_rule5_discharge_with_sufficient_remain,
    prepare_rule5_state,
)
from model.model_packages.Demand_Response_optim.strategy.rules import (
    strategy_adjust_model_3,
    strategy_adjust_model_4,
)
from utils.log_util import logger


def strategy_adjust_model_5(
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period: pd.DataFrame,
    period_map: Dict,
    response_capacity: float,
    peak1_discharge_power: float,
    peak1_discharge_load: float,
    peak1_max_discharge_load: float,
    max_charge_load: float,
    clearing_price: float,
    freq: str,
    discharge_period_adjust_after_fn: Callable,
    charge_period_adjust_fn: Callable,
    after_response_strategy_fn: Callable,
):
    (
        df_strategy_period_new,
        response_discharge_power,
        response_charge_power,
        remain_power_before_response,
    ) = prepare_rule5_state(
        df_strategy_period=df_strategy_period,
        period_map=period_map,
        response_capacity=response_capacity,
        max_discharge_load=peak1_max_discharge_load,
        max_charge_load=max_charge_load,
        remain_power_source_df=df_strategy_period,
    )
    charge_response_result = handle_rule5_charge_response(
        df_strategy_period_new=df_strategy_period_new,
        response_charge_power=response_charge_power,
        peak1_discharge_load=peak1_discharge_load,
        period_map=period_map,
        freq=freq,
        after_response_strategy_fn=after_response_strategy_fn,
    )
    if charge_response_result is not None:
        df_strategy_period_new, _ = charge_response_result

    discharge_result = dispatch_rule5_discharge(
        response_discharge_power=response_discharge_power,
        peak1_discharge_power=peak1_discharge_power,
        remain_power_before_response=remain_power_before_response,
        on_less_enough_fn=lambda: handle_rule5_discharge_with_sufficient_remain(
            df_strategy_period_new=df_strategy_period_new,
            period_map=period_map,
            peak1_discharge_power=peak1_discharge_power,
            response_discharge_power=response_discharge_power,
            max_discharge_load=peak1_max_discharge_load,
            max_charge_load=max_charge_load,
            freq=freq,
            after_response_strategy_fn=after_response_strategy_fn,
            apply_readjust=False,
        ),
        on_less_not_enough_fn=lambda: handle_rule5_discharge_partial_peak_cancel(
            df_strategy_period_new=df_strategy_period_new,
            period_map=period_map,
            peak1_discharge_load=peak1_discharge_load,
            response_discharge_power=response_discharge_power,
            remain_power_before_response=remain_power_before_response,
            freq=freq,
            discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
            after_response_strategy_fn=after_response_strategy_fn,
        ),
        on_ge_enough_fn=lambda: handle_rule5_discharge_with_sufficient_remain(
            df_strategy_period_new=df_strategy_period_new,
            period_map=period_map,
            peak1_discharge_power=peak1_discharge_power,
            response_discharge_power=response_discharge_power,
            max_discharge_load=peak1_max_discharge_load,
            max_charge_load=max_charge_load,
            freq=freq,
            after_response_strategy_fn=after_response_strategy_fn,
            apply_readjust=True,
        ),
        on_ge_not_enough_fn=lambda: (
            logger.info("debug::剩余电量不够需求响应..."),
            logger.info(f"debug::response_discharge_power: {response_discharge_power}"),
            compare_rule5_alternative_strategies(
                df_strategy_period_raw=df_strategy_period_raw,
                response_capacity=response_capacity,
                clearing_price=clearing_price,
                build_strategy_1_fn=lambda: strategy_adjust_model_4(
                    df_strategy_period_new.copy(),
                    period_map["peak1_discharge"],
                    period_map["charge"],
                    period_map["response"],
                    period_map["response_before_1h"],
                    period_map["response_after_1h"],
                    peak1_discharge_load,
                    response_discharge_power,
                    freq,
                    discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
                    charge_period_adjust_fn=charge_period_adjust_fn,
                    after_response_strategy_fn=after_response_strategy_fn,
                ),
                build_strategy_2_fn=lambda: strategy_adjust_model_3(
                    df_strategy_period_new.copy(),
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
                ),
            ),
        )[-1],
    )
    if discharge_result is not None:
        df_strategy_period_new = discharge_result
    return df_strategy_period_new


def profit_output(
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_new: pd.DataFrame,
    period_map: Dict,
    response_capacity: float,
    clearing_price: float,
    discharge_period_adjust_after_fn: Callable,
):
    logger.info("debug::收益计算...")
    logger.info(f"debug::{'-' * 50}")
    df_strategy_period_new_temp = df_strategy_period_new.copy()
    if period_map["response"]["start"] < period_map["peak2_discharge"]["start"]:
        peak1_discharge_load = get_discharge_load(
            df_strategy_period_new_temp, period_map["peak1_discharge"]
        )
        logger.info(f"debug::peak1_discharge_load: {peak1_discharge_load} kW")
        flat_charge_power = get_charge_power(df_strategy_period_new_temp, period_map["charge"])
        logger.info(f"debug::flat_charge_power: {flat_charge_power} kWh")
        charge_period = {
            "start": period_map["charge"]["start"],
            "end": period_map["peak2_discharge"]["start"] - timedelta(minutes=5),
        }
        flat_discharge_power = get_discharge_power(df_strategy_period_new_temp, charge_period)
        logger.info(f"debug::flat_discharge_power: {flat_discharge_power} kWh")
        is_cancel_charge = get_cancel_charge_power(df_strategy_period_new_temp, period_map["charge"])
        logger.info(f"debug::is_cancel_charge: {is_cancel_charge}")

        if (period_map["current_time"] <= period_map["peak1_discharge"]["end"]) and is_cancel_charge:
            remain_power_before_peak2 = abs(flat_charge_power) - flat_discharge_power
        elif (period_map["current_time"] > period_map["peak1_discharge"]["end"]) and flat_discharge_power != 0:
            remain_power_before_peak2 = abs(flat_charge_power) - flat_discharge_power
        else:
            remain_power_period = {
                "start": period_map["strategy"]["start"],
                "end": period_map["peak2_discharge"]["start"] - timedelta(minutes=5),
            }
            remain_power_before_peak2 = get_remain_power(
                df_strategy_period_new_temp, remain_power_period
            )
        logger.info(f"debug::remain_power_before_peak2: {remain_power_before_peak2} kWh")
        df_strategy_period_new_temp = simulate_peak2_discharge_for_profit(
            df_strategy_period_temp=df_strategy_period_new_temp,
            period_map=period_map,
            peak1_discharge_load=peak1_discharge_load,
            remain_power_before_peak2=remain_power_before_peak2,
            discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
            peak2_min_floor=17888,
        )
    return calc_profit(
        df_strategy_period_raw,
        df_strategy_period_new_temp,
        response_capacity,
        response_capacity,
        clearing_price,
    )
