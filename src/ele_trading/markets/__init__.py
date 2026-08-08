"""markets — 市场规则插件层。

按结算模式（而非地区名）组织子包；每个子包是一个自包含的规则插件
（配置契约 + 加载校验 + 结算引擎 + MarketMode 装配）。``shared``
提供跨模式通用结算工具；``sections`` 提供主链共享的组合式配置
子对象词汇；``protocol`` 定义主链唯一依赖的 MarketMode /
SettlementEngine 协议（v3 M4 / D-002）。
当前插件：

- ``single_settlement``：单结算模式（实时电能 + 中长期差价 + 回收及逐项
  调整），规则研究参考蒙西市场规则。
- ``dual_settlement``：双结算（偏差带考核）模式（量价结算 C/差价结算 C2 +
  日前偏差考核 + 中长期回收），规则研究参考蒙西 v1.3 双结算设计；
  当前为带测试的规则引擎库，未接入主链编排。

包级导入采用 PEP 562 惰性加载：``import ele_trading.markets.price_roles``
等子模块不会连带加载两个结算插件，保证底层包（scenario/forecasting）
可以引用价格角色词汇而不触达市场插件层。
"""

from typing import Any

__all__ = [
    "MarketMode",
    "MarketProfile",
    "PriceRoleCapability",
    "PriceRole",
    "SettlementEngine",
    "normalize_price_role",
    "dual_settlement",
    "single_settlement",
]


def __getattr__(name: str) -> Any:
    if name == "MarketProfile":
        from .profile import MarketProfile

        return MarketProfile
    if name in ("MarketMode", "PriceRoleCapability", "SettlementEngine"):
        from .protocol import MarketMode, PriceRoleCapability, SettlementEngine

        return {
            "MarketMode": MarketMode,
            "PriceRoleCapability": PriceRoleCapability,
            "SettlementEngine": SettlementEngine,
        }[name]
    if name in ("PriceRole", "normalize_price_role"):
        from ele_trading.domain.price_roles import PriceRole, normalize_price_role

        return {
            "PriceRole": PriceRole,
            "normalize_price_role": normalize_price_role,
        }[name]
    if name == "dual_settlement":
        from . import dual_settlement

        return dual_settlement
    if name == "single_settlement":
        from . import single_settlement

        return single_settlement
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
