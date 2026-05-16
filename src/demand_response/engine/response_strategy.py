import math
from typing import Dict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.day_dispatch import (
    dispatch_day_strategy_by_mode,
)
from model.model_packages.Demand_Response_optim.strategy.day_specific import (
    profit_output as day_profit_output,
    strategy_adjust_model_5,
)
from model.model_packages.Demand_Response_optim.strategy.night_dispatch import (
    dispatch_night_strategy_by_mode,
)
from model.model_packages.Demand_Response_optim.strategy.night_specific import (
    profit_output as night_profit_output,
)
from model.model_packages.Demand_Response_optim.strategy.match import (
    build_rule_match_context,
    get_day_rule_matches,
    get_night_rule_matches,
)
from model.model_packages.Demand_Response_optim.strategy.core import (
    get_response_time_len,
)
from model.model_packages.Demand_Response_optim.strategy.cooldown import (
    after_response_strategy,
)
from model.model_packages.Demand_Response_optim.strategy.dispatch import (
    execute_rule_and_return,
    prepare_strategy_rule_context,
)
from model.model_packages.Demand_Response_optim.strategy.rules import (
    strategy_adjust_model_1,
    strategy_adjust_model_2,
    strategy_adjust_model_3,
    strategy_adjust_model_4,
)
from utils.log_util import logger

DAY_RULE1_LOG_LABEL = "1.【0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量】"
DAY_RULE2_LOG_LABEL = "2.【0 < 基线调整系数样本所在时段的放电量 < 峰时段放电量】"
DAY_RULE3_LOG_LABEL = "3.【基线调整系数样本所在时段的放电量 == 峰时段放电量】"
DAY_RULE4_LOG_LABEL = "4.【基线调整系数样本所在时段的放电量 == 峰时段放电量】"
RULE5_LOG_LABEL = "5.【基线调整系数样本所在时段的放电量 == 0】"

def _build_strategy_rule_match_context(rule_context: Dict, response_date, response_start):
    """
    提取规则命中判断需要的最小上下文。
    """
    return build_rule_match_context(
        response_date=response_date,
        response_start=response_start,
        delta_discharge_power_1=rule_context["delta_discharge_power_1"],
        delta_discharge_power_2=rule_context["delta_discharge_power_2"],
        peak1_discharge_power=rule_context["peak1_discharge_power"],
        peak2_discharge_power=rule_context["peak2_discharge_power"],
    )


def _build_strategy_runtime_context(
    *,
    df_strategy_period_new: pd.DataFrame,
    period_map: Dict,
    period_profile: Dict,
    response_date,
):
    """
    收口规则执行前的运行时上下文准备。
    """
    rule_context = prepare_strategy_rule_context(df_strategy_period_new, period_map)
    runtime_period_map = rule_context["period_map"]
    response_start = runtime_period_map["response"]["start"]
    return {
        "rule_context": rule_context,
        "period_map": runtime_period_map,
        "response_start": response_start,
        "response_mode": period_profile["response_mode"],
        "shared_after_response_strategy": after_response_strategy,
        "rule_match_context": _build_strategy_rule_match_context(
            rule_context,
            response_date,
            response_start,
        ),
    }


def _build_strategy_output(*, strategy_df, peak1_discharge_load: float, period_type: str, response_mode: str):
    """
    统一封装策略调整输出，避免白天和跨夜返回结构分叉。
    """
    return {
        "strategy_df": strategy_df,
        "peak1_discharge_load": peak1_discharge_load,
        "meta": {"period_type": period_type, "response_mode": response_mode},
    }


def response_period_adjust_standby_discharge(df_strategy_period: pd.DataFrame,
                                             time_period: Dict,
                                             response_capacity: float = None,
                                             max_discharge_load: float = None,
                                             max_charge_load: float = None) -> pd.DataFrame:
    """
    把响应时段的充电策略改成待机或放电，以满足响应目标。
    """
    logger.info("debug::充放电策略的调整: 充电修改为待机或放电...")
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    response_time_len = get_response_time_len(response_period=time_period)
    response_load = response_capacity / response_time_len
    response_load = np.nanmin([response_load, max_discharge_load])
    response_load = np.nanmax([response_load, max_charge_load])
    logger.info(f"debug::response_capacity: {response_capacity}")
    logger.info(f"debug::response_time_len: {response_time_len}")
    logger.info(f"debug::response_load: {response_load}")
    if response_load > 0.0:
        df_strategy_period.loc[period_mask, "strategy_load"] = df_strategy_period.loc[
            period_mask, "strategy_load"
        ].apply(lambda x: response_load if x <= 0.0 else x + response_load)
    else:
        df_strategy_period.loc[period_mask, "strategy_load"] = df_strategy_period.loc[
            period_mask, "strategy_load"
        ].apply(lambda x: x if x >= 0.0 else 0.0)
    return df_strategy_period


