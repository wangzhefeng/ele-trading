from datetime import timedelta
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd

from model.model_packages.Demand_Response_optim.engine.period_context import (
    get_response_period_type,
)


FULL_COVERAGE_RESPONSE_TIME_LEN_LIST = [
    0.5, 
    1.0, 
    1.5, 
    2.0, 
    2.5,
]
FULL_COVERAGE_NOTIFICATION_HOURS_LIST = [
    0.5,  # 日内
    2.0,  # 日内
    4.0,  # 日内
    18.0, # 日前
]
DEFAULT_ROUTE_LIST = [
    "lingang_A", 
    # "lingang_B"
]


# ##############################
# 测试设备参数和输入数据
# ##############################
def build_model_cfgs() -> Dict:
    """
    构造本地回归默认使用的储能设备参数。
    """
    return {
        "current_soc_list": 0,
        "devices_info": {
            "transform_capacity": 63000,
            "invertband": 0,
            "soc_redundant_ratio": 0,
            "usable_depth": 0.98,
            "charge_loss": 0.92,
            "discharge_loss": 0.95,
            "es_charge_max": 8920,
            "es_charge_min": -8920,
            "es_capacity_max": 17888,
            "es_capacity_min": 0,
        },
    }


def load_case_input(
    project_dir: Path,
    route: str,
    response_date: str,
    current_time,
    response_start,
    response_end,
    response_mode: str,
    *,
    data_check: bool = False,
    result_visual: bool = False,
    result_save: bool = False,
) -> Dict:
    """
    加载单个测试场景所需的全部输入数据。
    """
    # 拼出指定日期和园区的测试数据目录
    project_data_dir = project_dir.joinpath(f"data/{response_date}/lingang/demand_response/{route}")
    return {
        "current_time": current_time,
        "data_check": data_check,
        "result_visual": result_visual,
        "result_save": result_save,
        "route": route,
        "response_task": True,
        "response_type": "削峰",
        "response_mode": response_mode,
        "response_period": {
            "start": response_start,
            "end": response_end,
        },
        "aidc_load_history": pd.read_csv(project_data_dir.joinpath("aidc_load_history.csv"), encoding="utf-8"),
        "demand_load_history": pd.read_csv(project_data_dir.joinpath("demand_load_history.csv"), encoding="utf-8"),
        "demand_load_predict": pd.read_csv(project_data_dir.joinpath("demand_load_predict.csv"), encoding="utf-8"),
        "strategy_load_history": pd.read_csv(project_data_dir.joinpath("strategy_load_history.csv"), encoding="utf-8"),
        "strategy_load_predict": pd.read_csv(project_data_dir.joinpath("strategy_load_predict.csv"), encoding="utf-8"),
        "ele_price": pd.read_csv(project_data_dir.joinpath("ele_price.csv"), encoding="utf-8")[["time", "price", "property"]],
        "soc_history": pd.read_csv(project_data_dir.joinpath("soc_history.csv"), encoding="utf-8"),
        "df_date": pd.read_csv(project_data_dir.joinpath("df_date.csv"), encoding="utf-8"),
    }

# ##############################
# 生成测试用例
# ##############################
def resolve_response_mode(notification_hours: float) -> str:
    """
    根据通知提前量映射业务模式。
    """
    if notification_hours >= 8.0:
        return "日前"
    if notification_hours >= 2.0:
        return "日内"
    return "日内-快速"


def generate_day_response_windows(response_date: str, response_time_len: float) -> List[Dict]:
    """
    生成白天场景下给定时长的所有响应窗口。
    """
    response_date = pd.to_datetime(response_date).date()

    response_day_start = pd.to_datetime(f"{response_date} 08:00:00")
    response_day_end = pd.to_datetime(f"{response_date} 22:00:00")
    step = timedelta(minutes=30)
    duration = timedelta(hours=response_time_len)

    cases = []
    current_start = response_day_start
    while current_start + duration <= response_day_end:
        current_end = current_start + duration
        cases.append({"start": current_start, "end": current_end})
        current_start += step
    return cases


def generate_night_response_windows(response_date: str, response_time_len: float) -> List[Dict]:
    """
    生成跨夜场景下给定时长的所有响应窗口。
    """
    response_date = pd.to_datetime(response_date).date()

    response_day_start = pd.to_datetime(f"{response_date - timedelta(days=1)} 22:00:00")
    response_day_end = pd.to_datetime(f"{response_date} 08:00:00")
    step = timedelta(minutes=30)
    duration = timedelta(hours=response_time_len)

    cases = []
    current_start = response_day_start
    while current_start + duration <= response_day_end:
        current_end = current_start + duration
        cases.append({"start": current_start, "end": current_end})
        current_start += step
    return cases


