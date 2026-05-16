from datetime import timedelta
from typing import Callable, Dict

import pandas as pd

from model.model_packages.Demand_Response_optim.engine.response_profit import calc_profit
from model.model_packages.Demand_Response_optim.strategy.core import (
    get_remain_power,
)
from model.model_packages.Demand_Response_optim.strategy.result import (
    simulate_peak2_discharge_for_profit,
)


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
        df_strategy_period_temp = simulate_peak2_discharge_for_profit(
            df_strategy_period_temp=df_strategy_period_temp,
            period_map=period_map,
            peak1_discharge_load=peak1_discharge_load,
            remain_power_before_peak2=remain_power_before_peak2,
            discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
        )
    return calc_profit(
        df_strategy_period_raw,
        df_strategy_period_temp,
        response_capacity,
        response_capacity,
        clearing_price,
    )