def response_period_adjust_discharge(df_strategy_period: pd.DataFrame, time_period: Dict) -> pd.DataFrame:
    """
    将响应时段统一改成放电响应。
    """
    logger.info("debug::需求响应时段充放电策略模拟调整: 充电/待机修改为放电...")
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    df_strategy_period.loc[period_mask, "strategy_load"] = 1.0
    return df_strategy_period


def discharge_period_adjust(df_strategy_period: pd.DataFrame, time_period: Dict, current_time: datetime, freq: str = "5min"):
    """
    从当前通知时刻起取消峰段剩余放电，计算释放出的电量。
    """
    logger.info("debug::第一个峰时放电时段内, 当前时段至放电结束时刻策略修改为待机...")
    period_mask = (
        (df_strategy_period["time"] >= current_time)
        & (df_strategy_period["time"] <= time_period["end"])
    )
    canceled_discharge_power = df_strategy_period.loc[period_mask, "strategy_load"].sum() * (int(freq[:-3]) / 60)
    df_strategy_period.loc[period_mask, "strategy_load"] = 0.0
    return df_strategy_period, canceled_discharge_power


def discharge_period_adjust_before(
    df_strategy_period: pd.DataFrame,
    time_period: Dict,
    peak_discharge_load: float,
    response_discharge_power: float,
    *,
    period_type: str,
):
    """
    取消峰段前部一段放电，为响应腾挪可用电量。
    """
    logger.info("debug::第一个峰时放电时段内放电策略的调整...")
    peak_stop_discharge_time_len = np.nanmin([response_discharge_power / peak_discharge_load, 2.0])
    logger.info(f"debug::response_discharge_power: {response_discharge_power}")
    logger.info(f"debug::peak_discharge_load: {peak_discharge_load}")
    logger.info(f"debug::peak_stop_discharge_time_len: {peak_stop_discharge_time_len}")
    if period_type == "day":
        peak_stop_discharge_minutes = math.ceil((peak_stop_discharge_time_len * 60) / 5) * 5
        period_mask = (
            (df_strategy_period["time"] >= time_period["start"])
            & (df_strategy_period["time"] <= time_period["start"] + timedelta(minutes=peak_stop_discharge_minutes))
        )
    else:
        period_mask = (
            (df_strategy_period["time"] >= time_period["start"])
            & (df_strategy_period["time"] <= time_period["start"] + timedelta(hours=peak_stop_discharge_time_len))
        )
    df_strategy_period.loc[period_mask, "strategy_load"] = 0.0
    return df_strategy_period


def discharge_period_adjust_after(df_strategy_period: pd.DataFrame,
                                  time_period: Dict,
                                  peak_discharge_load: float,
                                  response_discharge_power: float,
                                  *,
                                  period_type: str = "day"):
    """
    取消峰段尾部一段放电，用于修正响应后的峰段策略。
    """
    logger.info("debug::峰时放电时段内放电策略的调整(取消部分放电)...")
    peak_stop_discharge_time_len = np.nanmin([response_discharge_power / peak_discharge_load, 2.0])
    logger.info(f"debug::peak_stop_discharge_time_len: {peak_stop_discharge_time_len}")
    if period_type == "day":
        peak_stop_discharge_minutes = math.ceil((peak_stop_discharge_time_len * 60) / 5) * 5
        period_mask = (
            (df_strategy_period["time"] >= time_period["end"] - timedelta(minutes=peak_stop_discharge_minutes))
            & (df_strategy_period["time"] <= time_period["end"])
        )
    else:
        period_mask = (
            (df_strategy_period["time"] >= time_period["end"] - timedelta(hours=peak_stop_discharge_time_len))
            & (df_strategy_period["time"] <= time_period["end"])
        )
    df_strategy_period.loc[period_mask, "strategy_load"] = 0.0
    return df_strategy_period


