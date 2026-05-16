from datetime import datetime, timedelta, time
import pandas as pd
import copy

def generate_days(start_time, end_time):
    time_point_list = []
    
    current_time = start_time
    while current_time < end_time:
        time_point_list.append(current_time)
        current_time += timedelta(days=1)
    return time_point_list

def generate_hours(start_time, end_time):
    time_point_list = []
    
    current_time = start_time
    while current_time < end_time:
        time_point_list.append(current_time)
        current_time += timedelta(hours=1)
    return time_point_list

def generate_quarters(start_time, end_time):
    time_point_list = []
    
    current_time = start_time
    while current_time < end_time:
        time_point_list.append(current_time)
        current_time += timedelta(minutes=15)
    return time_point_list

def generate_5mins(start_time, end_time):
    time_point_list = []
    
    current_time = start_time
    while current_time < end_time:
        time_point_list.append(current_time)
        current_time += timedelta(minutes=5)
    return time_point_list

def end_of_that_day(current_day_time):
    next_day_time = current_day_time + timedelta(days=1)
    end_of_current_day_time = datetime.combine(next_day_time.date(), time.min)
    # end_of_current_day_time_str = end_of_current_day_time.strftime("%Y-%m-%d %H:%M:%S")
    
    # return end_of_current_day_time_str
    return end_of_current_day_time

def start_of_this_es_cycle(current_time, division_hour):
    # 获取当前日期的储能充放周期分割点
    current_date = current_time.date()
    division_time_first = datetime.combine(current_date, datetime.min.time().replace(hour = division_hour))
    
    if current_time < division_time_first:
        # 如果输入时间大于或等于今天的分割点，则返回下一个的分割点
        return division_time_first - timedelta(days=1)
    else:
        # 否则返回今天的分割点
        return division_time_first

def end_of_this_es_cycle(current_time, division_hour):
    # 获取当前日期的储能充放周期分割点
    current_date = current_time.date()
    division_time_first = datetime.combine(current_date, datetime.min.time().replace(hour = division_hour))
    
    if current_time >= division_time_first:
        # 如果输入时间大于或等于今天的分割点，则返回下一个的分割点
        return division_time_first + timedelta(days=1)
    else:
        # 否则返回今天的分割点
        return division_time_first


def process_time_index(raw_df: pd.DataFrame, column_name: str, new_column_name: str = "time"):
    df = copy.deepcopy(raw_df)
    # 转换时间戳类型
    df[new_column_name] = pd.to_datetime(df[column_name])
    # 去除重复时间戳
    df.drop_duplicates(subset=new_column_name, keep="last", inplace=True, ignore_index=True)
    df.set_index("time", inplace=True)

    return df


# 测试代码 main 函数
def main():
    start_time = pd.to_datetime("2026-01-01 00:00:00")
    end_time = pd.to_datetime("2026-01-02 00:00:00")
    time_point_list = generate_5mins(start_time, end_time)
    print(time_point_list)
    
    current_time = pd.to_datetime("2026-03-05 11:47:41") + timedelta(minutes=5)
    division_time_first_start = start_of_this_es_cycle(current_time, division_hour=22)
    division_time_first_end = end_of_this_es_cycle(current_time, division_hour=22)
    print(division_time_first_start)
    print(division_time_first_end)
    
    # current_time = pd.to_datetime("2026-03-05 22:47:41") + timedelta(minutes=5)
    # division_time_first_start = start_of_this_es_cycle(current_time, division_hour=22)
    # division_time_first_end = end_of_this_es_cycle(current_time, division_hour=22)
    # print(division_time_first_start)
    # print(division_time_first_end)

    print()
    # 第一优化周期时段
    opt_start_time_1st = current_time + timedelta(minutes=5)
    opt_end_time_1st = end_of_this_es_cycle(opt_start_time_1st, division_hour=22)
    print(opt_start_time_1st)
    print(opt_end_time_1st)
    # 第二优化周期时段
    opt_start_time_2nd = opt_end_time_1st
    opt_end_time_2nd = end_of_this_es_cycle(opt_start_time_2nd, division_hour=22)
    print(opt_start_time_2nd)
    print(opt_end_time_2nd)
    # 全优化周期时段输出结果收集器
    print(opt_start_time_1st)
    print(opt_end_time_2nd)
    opt_time_range = generate_5mins(opt_start_time_1st, opt_end_time_2nd)
    # print(opt_time_range)

if __name__ == "__main__":
    main()
