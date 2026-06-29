from datetime import datetime, timedelta
from typing import List, Tuple


def generate_month_ranges(start_time: datetime, end_time: datetime) -> List[Tuple[datetime, datetime]]:
    """将时间范围按月分割为 (月开始, 月结束) 元组列表。"""
    if start_time >= end_time:
        return []

    result = []
    current = start_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current < end_time:
        if current.month == 12:
            next_month_start = current.replace(
                year=current.year + 1, month=1, day=1,
                hour=0, minute=0, second=0, microsecond=0,
            )
        else:
            next_month_start = current.replace(
                month=current.month + 1, day=1,
                hour=0, minute=0, second=0, microsecond=0,
            )
        result.append((current, next_month_start))
        current = next_month_start
        if current >= end_time:
            break

    return result


def generate_day_pairs(start_time: datetime, end_time: datetime) -> List[Tuple[datetime, datetime]]:
    """将时间范围按天分割为 (天开始, 天结束) 元组列表。"""
    time_point_list = []
    current_time = start_time
    while current_time < end_time:
        time_point_list.append((current_time, current_time + timedelta(days=1)))
        current_time += timedelta(days=1)
    return time_point_list


def get_time_ranges(start_time: datetime, end_time: datetime, strategy: str) -> List[Tuple[datetime, datetime]]:
    """根据策略名称获取时间分割结果。"""
    if strategy == "month":
        return generate_month_ranges(start_time, end_time)
    elif strategy == "day":
        return generate_day_pairs(start_time, end_time)
    else:
        raise ValueError(f"Unknown time splitting strategy: {strategy}")
