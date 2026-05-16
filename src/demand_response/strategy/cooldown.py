from datetime import timedelta
from typing import Dict

from model.model_packages.Demand_Response_optim.strategy.core import (
    get_response_period_sign_flags,
)
from utils.log_util import logger


def _response_before_1h_period_adjust(
    df_strategy_period,
    response_period: Dict,
    response_before_1h_period: Dict,
    freq: str,
):
    """
    根据响应前的充放电方向，决定是否清空响应前 1 小时的策略。

    业务目标是避免储能在临近响应前仍维持与响应方向冲突的充放电动作，
    从而为正式响应预留电量和状态空间。
    """
    # 响应开始前 1 个步长的策略方向，用于判断临近响应时的状态。
    before_response_before_1min_period_mask = (
        (df_strategy_period["time"] >= response_period["start"] - timedelta(minutes=int(freq[:-3])))
        & (df_strategy_period["time"] < response_period["start"])
    )
    before_response_before_1min_period_values = df_strategy_period.loc[
        before_response_before_1min_period_mask, "strategy_load"
    ].values
    # 响应前 1 小时之前 1 个步长的策略方向，用于判断冷静期前的衔接状态。
    before_response_before_1h_period_mask = (
        (df_strategy_period["time"] >= response_before_1h_period["start"] - timedelta(minutes=int(freq[:-3])))
        & (df_strategy_period["time"] < response_before_1h_period["start"])
    )
    before_response_before_1h_period_values = df_strategy_period.loc[
        before_response_before_1h_period_mask, "strategy_load"
    ].values
    # 正式响应时段内的策略方向，用于判断本次响应属于放电响应还是充电响应。
    response_period_mask = (
        (df_strategy_period["time"] >= response_period["start"])
        & (df_strategy_period["time"] <= response_period["end"])
    )
    response_period_values = df_strategy_period.loc[response_period_mask, "strategy_load"].values
    # 响应前 1 小时窗口是待修正区间。
    response_before_1h_period_mask = (
        (df_strategy_period["time"] >= response_before_1h_period["start"])
        & (df_strategy_period["time"] <= response_before_1h_period["end"])
    )
    # 当响应方向与响应前窗口内的策略方向冲突时，将冷静期窗口置为待机。
    if before_response_before_1h_period_values.size > 0.0:
        response_period_values_pos, response_period_values_neg = get_response_period_sign_flags(
            response_period_values
        )
        if (response_period_values_pos and before_response_before_1h_period_values[0] < 0.0) or \
           (response_period_values_pos and before_response_before_1min_period_values[0] < 0.0) or \
           (response_period_values_neg and before_response_before_1h_period_values[0] > 0.0):
            df_strategy_period.loc[response_before_1h_period_mask, "strategy_load"] = 0.0
    return df_strategy_period


def _response_after_1h_period_adjust(
    df_strategy_period,
    response_period: Dict,
    response_after_1h_period: Dict,
    freq: str,
):
    """
    根据响应后的充放电方向，修正响应后 1 小时的恢复策略。

    业务目标是避免储能在刚完成需求响应后立即进入与响应方向相反的动作，
    以满足电池冷静期和策略连续性的要求。
    """
    # 响应结束后 1 个步长的策略方向，用于判断刚结束响应时的状态。
    after_response_after_1min_period_mask = (
        (df_strategy_period["time"] > response_period["end"])
        & (df_strategy_period["time"] <= response_period["end"] + timedelta(minutes=int(freq[:-3])))
    )
    after_response_after_1min_period_values = df_strategy_period.loc[
        after_response_after_1min_period_mask, "strategy_load"
    ].values
    # 响应后 1 小时之后 1 个步长的策略方向，用于判断恢复窗口后的衔接状态。
    after_response_after_1h_period_mask = (
        (df_strategy_period["time"] > response_after_1h_period["end"])
        & (df_strategy_period["time"] <= response_after_1h_period["end"] + timedelta(minutes=int(freq[:-3])))
    )
    after_response_after_1h_period_values = df_strategy_period.loc[
        after_response_after_1h_period_mask, "strategy_load"
    ].values
    # 正式响应时段内的策略方向，用于判断本次响应属于放电响应还是充电响应。
    response_period_mask = (
        (df_strategy_period["time"] >= response_period["start"])
        & (df_strategy_period["time"] <= response_period["end"])
    )
    response_period_values = df_strategy_period.loc[response_period_mask, "strategy_load"].values
    # 响应后 1 小时窗口是待修正区间。
    response_after_1h_period_mask = (
        (df_strategy_period["time"] >= response_after_1h_period["start"])
        & (df_strategy_period["time"] <= response_after_1h_period["end"])
    )
    # 当恢复窗口内的方向与响应方向冲突时，去掉负向充电动作，保留待机或放电。
    if after_response_after_1h_period_values.size > 0:
        response_period_values_pos, response_period_values_neg = get_response_period_sign_flags(
            response_period_values
        )
        if (response_period_values_pos and after_response_after_1h_period_values[0] < 0.0) or \
           (response_period_values_pos and after_response_after_1min_period_values[0] < 0.0) or \
           (response_period_values_neg and after_response_after_1h_period_values[0] > 0.0):
            df_strategy_period.loc[response_after_1h_period_mask, "strategy_load"] = df_strategy_period.loc[
                response_after_1h_period_mask, "strategy_load"
            ].apply(lambda x: x if x >= 0.0 else 0.0)
    return df_strategy_period


def after_response_strategy(
    df_strategy_period,
    response_before_1h_period: Dict,
    response_after_1h_period: Dict,
    response_period: Dict,
    freq: float,
):
    """
    统一执行需求响应前后 1 小时的冷静期修正。

    这是 cooldown 模块对外暴露的唯一接口。调用方只需提供策略表和三个时段窗口，
    不再关心响应前后两个修正函数的组合方式。
    """
    logger.info("debug::需求响应时段前 1 小时时段内电池冷静期策略调整...")
    df_strategy_period = _response_before_1h_period_adjust(
        df_strategy_period, response_period, response_before_1h_period, freq
    )
    logger.info("debug::需求响应时段后 1 小时时段内电池冷静期策略调整...")
    df_strategy_period = _response_after_1h_period_adjust(
        df_strategy_period, response_period, response_after_1h_period, freq
    )
    return df_strategy_period
