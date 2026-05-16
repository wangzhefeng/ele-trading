from typing import Callable, Dict

from model.model_packages.Demand_Response_optim.strategy.realtime import (
    apply_canceled_peak_response,
    calc_midday_response_remain_power,
)
from model.model_packages.Demand_Response_optim.strategy.result import (
    find_first_strategy_change_time,
)
from model.model_packages.Demand_Response_optim.strategy.clock import (
    build_day_time_points,
    is_at_or_after,
    is_before,
    is_in_window,
)
from utils.log_util import logger


def _dispatch_day_intraday_strategy(*, response_date, current_time, df_strategy_period_new, **kwargs):
    """
    白天日内模式只在 10:00 后继续做后置调度。
    """
    time_points = build_day_time_points(response_date)
    if is_before(current_time, time_points["10:00"]):
        logger.info("debug::日内模式下，10:00 前不进行后置调度，保持基础规则结果...")
        return df_strategy_period_new
    return _dispatch_day_realtime_strategy(
        response_date=response_date,
        current_time=current_time,
        df_strategy_period_new=df_strategy_period_new,
        **kwargs,
    )


def _dispatch_day_fast_strategy(*, response_date, current_time, df_strategy_period_new, **kwargs):
    """
    白天快速模式只在晚峰前后才继续做后置调度。
    """
    time_points = build_day_time_points(response_date)
    if is_before(current_time, time_points["19:00"]):
        logger.info("debug::日内-快速模式下，19:00 前不进行后置调度，保持基础规则结果...")
        return df_strategy_period_new
    return _dispatch_day_realtime_strategy(
        response_date=response_date,
        current_time=current_time,
        df_strategy_period_new=df_strategy_period_new,
        **kwargs,
    )


