from datetime import timedelta
from typing import Dict, Tuple

import pandas as pd
from utils.log_util import logger

DAY_START_TIME = "08:00:00"
DAY_END_TIME = "21:55:00"
DAY_DATA_END_TIME = "22:00:00"
DAY_CHARGE_START_TIME = "11:00:00"
DAY_CHARGE_END_TIME = "17:55:00"
PEAK1_START_TIME = "08:00:00"
PEAK1_END_TIME = "09:55:00"
PEAK2_START_TIME = "19:00:00"
PEAK2_END_TIME = "20:55:00"
NIGHT_CHARGE_START_TIME = "22:00:00"
NIGHT_CHARGE_END_TIME = "05:55:00"
NIGHT_DATA_END_TIME = "11:00:00"
MIDNIGHT_BOUNDARY_TIME = "23:55:00"
ALLOWED_RESPONSE_MODES = {"日前", "日内", "日内-快速"}

class PeriodProfileBuilder:
    """
    构造业务视角的时间画像，定义响应属于白天还是跨夜以及各类窗口。
    """
    def __init__(self, response_period: Dict, current_time, response_mode: str) -> None:
        self.response_period = response_period
        self.current_time = current_time
        self.response_mode = response_mode
        self.response_start = response_period["start"]
        self.response_end = response_period["end"]
        self.response_start_date = self.response_start.date()
        self.response_end_date = self.response_end.date()
        self.period_type = get_response_period_type(response_period)

    def build(self) -> Dict:
        """
        按白天或跨夜模式生成完整的 period_profile。
        """
        base_profile = self._build_base_profile()
        if self.period_type == "day":
            return self._build_day_profile(base_profile)
        return self._build_night_profile(base_profile)

    def _build_base_profile(self) -> Dict:
        """
        构造白天和跨夜场景共享的基础时间字段。
        """
        return {
            "period_type": self.period_type,
            "response_mode": self.response_mode,
            "current_time": self.current_time,
            "response_start": self.response_start,
            "response_end": self.response_end,
            "response_start_date": self.response_start_date,
            "response_end_date": self.response_end_date,
            "cross_midnight_mode": self.response_start_date != self.response_end_date,
            "midnight_boundary_policy": (
                "cross_midnight_2355_0000"
                if self.response_start.time() <= pd.to_datetime(MIDNIGHT_BOUNDARY_TIME).time()
                and self.response_end_date != self.response_start_date
                else "standard"
            ),
        }

    def _build_day_profile(self, base_profile: Dict) -> Dict:
        """
        构造白天场景的取数范围、策略范围和峰充放窗口。
        """
        response_reference_date = self.response_start_date
        return {
            **base_profile,
            "response_reference_date": response_reference_date,
            "coef_reference_date": response_reference_date,
            "history_cutoff_hour": 22,
            "fillna_history": False,
            "recompute_missing_aidc": True,
            "data_check_mode": "day",
            "response_check_mode": "day",
            "data_bounds": {
                "start": f"{response_reference_date - timedelta(days=30)} 00:00:00",
                "end": f"{response_reference_date} {DAY_DATA_END_TIME}",
            },
            "strategy_bounds": {
                "start": f"{response_reference_date - timedelta(days=1)} 22:00:00",
                "end": f"{response_reference_date} {DAY_DATA_END_TIME}",
            },
            "peak1_discharge": {
                "start": pd.to_datetime(f"{response_reference_date} {PEAK1_START_TIME}"),
                "end": pd.to_datetime(f"{response_reference_date} {PEAK1_END_TIME}"),
            },
            "peak2_discharge": {
                "start": pd.to_datetime(f"{response_reference_date} {PEAK2_START_TIME}"),
                "end": pd.to_datetime(f"{response_reference_date} {PEAK2_END_TIME}"),
            },
            "charge": {
                "start": pd.to_datetime(f"{response_reference_date} {DAY_CHARGE_START_TIME}"),
                "end": pd.to_datetime(f"{response_reference_date} {DAY_CHARGE_END_TIME}"),
            },
        }

    def _build_night_profile(self, base_profile: Dict) -> Dict:
        """
        构造跨夜场景的参考日、取数范围和峰充放窗口。
        """
        response_reference_date = self.response_end_date
        data_bounds, strategy_bounds, peak1_discharge, peak2_discharge, charge = self._build_night_period_windows()
        coef_reference_date = self._resolve_night_coef_reference_date(response_reference_date)
        return {
            **base_profile,
            "response_reference_date": response_reference_date,
            "coef_reference_date": coef_reference_date,
            "history_cutoff_hour": 11,
            "fillna_history": True,
            "recompute_missing_aidc": False,
            "data_check_mode": "night",
            "response_check_mode": "night",
            "data_bounds": data_bounds,
            "strategy_bounds": strategy_bounds,
            "peak1_discharge": peak1_discharge,
            "peak2_discharge": peak2_discharge,
            "charge": charge,
        }

    def _build_night_period_windows(self) -> Tuple[Dict, Dict, Dict, Dict, Dict]:
        """
        按跨夜响应落点生成晚间、凌晨和白天延伸三类窗口。
        """
        window_type = self._get_night_window_type()

        if window_type == "late_evening":
            return (
                {
                    "start": f"{self.response_end_date - timedelta(days=30)} 00:00:00",
                    "end": f"{self.response_end_date + timedelta(days=1)} {NIGHT_DATA_END_TIME}",
                },
                {
                    "start": f"{self.response_end_date} {DAY_CHARGE_START_TIME}",
                    "end": f"{self.response_end_date + timedelta(days=1)} {NIGHT_DATA_END_TIME}",
                },
                {
                    "start": pd.to_datetime(f"{self.response_end_date} {PEAK2_START_TIME}"),
                    "end": pd.to_datetime(f"{self.response_end_date} {PEAK2_END_TIME}"),
                },
                {
                    "start": pd.to_datetime(f"{self.response_end_date + timedelta(days=1)} {PEAK1_START_TIME}"),
                    "end": pd.to_datetime(f"{self.response_end_date + timedelta(days=1)} {PEAK1_END_TIME}"),
                },
                {
                    "start": pd.to_datetime(f"{self.response_end_date} {NIGHT_CHARGE_START_TIME}"),
                    "end": pd.to_datetime(f"{self.response_end_date + timedelta(days=1)} {NIGHT_CHARGE_END_TIME}"),
                },
            )

        if window_type == "early_morning":
            return (
                {
                    "start": f"{self.response_end_date - timedelta(days=30)} 00:00:00",
                    "end": f"{self.response_end_date} {NIGHT_DATA_END_TIME}",
                },
                {
                    "start": f"{self.response_end_date - timedelta(days=1)} {DAY_CHARGE_START_TIME}",
                    "end": f"{self.response_end_date} {NIGHT_DATA_END_TIME}",
                },
                {
                    "start": pd.to_datetime(f"{self.response_end_date - timedelta(days=1)} {PEAK2_START_TIME}"),
                    "end": pd.to_datetime(f"{self.response_end_date - timedelta(days=1)} {PEAK2_END_TIME}"),
                },
                {
                    "start": pd.to_datetime(f"{self.response_end_date} {PEAK1_START_TIME}"),
                    "end": pd.to_datetime(f"{self.response_end_date} {PEAK1_END_TIME}"),
                },
                {
                    "start": pd.to_datetime(f"{self.response_end_date - timedelta(days=1)} {NIGHT_CHARGE_START_TIME}"),
                    "end": pd.to_datetime(f"{self.response_end_date} {NIGHT_CHARGE_END_TIME}"),
                },
            )

        return (
            {
                "start": f"{self.response_end_date - timedelta(days=30)} 00:00:00",
                "end": f"{self.response_end_date + timedelta(days=1)} {DAY_DATA_END_TIME}",
            },
            {
                "start": f"{self.response_end_date - timedelta(days=1)} {DAY_DATA_END_TIME}",
                "end": f"{self.response_end_date} {DAY_DATA_END_TIME}",
            },
            {
                "start": pd.to_datetime(f"{self.response_end_date} {PEAK1_START_TIME}"),
                "end": pd.to_datetime(f"{self.response_end_date} {PEAK1_END_TIME}"),
            },
            {
                "start": pd.to_datetime(f"{self.response_end_date} {PEAK2_START_TIME}"),
                "end": pd.to_datetime(f"{self.response_end_date} {PEAK2_END_TIME}"),
            },
            {
                "start": pd.to_datetime(f"{self.response_end_date} {DAY_CHARGE_START_TIME}"),
                "end": pd.to_datetime(f"{self.response_end_date} {DAY_CHARGE_END_TIME}"),
            },
        )

    def _get_night_window_type(self) -> str:
        """
        判断跨夜响应主要落在晚间、凌晨还是次日日间延伸段。
        """
        if 22 <= self.response_end.hour <= 23:
            return "late_evening"
        if 0 <= self.response_end.hour <= 7:
            return "early_morning"
        return "daytime_extension"

    def _resolve_night_coef_reference_date(self, response_reference_date):
        """
        确定跨夜场景下基线系数应参考哪一天。
        """
        baseline_coef_start = self.response_start - timedelta(hours=2.5)
        if baseline_coef_start.date() != response_reference_date:
            return response_reference_date - timedelta(days=1)
        return response_reference_date