def charge_period_adjust(df_strategy_period: pd.DataFrame,
                         time_period: Dict,
                         response_period: Dict):
    """
    清空响应开始前的充电策略，避免对响应形成反向影响。
    """
    time_period = dict(time_period)
    time_period["end"] = response_period["start"]
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] < time_period["end"])
    )
    df_strategy_period.loc[period_mask, "strategy_load"] = 0.0
    return df_strategy_period


def profit_output(
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_new: pd.DataFrame,
    period_map: Dict,
    period_profile: Dict,
    response_capacity: float,
    clearing_price: float,
    peak1_discharge_load: float = None,
):
    """
    根据场景类型计算调整后策略的收益结果。
    """
    if period_profile["period_type"] == "day":
        return day_profit_output(
            df_strategy_period_raw,
            df_strategy_period_new,
            period_map,
            response_capacity,
            clearing_price,
            discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="day"),
        )
    return night_profit_output(
        df_strategy_period_raw,
        df_strategy_period_new,
        period_map,
        peak1_discharge_load,
        response_capacity,
        clearing_price,
        discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="night"),
    )


def _execute_day_basic_rules(
    *,
    df_strategy_period_raw: pd.DataFrame,
    df_strategy_period_new: pd.DataFrame,
    rule_matches: Dict,
    period_map: Dict,
    response_capacity: float,
    peak1_discharge_load: float,
    peak1_discharge_power: float,
    peak2_discharge_power: float,
    climbing_period_discharge_power: float,
    peak1_max_discharge_load: float,
    max_charge_load: float,
    clearing_price: float,
    freq: str,
    shared_after_response_strategy,
):
    """
    执行白天场景的基础规则 1~5。
    """
    if rule_matches["rule1"]:
        result = execute_rule_and_return(
            matched=True,
            log_label=DAY_RULE1_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_2(
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
                discharge_period_adjust_before_fn=lambda *args, **kwargs: discharge_period_adjust_before(*args, **kwargs, period_type="day"),
                discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="day"),
                after_response_strategy_fn=shared_after_response_strategy,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            df_strategy_period_new, _ = result

    if rule_matches["rule2"]:
        result = execute_rule_and_return(
            matched=True,
            log_label=DAY_RULE2_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_2(
                df_strategy_period_new,
                period_map["response"],
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["peak2_discharge"],
                peak1_discharge_load,
                peak2_discharge_power,
                climbing_period_discharge_power,
                response_capacity,
                peak1_max_discharge_load,
                max_charge_load,
                freq,
                discharge_period_adjust_before_fn=lambda *args, **kwargs: discharge_period_adjust_before(*args, **kwargs, period_type="day"),
                discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="day"),
                after_response_strategy_fn=shared_after_response_strategy,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            df_strategy_period_new, _ = result

    if rule_matches["rule3"]:
        result = execute_rule_and_return(
            matched=True,
            log_label=DAY_RULE3_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_1(
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
                discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="day"),
                after_response_strategy_fn=shared_after_response_strategy,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            df_strategy_period_new, _ = result

    if rule_matches["rule4"]:
        result = execute_rule_and_return(
            matched=True,
            log_label=DAY_RULE4_LOG_LABEL,
            execute_fn=lambda: strategy_adjust_model_1(
                df_strategy_period_new,
                period_map["response"],
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["peak2_discharge"],
                response_capacity,
                peak1_discharge_load,
                peak1_max_discharge_load,
                max_charge_load,
                peak2_discharge_power,
                freq,
                discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="day"),
                after_response_strategy_fn=shared_after_response_strategy,
            ),
            peak1_discharge_load=peak1_discharge_load,
        )
        if result is not None:
            df_strategy_period_new, _ = result

    if rule_matches["rule5"]:
        logger.info(f"debug::{RULE5_LOG_LABEL}")
        logger.info(f"debug::{'-' * 50}")
        df_strategy_period_new = strategy_adjust_model_5(
            df_strategy_period_raw,
            df_strategy_period_new,
            period_map,
            response_capacity,
            peak1_discharge_power,
            peak1_discharge_load,
            peak1_max_discharge_load,
            max_charge_load,
            clearing_price,
            freq,
            discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="day"),
            charge_period_adjust_fn=charge_period_adjust,
            after_response_strategy_fn=shared_after_response_strategy,
        )
        logger.info("debug::需求响应策略调整完成!!!")

    return df_strategy_period_new


def _execute_night_basic_rules(
    *,
    response_mode: str,
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
    shared_after_response_strategy,
):
    """
    将跨夜场景的基础规则执行委托给 night_dispatch。
    """
    return dispatch_night_strategy_by_mode(
        response_mode=response_mode,
        df_strategy_period_raw=df_strategy_period_raw,
        df_strategy_period_new=df_strategy_period_new,
        response_capacity=response_capacity,
        period_map=period_map,
        rule_matches=rule_matches,
        peak1_discharge_load=peak1_discharge_load,
        peak1_discharge_power=peak1_discharge_power,
        peak2_discharge_power=peak2_discharge_power,
        climbing_period_discharge_power=climbing_period_discharge_power,
        peak1_max_discharge_load=peak1_max_discharge_load,
        peak2_max_discharge_load=peak2_max_discharge_load,
        max_charge_load=max_charge_load,
        clearing_price=clearing_price,
        freq=freq,
        strategy_adjust_model_1_fn=strategy_adjust_model_1,
        strategy_adjust_model_2_fn=strategy_adjust_model_2,
        strategy_adjust_model_3_fn=strategy_adjust_model_3,
        strategy_adjust_model_4_fn=strategy_adjust_model_4,
        discharge_period_adjust_before_fn=lambda *args, **kwargs: discharge_period_adjust_before(*args, **kwargs, period_type="night"),
        discharge_period_adjust_after_fn=lambda *args, **kwargs: discharge_period_adjust_after(*args, **kwargs, period_type="night"),
        charge_period_adjust_fn=charge_period_adjust,
        after_response_strategy_fn=shared_after_response_strategy,
    )


def _day_strategy_adjust_model(
    df_strategy_period: pd.DataFrame,
    response_capacity: float,
    period_map: Dict,
    period_profile: Dict,
    peak1_max_discharge_load: float,
    peak2_max_discharge_load: float,
    max_charge_load: float,
    clearing_price: float,
    freq: str = "5min",
):
    """
    执行白天场景的完整策略调整主干。
    """
    df_strategy_period_raw = df_strategy_period[["time", "ele_price", "strategy_load"]].copy()
    df_strategy_period_new = df_strategy_period.copy()
    runtime_context = _build_strategy_runtime_context(
        df_strategy_period_new=df_strategy_period_new,
        period_map=period_map,
        period_profile=period_profile,
        response_date=period_map["response_date"],
    )
    rule_context = runtime_context["rule_context"]
    peak1_discharge_load = rule_context["peak1_discharge_load"]
    peak1_discharge_power = rule_context["peak1_discharge_power"]
    peak2_discharge_power = rule_context["peak2_discharge_power"]
    climbing_period_discharge_power = rule_context["climbing_period_discharge_power"]
    period_map = runtime_context["period_map"]
    response_date = period_map["response_date"]
    response_start = runtime_context["response_start"]
    current_time = period_map["current_time"]
    response_mode = runtime_context["response_mode"]
    shared_after_response_strategy = runtime_context["shared_after_response_strategy"]
    rule_match_context = runtime_context["rule_match_context"]
    rule_matches = get_day_rule_matches(rule_match_context)
    df_strategy_period_new = _execute_day_basic_rules(
        df_strategy_period_raw=df_strategy_period_raw,
        df_strategy_period_new=df_strategy_period_new,
        rule_matches=rule_matches,
        period_map=period_map,
        response_capacity=response_capacity,
        peak1_discharge_load=peak1_discharge_load,
        peak1_discharge_power=peak1_discharge_power,
        peak2_discharge_power=peak2_discharge_power,
        climbing_period_discharge_power=climbing_period_discharge_power,
        peak1_max_discharge_load=peak1_max_discharge_load,
        max_charge_load=max_charge_load,
        clearing_price=clearing_price,
        freq=freq,
        shared_after_response_strategy=shared_after_response_strategy,
    )
    strategy_df = dispatch_day_strategy_by_mode(
        response_mode=response_mode,
        df_strategy_period=df_strategy_period,
        df_strategy_period_new=df_strategy_period_new,
        period_map=period_map,
        response_date=response_date,
        response_start=response_start,
        current_time=current_time,
        peak1_max_discharge_load=peak1_max_discharge_load,
        peak2_max_discharge_load=peak2_max_discharge_load,
        max_charge_load=max_charge_load,
        freq=freq,
        discharge_period_adjust_fn=discharge_period_adjust,
        response_period_adjust_discharge_fn=response_period_adjust_discharge,
        response_period_adjust_standby_discharge_fn=response_period_adjust_standby_discharge,
        after_response_strategy_fn=shared_after_response_strategy,
    )
    return _build_strategy_output(
        strategy_df=strategy_df,
        peak1_discharge_load=peak1_discharge_load,
        period_type="day",
        response_mode=response_mode,
    )


def _night_strategy_adjust_model(
    df_strategy_period: pd.DataFrame,
    response_capacity: float,
    period_map: Dict,
    period_profile: Dict,
    peak1_max_discharge_load: float,
    peak2_max_discharge_load: float,
    max_charge_load: float,
    clearing_price: float,
    freq: str = "5min",
):
    """
    执行跨夜场景的完整策略调整主干。
    """
    df_strategy_period_raw = df_strategy_period[["time", "ele_price", "strategy_load"]].copy()
    df_strategy_period_new = df_strategy_period.copy()
    response_date = period_map["response_end_date"]
    runtime_context = _build_strategy_runtime_context(
        df_strategy_period_new=df_strategy_period_new,
        period_map=period_map,
        period_profile=period_profile,
        response_date=response_date,
    )
    rule_context = runtime_context["rule_context"]
    peak1_discharge_load = rule_context["peak1_discharge_load"]
    peak1_discharge_power = rule_context["peak1_discharge_power"]
    peak2_discharge_power = rule_context["peak2_discharge_power"]
    climbing_period_discharge_power = rule_context["climbing_period_discharge_power"]
    period_map = runtime_context["period_map"]
    response_mode = runtime_context["response_mode"]
    shared_after_response_strategy = runtime_context["shared_after_response_strategy"]
    rule_match_context = runtime_context["rule_match_context"]
    rule_matches = get_night_rule_matches(rule_match_context)
    result = _execute_night_basic_rules(
        response_mode=response_mode,
        df_strategy_period_raw=df_strategy_period_raw,
        df_strategy_period_new=df_strategy_period_new,
        response_capacity=response_capacity,
        period_map=period_map,
        rule_matches=rule_matches,
        peak1_discharge_load=peak1_discharge_load,
        peak1_discharge_power=peak1_discharge_power,
        peak2_discharge_power=peak2_discharge_power,
        climbing_period_discharge_power=climbing_period_discharge_power,
        peak1_max_discharge_load=peak1_max_discharge_load,
        peak2_max_discharge_load=peak2_max_discharge_load,
        max_charge_load=max_charge_load,
        clearing_price=clearing_price,
        freq=freq,
        shared_after_response_strategy=shared_after_response_strategy,
    )
    if result is None:
        return _build_strategy_output(
            strategy_df=None,
            peak1_discharge_load=peak1_discharge_load,
            period_type="night",
            response_mode=response_mode,
        )
    strategy_df, peak1_load = result
    return _build_strategy_output(
        strategy_df=strategy_df,
        peak1_discharge_load=peak1_load,
        period_type="night",
        response_mode=response_mode,
    )


def strategy_adjust_model(df_strategy_period: pd.DataFrame,
                          response_capacity: float,
                          period_map: Dict,
                          period_profile: Dict,
                          peak1_max_discharge_load: float,
                          peak2_max_discharge_load: float,
                          max_charge_load: float,
                          clearing_price: float,
                          freq: str = "5min"):
    """
    按 period_type 分派到白天或跨夜策略调整主干。
    """
    if period_profile["period_type"] == "day":
        return _day_strategy_adjust_model(
            df_strategy_period=df_strategy_period,
            response_capacity=response_capacity,
            period_map=period_map,
            period_profile=period_profile,
            peak1_max_discharge_load=peak1_max_discharge_load,
            peak2_max_discharge_load=peak2_max_discharge_load,
            max_charge_load=max_charge_load,
            clearing_price=clearing_price,
            freq=freq,
        )
    return _night_strategy_adjust_model(
        df_strategy_period=df_strategy_period,
        response_capacity=response_capacity,
        period_map=period_map,
        period_profile=period_profile,
        peak1_max_discharge_load=peak1_max_discharge_load,
        peak2_max_discharge_load=peak2_max_discharge_load,
        max_charge_load=max_charge_load,
        clearing_price=clearing_price,
        freq=freq,
    )