def _dispatch_day_realtime_strategy(
    *,
    df_strategy_period,
    df_strategy_period_new,
    period_map: Dict,
    response_date,
    response_start,
    current_time,
    peak1_max_discharge_load: float,
    peak2_max_discharge_load: float,
    max_charge_load: float,
    freq: str,
    discharge_period_adjust_fn: Callable,
    response_period_adjust_discharge_fn: Callable,
    response_period_adjust_standby_discharge_fn: Callable,
    after_response_strategy_fn: Callable,
):
    """
    执行白天场景的后置调度，决定基础规则后是否继续改峰段和响应前后策略。
    """
    logger.info(f"debug::{'-' * 50}")
    df_strategy_raw = df_strategy_period[["time", "ele_price", "strategy_load"]].copy()
    df_strategy_copy = df_strategy_period.copy()
    time_points = build_day_time_points(response_date)

    if is_before(current_time, time_points["08:00"]):
        logger.info("debug::当前时刻(通知时刻) < 响应日 08:00, 基本规则不变...")
        return df_strategy_period_new

    if is_in_window(current_time, time_points["08:00"], time_points["10:00"]):
        logger.info("debug::当前时刻(通知时刻)处于响应日 [08:00~10:00)...")
        first_strategy_change_time = find_first_strategy_change_time(
            df_strategy_raw, df_strategy_period_new
        )
        logger.info(f"debug::first_strategy_change_time: {first_strategy_change_time}")
        if first_strategy_change_time is None:
            logger.info("debug::需求响应策略未发生变化，保持原策略...")
            return df_strategy_period_new
        if current_time <= first_strategy_change_time:
            logger.info("debug::当前时刻(通知时刻) <= 需求响应第一个峰时放电策略修改开始的时刻, 基本规则不变...")
            return df_strategy_period_new
        logger.info("debug::当前时刻(通知时刻) > 需求响应第一个峰时放电策略修改开始的时刻...")
        return apply_canceled_peak_response(
            df_strategy_raw=df_strategy_raw,
            df_strategy_copy=df_strategy_copy,
            peak_period=period_map["peak1_discharge"],
            current_time=current_time,
            period_map=period_map,
            max_discharge_load=peak1_max_discharge_load,
            max_charge_load=max_charge_load,
            freq=freq,
            discharge_period_adjust_fn=discharge_period_adjust_fn,
            response_period_adjust_discharge_fn=response_period_adjust_discharge_fn,
            response_period_adjust_standby_discharge_fn=response_period_adjust_standby_discharge_fn,
            after_response_strategy_fn=after_response_strategy_fn,
        )

    if is_in_window(current_time, time_points["10:00"], time_points["19:00"]):
        logger.info("debug::当前时刻(通知时刻)处于响应日 [10:00~19:00), 第一个峰时放电时段修改时间已过，第一个峰时放电策略不能进行调整...")
        if response_start <= time_points["12:00"]:
            logger.info("debug::需求响应开始时间 <= 响应日 12:00, 考虑到电池冷静期，需求响应时段只能待机...")
            if response_start <= time_points["10:30"]:
                logger.info("debug::需求响应开始时间 <= 响应日 10:30, 不进行需求响应...")
                return None
            logger.info("debug::需求响应开始时间 > 响应日 10:30, 考虑到电池冷静期，需求响应时段只能待机...")
            return response_period_adjust_standby_discharge_fn(
                df_strategy_copy,
                period_map["response"],
                0.0,
                peak1_max_discharge_load,
                max_charge_load,
            )

        if is_in_window(response_start, time_points["12:00"], time_points["21:00"]):
            logger.info("debug::需求响应开始时间处于响应日 (12:00, 21:00), 需求响应时段能够进行放电响应...")
            df_strategy_new_temp = response_period_adjust_discharge_fn(
                df_strategy_raw,
                period_map["response"],
            )
            df_strategy_new_temp = after_response_strategy_fn(
                df_strategy_new_temp,
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["response"],
                freq,
            )
            remain_power_before_response = calc_midday_response_remain_power(
                df_strategy_new_temp=df_strategy_new_temp,
                response_date=response_date,
                response_start=response_start,
            )
            logger.info("debug::使用剩余的电量进行需求响应...")
            df_strategy_new = response_period_adjust_standby_discharge_fn(
                df_strategy_copy,
                period_map["response"],
                remain_power_before_response,
                peak2_max_discharge_load,
                max_charge_load,
            )
            return after_response_strategy_fn(
                df_strategy_new,
                period_map["response_before_1h"],
                period_map["response_after_1h"],
                period_map["response"],
                freq,
            )

        if is_at_or_after(response_start, time_points["21:00"]):
            logger.info("debug::需求响应开始时间 >= 响应日 21:00...")
            first_strategy_change_time = find_first_strategy_change_time(
                df_strategy_raw, df_strategy_period_new
            )
            logger.info(f"debug::first_strategy_change_time: {first_strategy_change_time}")
            if first_strategy_change_time is None:
                logger.info("debug::需求响应策略未发生变化，保持原策略...")
                return df_strategy_period_new
            if current_time <= first_strategy_change_time:
                logger.info("debug::当前时刻(通知时刻) <= 需求响应第二个峰时放电策略修改开始的时刻, 基本规则不变...")
                return df_strategy_period_new
            logger.info("debug::当前时刻(通知时刻) > 需求响应第二个峰时放电策略修改开始的时刻...")
            return apply_canceled_peak_response(
                df_strategy_raw=df_strategy_raw,
                df_strategy_copy=df_strategy_copy,
                peak_period=period_map["peak2_discharge"],
                current_time=current_time,
                period_map=period_map,
                max_discharge_load=peak2_max_discharge_load,
                max_charge_load=max_charge_load,
                freq=freq,
                discharge_period_adjust_fn=discharge_period_adjust_fn,
                response_period_adjust_discharge_fn=response_period_adjust_discharge_fn,
                response_period_adjust_standby_discharge_fn=response_period_adjust_standby_discharge_fn,
                after_response_strategy_fn=after_response_strategy_fn,
            )

    if is_in_window(current_time, time_points["19:00"], time_points["21:00"]):
        logger.info("debug::当前时刻(通知时刻)处于响应日 [19:00~21:00)...")
        first_strategy_change_time = find_first_strategy_change_time(
            df_strategy_raw, df_strategy_period_new
        )
        logger.info(f"debug::first_strategy_change_time: {first_strategy_change_time}")
        if first_strategy_change_time is None:
            logger.info("debug::需求响应策略未发生变化，保持原策略...")
            return df_strategy_period_new
        if current_time <= first_strategy_change_time:
            logger.info("debug::当前时刻(通知时刻) <= 需求响应第一个峰时放电策略修改开始的时刻, 基本规则不变...")
            return df_strategy_period_new
        logger.info("debug::当前时刻(通知时刻) > 需求响应第一个峰时放电策略修改开始的时刻...")
        return apply_canceled_peak_response(
            df_strategy_raw=df_strategy_raw,
            df_strategy_copy=df_strategy_copy,
            peak_period=period_map["peak2_discharge"],
            current_time=current_time,
            period_map=period_map,
            max_discharge_load=peak2_max_discharge_load,
            max_charge_load=max_charge_load,
            freq=freq,
            discharge_period_adjust_fn=discharge_period_adjust_fn,
            response_period_adjust_discharge_fn=response_period_adjust_discharge_fn,
            response_period_adjust_standby_discharge_fn=response_period_adjust_standby_discharge_fn,
            after_response_strategy_fn=after_response_strategy_fn,
        )

    if is_at_or_after(current_time, time_points["21:00"]):
        logger.info("debug::当前时刻(通知时刻) >= 响应日 21:00, 不参与需求响应...")
        return None

    return df_strategy_period_new


def dispatch_day_strategy_by_mode(*, response_mode: str, **kwargs):
    """
    按 response_mode 选择白天场景的后置调度强度。
    """
    logger.info(f"debug::日间模式后置调度, response_mode={response_mode}")
    if response_mode == "日前":
        return _dispatch_day_realtime_strategy(**kwargs)
    if response_mode == "日内":
        return _dispatch_day_intraday_strategy(**kwargs)
    if response_mode == "日内-快速":
        return _dispatch_day_fast_strategy(**kwargs)
    raise ValueError(f"Unsupported response_mode: {response_mode}")
