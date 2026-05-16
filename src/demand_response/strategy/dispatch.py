from typing import Any, Callable, Dict, Tuple

import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.rules import (
    get_strategy_info,
)
from utils.log_util import logger


def prepare_strategy_rule_context(
    df_strategy_period_new: pd.DataFrame,
    period_map: Dict,
) -> Dict[str, Any]:
    (
        peak1_discharge_load,
        peak1_discharge_power,
        peak2_discharge_load,
        peak2_discharge_power,
        baseline_coef_period_discharge_power,
        climbing_period_discharge_power,
        period_map,
    ) = get_strategy_info(df_strategy_period_new, period_map)
    delta_discharge_power_1 = (
        peak1_discharge_power - baseline_coef_period_discharge_power
    )
    logger.info(f"debug::delta_discharge_power_1: {delta_discharge_power_1} kWh")
    delta_discharge_power_2 = (
        peak2_discharge_power - baseline_coef_period_discharge_power
    )
    logger.info(f"debug::delta_discharge_power_2: {delta_discharge_power_2} kWh")
    return {
        "peak1_discharge_load": peak1_discharge_load,
        "peak1_discharge_power": peak1_discharge_power,
        "peak2_discharge_load": peak2_discharge_load,
        "peak2_discharge_power": peak2_discharge_power,
        "baseline_coef_period_discharge_power": baseline_coef_period_discharge_power,
        "climbing_period_discharge_power": climbing_period_discharge_power,
        "delta_discharge_power_1": delta_discharge_power_1,
        "delta_discharge_power_2": delta_discharge_power_2,
        "period_map": period_map,
    }


def execute_rule_and_return(
    matched: bool,
    log_label: str,
    execute_fn: Callable[[], pd.DataFrame],
    peak1_discharge_load: float,
) -> Tuple[pd.DataFrame, float] | None:
    if not matched:
        return None
    logger.info(f"debug::{log_label}")
    logger.info(f"debug::{'-' * 50}")
    df_strategy_period_new = execute_fn()
    logger.info("debug::需求响应策略调整完成!!!")
    return (df_strategy_period_new, peak1_discharge_load)
