from typing import Dict

import pandas as pd

from model.model_packages.Demand_Response_optim.engine.period_context import (
    DAY_DATA_END_TIME,
    DAY_END_TIME,
    DAY_START_TIME,
    NIGHT_CHARGE_START_TIME,
)


def normalize_response_period(response_period: Dict) -> Dict:
    """
    把外部输入的响应时段统一标准化为 Timestamp。
    """
    return {
        "start": pd.to_datetime(response_period["start"]),
        "end": pd.to_datetime(response_period["end"]),
    }



def day_response_period_allowed(response_period: Dict) -> bool:
    """
    判断白天响应是否完整落在允许参与的日间窗口内。
    """
    normalized_period = normalize_response_period(response_period)
    response_start = normalized_period["start"]
    response_end = normalized_period["end"]
    response_date = response_start.date()
    day_start = pd.to_datetime(f"{response_date} {DAY_START_TIME}")
    day_end = pd.to_datetime(f"{response_date} {DAY_DATA_END_TIME}")
    return (
        response_start.date() == response_end.date()
        and response_start >= day_start
        and response_end <= day_end
    )


def day_notification_allowed(current_time, response_date) -> bool:
    """
    判断白天场景的通知时刻是否早于允许截止时间。
    """
    normalized_current_time = pd.to_datetime(current_time)
    notification_deadline = pd.to_datetime(f"{response_date} {DAY_END_TIME}")
    return normalized_current_time < notification_deadline


def night_response_period_allowed(response_period: Dict) -> bool:
    """
    判断跨夜响应是否位于夜间允许窗口，包括同日夜段与跨日夜段。
    """
    normalized_period = normalize_response_period(response_period)
    response_start = normalized_period["start"]
    response_end = normalized_period["end"]
    response_start_date = response_start.date()
    response_end_date = response_end.date()

    if response_start_date == response_end_date:
        early_morning_allowed = (
            response_start >= pd.to_datetime(f"{response_end_date} 00:00:00")
            and response_end <= pd.to_datetime(f"{response_end_date} {DAY_START_TIME}")
        )
        late_evening_allowed = (
            response_start >= pd.to_datetime(f"{response_end_date} {NIGHT_CHARGE_START_TIME}")
            and response_end <= pd.to_datetime(f"{response_end_date + pd.Timedelta(days=1)} 00:00:00")
        )
        return early_morning_allowed or late_evening_allowed

    return (
        response_start >= pd.to_datetime(f"{response_start_date} {NIGHT_CHARGE_START_TIME}")
        and response_end <= pd.to_datetime(f"{response_end_date} {DAY_START_TIME}")
    )
