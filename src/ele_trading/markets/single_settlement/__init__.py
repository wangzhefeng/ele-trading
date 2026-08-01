"""single_settlement — 单结算模式市场规则插件。

包含：市场配置契约（``MarketConfig``）与加载校验（``load_market_config``）、
结算报告契约（``SettlementReport``）、单结算引擎（``settlement.py``：
实时电能 + 中长期差价 + 月度回收 + DR/退化/执行分项）。
规则研究参考蒙西市场规则；规则参数以 configs 数据注入，不硬编码。
"""

from .config_loader import load_market_config
from .contracts import MarketConfig, SettlementReport
from .settlement import (
    aggregate_to_settle_periods,
    build_settlement_report,
    compute_contract_difference,
    compute_dr_settlement,
    compute_energy_cost,
    compute_long_recovery,
)

__all__ = [
    "MarketConfig",
    "SettlementReport",
    "aggregate_to_settle_periods",
    "build_settlement_report",
    "compute_contract_difference",
    "compute_dr_settlement",
    "compute_energy_cost",
    "compute_long_recovery",
    "load_market_config",
]
