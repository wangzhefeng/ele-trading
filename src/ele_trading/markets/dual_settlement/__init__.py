"""dual_settlement — 双结算（偏差带考核）模式市场规则插件。

结算引擎：量价结算 C、差价结算 C2（两种口径代数恒等）、日前偏差考核
Cpen_dayah、中长期月度回收 Cpen_long。规则研究参考蒙西 v1.3 双结算
设计（§10.1）；规则参数以 configs 数据注入，不硬编码。

当前为带测试的规则引擎库，未接入主链编排（参与者角色差异——v1 报量
报价日前不随本插件移植）。
"""

from .config_loader import load_market_config
from .contracts import MarketConfig, SettlementReport
from .settlement import (
    aggregate_to_settle_periods,
    compute_cpen_dayah,
    compute_cpen_long,
    compute_settlement_C,
    compute_settlement_C2,
)

__all__ = [
    "MarketConfig",
    "SettlementReport",
    "aggregate_to_settle_periods",
    "compute_cpen_dayah",
    "compute_cpen_long",
    "compute_settlement_C",
    "compute_settlement_C2",
    "load_market_config",
]
