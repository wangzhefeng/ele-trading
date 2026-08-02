"""交易事件契约：Forecast → Bid → Award → Dispatch → Metering → Settlement。

v3 M0 补全：市场日历（``MarketCalendar``）、交割时段与单位字段。
事件是决策追踪的载体：任一交易决策可经事件链回溯输入版本、
配置版本和求解状态（v3 不变量 7、决策 D-006）。

字段语义：

- ``issue_time``：事件签发时刻（决策时点，防前瞻边界）；
- ``valid_time``：事件生效/交割时段起点；
- ``calendar``：市场日历（市场代码、时区、交割粒度、交易日时段数）；
- ``unit``：事件承载量的量纲声明（如 ``MW``、``MWh``、``CNY/MWh``）；
- ``version``：数据或规则版本标识（溯源用）；
- ``source``：事件来源（如 ``"forecast"``/``"market"``/``"metering"``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


# ---------------- 校验辅助（与 forecasting.contracts 同风格，domain 不依赖上层） ----------------


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_aware_timestamp(value: pd.Timestamp, field_name: str) -> None:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(
            f"{field_name} must be a valid timezone-aware timestamp"
        )


# ---------------- 市场日历 ----------------


@dataclass(frozen=True, slots=True)
class MarketCalendar:
    """市场日历：市场代码、时区、交割粒度与交易日时段数。"""

    market: str           # 市场代码，如 "mengxi"
    tz: str               # 时区，如 "Asia/Shanghai"
    freq_minutes: int     # 交割时段粒度（分钟），现货主链 15
    settle_periods: int   # 每交易日交割时段数，96

    def __post_init__(self) -> None:
        _require_non_empty(self.market, "market")
        _require_non_empty(self.tz, "tz")
        try:
            ZoneInfo(self.tz)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"tz must be a valid IANA timezone: {self.tz!r}") from exc
        for name, value in (
            ("freq_minutes", self.freq_minutes),
            ("settle_periods", self.settle_periods),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.freq_minutes * self.settle_periods != 24 * 60:
            raise ValueError(
                "settle_periods * freq_minutes must cover a full trading day"
            )


# ---------------- 事件基座与子类 ----------------


@dataclass(slots=True)
class TradingEvent:
    """全部交易事件的公共基座。"""

    issue_time: pd.Timestamp
    valid_time: pd.Timestamp
    version: str
    source: str
    calendar: MarketCalendar
    unit: str

    def __post_init__(self) -> None:
        _require_aware_timestamp(self.issue_time, "issue_time")
        _require_aware_timestamp(self.valid_time, "valid_time")
        _require_non_empty(self.version, "version")
        _require_non_empty(self.source, "source")
        _require_non_empty(self.unit, "unit")
        if not isinstance(self.calendar, MarketCalendar):
            raise ValueError("calendar must be a MarketCalendar")

    @property
    def delivery_period(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        """交割时段：[valid_time, valid_time + freq_minutes)。"""
        start = cast(pd.Timestamp, pd.Timestamp(self.valid_time))
        end = cast(
            pd.Timestamp,
            start + pd.Timedelta(minutes=self.calendar.freq_minutes),
        )
        return start, end


@dataclass(slots=True)
class ForecastEvent(TradingEvent):
    """预测事件：某签发时刻对未来有效时刻的预测量。"""


@dataclass(slots=True)
class BidEvent(TradingEvent):
    """申报事件：向市场提交的量价申报。"""


@dataclass(slots=True)
class AwardEvent(TradingEvent):
    """成交事件：市场出清后返回的成交结果。"""


@dataclass(slots=True)
class DispatchEvent(TradingEvent):
    """调度事件：资源侧形成的运行计划或指令。"""


@dataclass(slots=True)
class MeteringEvent(TradingEvent):
    """计量事件：执行后的实测电量。"""


@dataclass(slots=True)
class SettlementEvent(TradingEvent):
    """结算事件：按市场规则核算的财务结果。"""


# ------------------------------------------------------------------ #
#  事件链派生（v3 M5 / D-006）
# ------------------------------------------------------------------ #

#: 参与 input_versions 派生的事件类型（Forecast/Award 携带输入版本）
_INPUT_VERSION_EVENT_TYPES = (ForecastEvent, AwardEvent)


def derive_input_versions(
    events: tuple[TradingEvent, ...] | list[TradingEvent],
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """从事件链派生 input_versions：{event.source: event.version}。

    DecisionTrace 的输入版本不再由编排器手工拼装，而由事件链唯一
    派生；``extra`` 用于事件之外的版本项（如 forecast_registry）。
    同一 source 出现多次时后者覆盖前者，调用方应保证 source 唯一。
    """
    versions = {
        event.source: event.version
        for event in events
        if isinstance(event, _INPUT_VERSION_EVENT_TYPES)
    }
    if extra:
        versions.update(extra)
    return versions
