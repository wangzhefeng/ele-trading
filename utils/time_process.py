import calendar
from datetime import datetime, timedelta


def generate_hourly_datetime_pairs(year, month, hour):
    """
    生成一个列表，列表中每个元素是一个元组。
    每个元组包含：
        - 第一个元素：某一天在指定小时的 datetime 对象
        - 第二个元素：下一天在同一小时的 datetime 对象
    
    列表范围：从输入月份的前一个月的最后一天开始，到输入月份的最后一天的入参指定的小时结束。

    :param month: 月份 (1-12)
    :param hour: 小时 (0-23)
    :return: 包含元组的列表，每个元组为 (datetime, datetime)
    """
    
    if not (1 <= month <= 12):
        raise ValueError("月份必须在 1 到 12 之间")
    if not (0 <= hour <= 23):
        raise ValueError("小时必须在 0 到 23 之间")

    # 获取当前年份
    current_year = year

    # 计算目标月份的前一个月和后一个月
    if month == 1:
        prev_month = 12
        prev_year = current_year - 1
    else:
        prev_month = month - 1
        prev_year = current_year

    if month == 12:
        next_month = 1
        next_year = current_year + 1
    else:
        next_month = month + 1
        next_year = current_year

    # 获取前一个月的最后一天
    _, days_in_prev_month = calendar.monthrange(prev_year, prev_month)
    start_day = days_in_prev_month  # 前一个月的最后一天

    # 获取目标月份的天数
    _, days_in_month = calendar.monthrange(current_year, month)

    # 获取下一个月的第一天
    end_day = 1

    result = []

    # 添加前一个月最后一天的数据
    day_datetime = datetime(prev_year, prev_month, start_day, hour)
    next_day_datetime = day_datetime + timedelta(days=1)
    result.append((day_datetime, next_day_datetime))

    # 添加目标月份每一天的数据
    for day in range(1, days_in_month + 1):
        day_datetime = datetime(current_year, month, day, hour)
        next_day_datetime = day_datetime + timedelta(days=1)
        result.append((day_datetime, next_day_datetime))

    return result


def get_month_range(month, year=None):
    """
    返回指定月份的第一天和下个月第一天的 datetime 对象。
    
    参数:
        month (int): 月份，1-12
        year (int, optional): 年份，默认为当前年份
    
    返回:
        tuple: (first_day, next_month_first_day)
               first_day: 该月第一天 00:00:00
               next_month_first_day: 下个月第一天 00:00:00
    """
    if month < 1 or month > 12:
        raise ValueError("月份必须在 1 到 12 之间")

    # 如果未指定年份，使用当前年份
    if year is None:
        year = datetime.now().year

    # 第一天
    first_day = datetime(year, month, 1, 0, 0, 0)

    # 下一个月的第一天
    if month == 12:
        next_month_first_day = datetime(year + 1, 1, 1, 0, 0, 0)
    else:
        next_month_first_day = datetime(year, month + 1, 1, 0, 0, 0)

    return first_day, next_month_first_day


def generate_day_pairs(start_time, end_time):
    time_point_list = []
    
    current_time = start_time
    while current_time < end_time:
        time_point_list.append((current_time, current_time + timedelta(days=1)))
        current_time += timedelta(days=1)
    return time_point_list
