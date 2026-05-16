from typing import Callable, Dict, Optional, Tuple

import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.dispatch import (
    execute_rule_and_return,
)
from model.model_packages.Demand_Response_optim.strategy.rule5 import (
    handle_rule5_charge_response,
    prepare_rule5_state,
    compare_rule5_alternative_strategies,
    handle_rule5_discharge_partial_peak_cancel,
    handle_rule5_discharge_with_sufficient_remain,
    dispatch_rule5_discharge,
)
from utils.log_util import logger

NIGHT_RULE1_LOG_LABEL = "1.【0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量】"
NIGHT_RULE2_LOG_LABEL = "2.【0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量】"
NIGHT_RULE3_LOG_LABEL = "3.【基线调整系数样本所在时段的放电量 == 峰时段放电量】"
NIGHT_RULE4_LOG_LABEL = "4.【基线调整系数样本所在时段的放电量 == 峰时段放电量】"
NIGHT_RULE5_LOG_LABEL = "5.【基线调整系数样本所在时段的放电量 == 0】"


def _execute_night_standard_rule(
    *,
    matched: bool,
    log_label: str,
    execute_fn: Callable[[], pd.DataFrame],
    peak1_discharge_load: float,
) -> Optional[Tuple[pd.DataFrame, float]]:
    """
    执行跨夜标准规则，并保持统一返回格式。
    """
    return execute_rule_and_return(
        matched=matched,
        log_label=log_label,
        execute_fn=execute_fn,
        peak1_discharge_load=peak1_discharge_load,
    )


