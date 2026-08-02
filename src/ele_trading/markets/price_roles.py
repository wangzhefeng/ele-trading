"""兼容导出入口：权威实现已下沉至 ``ele_trading.domain.price_roles``。

价格角色是 forecasting/scenario（位于 markets 之下）也需要引用的
底层词汇，按 v3 依赖方向 domain ← markets，实现只能存放在 domain。
"""

from ele_trading.domain.price_roles import PriceRole, normalize_price_role

__all__ = ["PriceRole", "normalize_price_role"]
