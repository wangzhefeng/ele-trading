"""交易事件契约骨架：Forecast → Bid → Award → Dispatch → Metering → Settlement。

对应路线文档 Phase 0 的事件契约占位。当前为 typed 骨架（仅公共字段），
字段语义：

- ``issue_time``：事件签发时刻（决策时点，防前瞻边界）；
- ``valid_time``：事件生效/交割时刻；
- ``version``：数据或规则版本标识（溯源用）；
- ``source``：事件来源（如 "forecast"/"market"/"metering"）。

完整市场日历、交割时段与单位系统在 Phase 0 正式启动时补全。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class TradingEvent:
    """全部交易事件的公共基座：签发时刻、生效时刻、版本与来源。"""

    issue_time: pd.Timestamp
    valid_time: pd.Timestamp
    version: str
    source: str


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