def _build_rule5_profit_compare_strategy(
    *,
    allow_rule5_profit_compare: bool,
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_new: pd.DataFrame,
    response_capacity: float,
    clearing_price: float,
    period_map: Dict,
    peak1_discharge_load: float,
    response_discharge_power: float,
    remain_power_before_response: float,
    freq: str,
    strategy_adjust_model_3_fn: Callable,
    strategy_adjust_model_4_fn: Callable,
    discharge_period_adjust_after_fn: Callable,
    charge_period_adjust_fn: Callable,
    after_response_strategy_fn: Callable,
) -> pd.DataFrame:
    """
    在 rule5 下比较多个候选策略，或在快速模式下直接走保守方案。
    """
    if allow_rule5_profit_compare:
        return compare_rule5_alternative_strategies(
            df_strategy_period_raw=df_strategy_period_raw,
            response_capacity=response_capacity,
            clearing_price=clearing_price,
            build_strategy_1_fn=lambda: strategy_adjust_model_4_fn(
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
            build_strategy_2_fn=lambda: strategy_adjust_model_3_fn(
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
        )
    logger.info("debug::日内-快速模式下跳过 rule5 收益比较，采用快速保守策略...")
    return strategy_adjust_model_3_fn(
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
    )


def _handle_night_rule5(
    *,
    allow_rule5_profit_compare: bool,
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_new: pd.DataFrame,
    response_capacity: float,
    period_map: Dict,
    peak1_discharge_load: float,
    peak1_discharge_power: float,
    peak1_max_discharge_load: float,
    max_charge_load: float,
    clearing_price: float,
    freq: str,
    strategy_adjust_model_3_fn: Callable,
    strategy_adjust_model_4_fn: Callable,
    discharge_period_adjust_after_fn: Callable,
    charge_period_adjust_fn: Callable,
    after_response_strategy_fn: Callable,
) -> Optional[Tuple[pd.DataFrame, float]]:
    """
    处理跨夜场景最复杂的 rule5 分支。
    """
    logger.info(f"debug::{NIGHT_RULE5_LOG_LABEL}")
    logger.info(f"debug::{'-' * 50}")
    (
        df_strategy_period_new,
        response_discharge_power,
        response_charge_power,
        remain_power_before_response,
    ) = prepare_rule5_state(
        df_strategy_period=df_strategy_period_new,
        period_map=period_map,
        response_capacity=response_capacity,
        max_discharge_load=peak1_max_discharge_load,
        max_charge_load=max_charge_load,
        remain_power_source_df=df_strategy_period_raw,
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
        return charge_response_result

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
            _build_rule5_profit_compare_strategy(
                allow_rule5_profit_compare=allow_rule5_profit_compare,
                df_strategy_period_raw=df_strategy_period_raw,
                df_strategy_period_new=df_strategy_period_new,
                response_capacity=response_capacity,
                clearing_price=clearing_price,
                period_map=period_map,
                peak1_discharge_load=peak1_discharge_load,
                response_discharge_power=response_discharge_power,
                remain_power_before_response=remain_power_before_response,
                freq=freq,
                strategy_adjust_model_3_fn=strategy_adjust_model_3_fn,
                strategy_adjust_model_4_fn=strategy_adjust_model_4_fn,
                discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
                charge_period_adjust_fn=charge_period_adjust_fn,
                after_response_strategy_fn=after_response_strategy_fn,
            ),
        )[-1],
    )
    if discharge_result is None:
        return None
    logger.info("debug::需求响应策略调整完成!!!")
    return (discharge_result, peak1_discharge_load)


def _dispatch_night_strategy_rules(
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_new: pd.DataFrame,
    response_capacity: float,
    period_map: Dict,
    rule_matches: Dict,
    peak1_discharge_load: float,
    peak1_discharge_power: float,
    peak2_discharge_power: float,
    climbing_period_discharge_power: float,
    peak1_max_discharge_load: float,
    peak2_max_discharge_load: float,
    max_charge_load: float,
    clearing_price: float,
    freq: str,
    strategy_adjust_model_1_fn: Callable,
    strategy_adjust_model_2_fn: Callable,
    strategy_adjust_model_3_fn: Callable,
    strategy_adjust_model_4_fn: Callable,
    discharge_period_adjust_before_fn: Callable,
    discharge_period_adjust_after_fn: Callable,
    charge_period_adjust_fn: Callable,
    after_response_strategy_fn: Callable,
    allow_rule5_profit_compare: bool = True,
) -> Optional[Tuple[pd.DataFrame, float]]:
    """
    顺序执行跨夜规则 1~5，命中后立即返回对应策略。
    """
    if rule_matches["rule1"]:
        result = _execute_night_standard_rule(
            matched=True,
            log_label=NIGHT_RULE1_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_2_fn(
                df_strategy_period_new,
                period_map["response"],
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["peak1_discharge"],
                peak1_discharge_load,
                peak1_discharge_power,
                climbing_period_discharge_power,
                response_capacity,
                peak1_max_discharge_load,
                max_charge_load,
                freq,
                discharge_period_adjust_before_fn=discharge_period_adjust_before_fn,
                discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
                after_response_strategy_fn=after_response_strategy_fn,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            return result

    if rule_matches["rule2"]:
        result = _execute_night_standard_rule(
            matched=True,
            log_label=NIGHT_RULE2_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_2_fn(
                df_strategy_period_new,
                period_map["response"],
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["peak2_discharge"],
                peak1_discharge_load,
                peak2_discharge_power,
                climbing_period_discharge_power,
                response_capacity,
                peak2_max_discharge_load,
                max_charge_load,
                freq,
                discharge_period_adjust_before_fn=discharge_period_adjust_before_fn,
                discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
                after_response_strategy_fn=after_response_strategy_fn,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            return result

    if rule_matches["rule3"]:
        result = _execute_night_standard_rule(
            matched=True,
            log_label=NIGHT_RULE3_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_1_fn(
                df_strategy_period_new,
                period_map["response"],
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["peak1_discharge"],
                response_capacity,
                peak1_discharge_load,
                peak1_max_discharge_load,
                max_charge_load,
                peak1_discharge_power,
                freq,
                discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
                after_response_strategy_fn=after_response_strategy_fn,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            return result

    if rule_matches["rule4"]:
        result = _execute_night_standard_rule(
            matched=True,
            log_label=NIGHT_RULE4_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_1_fn(
                df_strategy_period_new,
                period_map["response"],
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["peak2_discharge"],
                response_capacity,
                peak1_discharge_load,
                peak2_max_discharge_load,
                max_charge_load,
                peak2_discharge_power,
                freq,
                discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
                after_response_strategy_fn=after_response_strategy_fn,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            return result

    if rule_matches["rule5"]:
        return _handle_night_rule5(
            allow_rule5_profit_compare=allow_rule5_profit_compare,
            df_strategy_period_raw=df_strategy_period_raw,
            df_strategy_period_new=df_strategy_period_new,
            response_capacity=response_capacity,
            period_map=period_map,
            peak1_discharge_load=peak1_discharge_load,
            peak1_discharge_power=peak1_discharge_power,
            peak1_max_discharge_load=peak1_max_discharge_load,
            max_charge_load=max_charge_load,
            clearing_price=clearing_price,
            freq=freq,
            strategy_adjust_model_3_fn=strategy_adjust_model_3_fn,
            strategy_adjust_model_4_fn=strategy_adjust_model_4_fn,
            discharge_period_adjust_after_fn=discharge_period_adjust_after_fn,
            charge_period_adjust_fn=charge_period_adjust_fn,
            after_response_strategy_fn=after_response_strategy_fn,
        )

    return None


def dispatch_night_strategy_by_mode(*, response_mode: str, **kwargs):
    """
    按 response_mode 决定跨夜场景是否保留 rule5 收益比较。
    """
    logger.info(f"debug::跨夜模式后置调度, response_mode={response_mode}")
    if response_mode == "日前":
        return _dispatch_night_strategy_rules(**kwargs, allow_rule5_profit_compare=True)
    if response_mode == "日内":
        return _dispatch_night_strategy_rules(**kwargs, allow_rule5_profit_compare=True)
    if response_mode == "日内-快速":
        return _dispatch_night_strategy_rules(**kwargs, allow_rule5_profit_compare=False)
    raise ValueError(f"Unsupported response_mode: {response_mode}")