class PeriodMapBuilder:
    """
    把业务时间画像物化成运行时真正使用的时间表和辅助窗口。
    """
    def __init__(self, response_period: Dict, current_time, data_bounds: Dict, strategy_bounds: Dict) -> None:
        self.response_period = response_period
        self.current_time = current_time
        self.data_bounds = data_bounds
        self.strategy_bounds = strategy_bounds

    def build(self) -> Dict:
        """
        生成主流程需要的 data/history/future/strategy/response 等时段对象。
        """
        data_period_df, data_period = self._build_period_frame(
            self.data_bounds["start"],
            self.data_bounds["end"],
        )
        history_period_df, history_period, future_period_df, future_period = self._build_history_future_frames(
            data_period=data_period,
        )
        strategy_period_df, strategy_period = self._build_period_frame(
            self.strategy_bounds["start"],
            self.strategy_bounds["end"],
        )
        response_period_df, normalized_response_period, response_period_df_15min, response_period_15min = (
            self._build_response_frames()
        )
        aux_periods = self._build_common_auxiliary_periods(normalized_response_period)
        return {
            "data_df": data_period_df,
            "data": data_period,
            "history_df": history_period_df,
            "history": history_period,
            "future_df": future_period_df,
            "future": future_period,
            "strategy_df": strategy_period_df,
            "strategy": strategy_period,
            "response_df": response_period_df,
            "response": normalized_response_period,
            "response_df_15min": response_period_df_15min,
            "response_15min": response_period_15min,
            "baseline_coef": aux_periods["baseline_coef"],
            "climbing": aux_periods["climbing"],
            "response_before_1h": aux_periods["response_before_1h"],
            "response_after_1h": aux_periods["response_after_1h"],
        }

    def _build_period_frame(self, start, end, inclusive: str = "left", freq: str = "5min"):
        """
        把任意时间边界转换成统一的时间序列和起止字典。
        """
        period_time_range = pd.date_range(start, end, freq=freq, inclusive=inclusive)
        period_df = pd.DataFrame({"time": period_time_range})
        period = {"start": min(period_time_range), "end": max(period_time_range)}
        return period_df, period

    def _build_history_future_frames(self, data_period: Dict):
        """
        用 current_time 将完整数据窗口切成历史段和未来段。
        """
        history_period_df, history_period = self._build_period_frame(
            data_period["start"],
            self.current_time,
            inclusive="left",
        )
        future_period_df, future_period = self._build_period_frame(
            self.current_time,
            data_period["end"],
            inclusive="both",
        )
        return history_period_df, history_period, future_period_df, future_period

    def _build_response_frames(self):
        """
        同时生成 5 分钟和 15 分钟粒度的响应时段表。
        """
        response_period_df, normalized_response_period = self._build_period_frame(
            self.response_period["start"],
            self.response_period["end"],
            inclusive="left",
        )
        response_period_df_15min, response_period_15min = self._build_period_frame(
            normalized_response_period["start"],
            normalized_response_period["end"],
            inclusive="left",
            freq="15min",
        )
        return response_period_df, normalized_response_period, response_period_df_15min, response_period_15min

    def _build_common_auxiliary_periods(self, response_period: Dict):
        """
        生成基线系数、爬坡和响应前后 1 小时等辅助窗口。
        """
        return {
            "baseline_coef": {
                "start": response_period["start"] - timedelta(hours=2.5),
                "end": response_period["start"] - timedelta(hours=0.5) - timedelta(minutes=5),
            },
            "climbing": {
                "start": response_period["start"] - timedelta(hours=0.5),
                "end": response_period["start"] - timedelta(minutes=5),
            },
            "response_before_1h": {
                "start": response_period["start"] - timedelta(hours=1),
                "end": response_period["start"] - timedelta(minutes=5),
            },
            "response_after_1h": {
                "start": response_period["end"] + timedelta(minutes=5),
                "end": response_period["end"] + timedelta(hours=1),
            },
        }

