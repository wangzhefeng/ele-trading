"""市场模式共享的价格角色词汇。"""

from __future__ import annotations

from enum import Enum


class PriceRole(str, Enum):
    """价格在预测、优化和结算中的业务语义。"""

    DAY_AHEAD_REFERENCE = "day_ahead_reference"
    DAY_AHEAD_SETTLEMENT = "day_ahead_settlement"
    REAL_TIME_SETTLEMENT = "real_time_settlement"
    SPREAD_DA_RT = "spread_da_rt"
    MID_LONG_TERM = "mid_long_term"


_LEGACY_ALIASES = {
    "real_time_reference": PriceRole.REAL_TIME_SETTLEMENT,
}

_LEGACY_SCOPES = {
    PriceRole.DAY_AHEAD_REFERENCE: "day_ahead_reference",
    PriceRole.DAY_AHEAD_SETTLEMENT: "day_ahead_reference",
    PriceRole.REAL_TIME_SETTLEMENT: "real_time_reference",
    PriceRole.SPREAD_DA_RT: "spread_da_rt",
    PriceRole.MID_LONG_TERM: "mid_long_term",
}


def normalize_price_role(value: PriceRole | str) -> PriceRole:
    """把规范值或旧 price scope 转为唯一价格角色。"""
    if isinstance(value, PriceRole):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("price_role must not be empty")
    normalized = value.strip().lower()
    if normalized in _LEGACY_ALIASES:
        return _LEGACY_ALIASES[normalized]
    try:
        return PriceRole(normalized)
    except ValueError as exc:
        supported = ", ".join(role.value for role in PriceRole)
        raise ValueError(
            f"unknown price_role {value!r}; expected one of {supported}"
        ) from exc


def legacy_price_scope(role: PriceRole | str) -> str:
    """返回旧 provider 使用的 scope，供迁移期兼容。"""
    return _LEGACY_SCOPES[normalize_price_role(role)]
