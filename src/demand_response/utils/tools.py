from typing import Dict, Set
from datetime import timedelta

import pandas as pd


def extract_daily_period_data(data: pd.DataFrame, data_col: str, time_period: Dict):
    """
    从带有 DatetimeIndex 的 DataFrame 中提取每天指定时间窗口的数据
    """
    df = data.copy()
    # 确保索引是 DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        df.set_index("time", inplace=True)
    # 转换时间窗口为 time 对象
    if time_period["start"].hour > time_period["end"].hour:
        period_start_1, period_end_1 = time_period["start"].time(), pd.to_datetime("2025-01-01 23:59:59").time()
        period_start_2, period_end_2 = pd.to_datetime("2025-01-01 00:00:00").time(), time_period["end"].time()
        # 筛选每天在时间窗口内的行
        mask = ((df.index.time >= period_start_1) & (df.index.time <= period_end_1)) | \
            ((df.index.time >= period_start_2) & (df.index.time <= period_end_2))
    else:
        period_start, period_end = time_period["start"].time(), time_period["end"].time()
        # 筛选每天在时间窗口内的行
        mask = (df.index.time >= period_start) & (df.index.time <= period_end)
    filtered_df = df[mask].copy()
    # logger.info(f"debug::filtered_df: \n{filtered_df}")
    # 提取日期和纯时间
    filtered_df['date_col'] = filtered_df.index.date
    filtered_df['time_col'] = filtered_df.index.time
    # 透视：time 为行，date 为列
    filtered_df = filtered_df[[data_col, "date_col", "time_col"]]
    result = filtered_df.pivot_table(
        index='time_col',
        columns='date_col',
        values=data_col,
        aggfunc='first'  # 防止重复时间（理论上不会）
    )
    result = result.sort_index()
    result = result[sorted(result.columns)]

    return result

def find_adjacent_date(dates_list: Set):
    """
    查找相邻（连续）的日期对
    """
    dates_list = list(dates_list)
    dates_list = [pd.to_datetime(item) for item in dates_list]
    dates_list.sort()
    
    adjacent_pairs = []
    for i in range(len(dates_list) - 1):
        if dates_list[i + 1] - dates_list[i] == timedelta(days=1):
            adjacent_pairs.append(
                (dates_list[i].strftime("%Y-%m-%d"), dates_list[i + 1].strftime("%Y-%m-%d"))
            )

    return adjacent_pairs

def peak_time_period(time_period):
    """
    滚动需求响应表格结果
    """
    time_period = int(time_period)
    if time_period < 11:
        return "其他 10:00~11:00"
    elif time_period >= 11 and time_period < 14:
        return "早峰 11:00~14:00"
    elif time_period >= 14 and time_period < 18:
        return "午峰 14:00~18:00"
    else:
        return "晚峰 18:00~22:00"