def generate_full_coverage_cases(
    response_date: str,
    response_time_len_list: Iterable[float],
    notification_hours_list: Iterable[float],
    *,
    period_type: str,
) -> List[Dict]:
    """
    生成某一类时段的全覆盖测试样例，并在样例中显式写入 period_type。
    """
    if period_type == "day":
        window_fn = generate_day_response_windows
    elif period_type == "night":
        window_fn = generate_night_response_windows
    else:
        raise ValueError(f"Unsupported period_type: {period_type}")

    cases = []
    for response_time_len in response_time_len_list:
        for window in window_fn(response_date, response_time_len):
            for notification_hours in notification_hours_list:
                current_time = window["start"] - timedelta(hours=notification_hours)
                cases.append({
                    "period_type": period_type,
                    "response_time_len": response_time_len,
                    "notification_hours": notification_hours,
                    "response_mode": resolve_response_mode(notification_hours),
                    "response_start": window["start"],
                    "response_end": window["end"],
                    "current_time": current_time,
                    "response_period_label": (
                        f"{window['start'].strftime('%Y-%m-%d %H:%M:%S')}~"
                        f"{window['end'].strftime('%Y-%m-%d %H:%M:%S')}"
                    ),
                })
    return cases


def generate_boundary_transition_cases(
    response_date: str,
    notification_hours_list: Iterable[float],
) -> List[Dict]:
    """
    生成白天与跨夜切换边界上的特殊测试样例。

    这些用例不用于覆盖普通响应窗口，而是专门验证时间语义最敏感的切换点：
    1. 21:55~22:00：白天窗口切到跨夜窗口；
    2. 23:55~00:00：跨自然日边界；
    3. 07:55~08:00：跨夜窗口切回白天窗口。
    """
    response_date = pd.to_datetime(response_date).date()
    windows = [
        {
            # 白天响应窗口的最后 5 分钟，用来验证 22:00 前后的场景切换。
            "start": pd.to_datetime(f"{response_date} 21:55:00"),
            "end": pd.to_datetime(f"{response_date} 22:00:00"),
        },
        {
            # 跨自然日边界，用来验证 23:55~次日 00:00 的日期归属和 period_type 判断。
            "start": pd.to_datetime(f"{response_date} 23:55:00"),
            "end": pd.to_datetime(f"{response_date + timedelta(days=1)} 00:00:00"),
        },
        {
            # 跨夜窗口的最后 5 分钟，用来验证 08:00 前后的场景切换。
            "start": pd.to_datetime(f"{response_date} 07:55:00"),
            "end": pd.to_datetime(f"{response_date} 08:00:00"),
        },
    ]
    cases = []
    for window in windows:
        for notification_hours in notification_hours_list:
            cases.append({
                "response_time_len": 5 / 60,
                "notification_hours": notification_hours,
                "response_mode": resolve_response_mode(notification_hours),
                "response_start": window["start"],
                "response_end": window["end"],
                "current_time": window["start"] - timedelta(hours=notification_hours),
                "response_period_label": (
                    f"{window['start'].strftime('%Y-%m-%d %H:%M:%S')}~"
                    f"{window['end'].strftime('%Y-%m-%d %H:%M:%S')}"
                ),
                "period_type": get_response_period_type(window),
            })
    return cases


def generate_all_day_coverage_cases(
    response_date: str,
    response_time_len_list: Iterable[float],
    notification_hours_list: Iterable[float],
) -> List[Dict]:
    """
    合并白天、跨夜和边界场景，形成全天覆盖回归集。
    """
    cases = []
    for period_type in ["night", "day"]:
        cases.extend(
            generate_full_coverage_cases(
            response_date=response_date,
            response_time_len_list=response_time_len_list,
            notification_hours_list=notification_hours_list,
            period_type=period_type,
            )
        )
    cases.extend(
        generate_boundary_transition_cases(
            response_date=response_date, 
            notification_hours_list=notification_hours_list
        )
    )
    return cases

# ##############################
# 把响应开始时刻映射到业务关注的全天时间分桶
# ##############################
def all_day_time_bucket(dt) -> str:
    """
    把响应开始时刻映射到业务关注的全天时间分桶。
    """
    hour = pd.to_datetime(dt).hour
    if hour < 8:
        return "夜间 00:00~07:55"
    if hour < 10:
        return "早峰 08:00~09:55"
    if hour < 14:
        return "平段 10:00~13:55"
    if hour < 18:
        return "午峰 14:00~17:55"
    if hour < 22:
        return "晚峰 18:00~21:55"
    return "夜间 22:00~23:55"
