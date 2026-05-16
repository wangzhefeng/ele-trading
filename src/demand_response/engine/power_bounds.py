from typing import Dict

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response_optim.utils.tools import (
    extract_daily_period_data,
)
from utils.log_util import logger


def _get_history_before_current_day(df_history: pd.DataFrame, period_map: Dict) -> pd.DataFrame:
    current_day_start = pd.to_datetime(f"{period_map['current_time'].date()} 00:00:00")
    return df_history.loc[df_history["time"] < current_day_start]


def _get_period_max_discharge_load(df_history: pd.DataFrame, period: Dict, label: str) -> float:
    df_discharge_history = extract_daily_period_data(df_history, "strategy_load", period)
    discharge_history = df_discharge_history.apply(lambda x: np.nanmean(x), axis=1).values
    max_discharge_load = np.nanmax([x for x in discharge_history if x > 0.0])
    logger.info(f"debug::{label} max_discharge_load: {max_discharge_load:2f} kW")
    return max_discharge_load


def get_peak1_discharge_max_load(df_history: pd.DataFrame, period_map: Dict) -> float:
    history_before_current_day = _get_history_before_current_day(df_history, period_map)
    return _get_period_max_discharge_load(
        history_before_current_day,
        period_map["peak1_discharge"],
        "peak1",
    )


def get_peak2_discharge_max_load(df_history: pd.DataFrame, period_map: Dict) -> float:
    history_before_current_day = _get_history_before_current_day(df_history, period_map)
    return _get_period_max_discharge_load(
        history_before_current_day,
        period_map["peak2_discharge"],
        "peak2",
    )


def get_charge_max_load(df_history: pd.DataFrame, period_map: Dict) -> float:
    history_before_current_day = _get_history_before_current_day(df_history, period_map)
    df_charge_history = extract_daily_period_data(
        history_before_current_day,
        "strategy_load",
        period_map["charge"],
    )
    charge_history = df_charge_history.apply(lambda x: np.nanmean(x), axis=1).values
    max_charge_load = np.nanmin([x for x in charge_history if x < 0.0])
    logger.info(f"debug::flat max_charge_load: {max_charge_load:2f} kW")
    return max_charge_load