# Public API helpers
def get_response_period_type(response_period: Dict) -> str:
    """
    根据响应开始时刻判断任务属于白天还是跨夜场景。
    """
    response_start = response_period["start"]
    response_date = response_start.date()
    day_start = pd.to_datetime(f"{response_date} {DAY_START_TIME}")
    day_end = pd.to_datetime(f"{response_date} {DAY_END_TIME}")
    if day_start <= response_start <= day_end:
        return "day"
    return "night"

def preprocessing_period(response_period: Dict, current_time, response_mode: str, verbose: bool = True) -> Dict:
    """
    统一输出主链路使用的完整时间上下文。
    """
    if response_mode not in ALLOWED_RESPONSE_MODES:
        raise ValueError(f"Unsupported response_mode: {response_mode}")
    period_profile = PeriodProfileBuilder(response_period, current_time, response_mode).build()
    period_map = PeriodMapBuilder(
        response_period=response_period,
        current_time=period_profile["current_time"],
        data_bounds=period_profile["data_bounds"],
        strategy_bounds=period_profile["strategy_bounds"],
    ).build()
    period_context = {
        "current_time": period_profile["current_time"],
        "current_date": period_profile["current_time"].date(),
        "response_date": period_profile["response_reference_date"],
        "response_start_date": period_profile["response_start_date"],
        "response_end_date": period_profile["response_end_date"],
        "response_mode": period_profile["response_mode"],
        "period_profile": period_profile,
        **period_map,
        "peak1_discharge": period_profile["peak1_discharge"],
        "peak2_discharge": period_profile["peak2_discharge"],
        "charge": period_profile["charge"],
    }
    if verbose:
        logger.info(f"debug::period_type: {period_profile['period_type']}")
        logger.info(f"debug::response_mode: {period_profile['response_mode']}")
        logger.info(f"debug::current_time: {period_context['current_time']}")
        logger.info(f"debug::current_date: {period_context['current_date']}")
        logger.info(f"debug::response_date: {period_context['response_date']}")
        logger.info(f"debug::response_start_date: {period_context['response_start_date']}")
        logger.info(f"debug::response_end_date: {period_context['response_end_date']}")
        logger.info(f"debug::data_period: {period_context['data']}")
        logger.info(f"debug::history_period: {period_context['history']}")
        logger.info(f"debug::future_period: {period_context['future']}")
        logger.info(f"debug::strategy_period: {period_context['strategy']}")
        logger.info(f"debug::baseline_coef_period: {period_context['baseline_coef']}")
        logger.info(f"debug::climbing_period: {period_context['climbing']}")
        logger.info(f"debug::response_before_1h_period: {period_context['response_before_1h']}")
        logger.info(f"debug::response_period: {period_context['response']}")
        logger.info(f"debug::response_after_1h_period: {period_context['response_after_1h']}")
        logger.info(f"debug::response_period_15min: {period_context['response_15min']}")
        logger.info(f"debug::peak1_discharge_period: {period_context['peak1_discharge']}")
        logger.info(f"debug::peak2_discharge_period: {period_context['peak2_discharge']}")
        logger.info(f"debug::charge_period: {period_context['charge']}")
    return period_context
