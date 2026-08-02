"""报量报价契约（v5 §10.1）。

``OfferStack`` 为单机组单时段的分段报价：段出力为正、价格单调不减、
上下界由规则快照给定。报价是市场输入，不是成本真值。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value.strip()


@dataclass(frozen=True, slots=True)
class BidSegment:
    """单段报价：``mw`` 段容量、``price`` 该段边际报价。"""

    mw: float
    price: float

    def __post_init__(self) -> None:
        mw = float(self.mw)
        price = float(self.price)
        if not np.isfinite(mw) or mw <= 0.0:
            raise ValueError("segment mw must be finite and positive")
        if not np.isfinite(price):
            raise ValueError("segment price must be finite")
        object.__setattr__(self, "mw", mw)
        object.__setattr__(self, "price", price)


@dataclass(frozen=True, slots=True)
class OfferStack:
    """单机组分段报价栈（价格单调不减，容量为各段之和）。"""

    generator_id: str
    segments: tuple[BidSegment, ...]
    rule_version: str

    def __post_init__(self) -> None:
        _non_empty(self.generator_id, "generator_id")
        _non_empty(self.rule_version, "rule_version")
        segments = tuple(self.segments)
        if not segments:
            raise ValueError("segments must not be empty")
        if not all(isinstance(item, BidSegment) for item in segments):
            raise ValueError("segments must contain BidSegment objects")
        prices = [item.price for item in segments]
        if any(later < earlier - 1e-12 for earlier, later in zip(prices, prices[1:])):
            raise ValueError("segment prices must be non-decreasing")
        object.__setattr__(self, "segments", segments)

    @property
    def total_mw(self) -> float:
        return float(sum(item.mw for item in self.segments))

    @property
    def marginal_price(self) -> float:
        """最后一段价格（整栈边际报价）。"""
        return float(self.segments[-1].price)

    @property
    def average_price(self) -> float:
        total = self.total_mw
        return float(
            sum(item.mw * item.price for item in self.segments) / total
        )

    def validate_against(
        self,
        *,
        capacity_mw: float,
        price_floor: float,
        price_cap: float,
    ) -> None:
        """对照规则边界校验：总容量不得超物理上限，价格在报价区间内。"""
        if not np.isfinite(capacity_mw) or capacity_mw <= 0.0:
            raise ValueError("capacity_mw must be finite and positive")
        if not np.isfinite(price_floor) or not np.isfinite(price_cap):
            raise ValueError("price bounds must be finite")
        if price_floor > price_cap:
            raise ValueError("price_floor cannot exceed price_cap")
        if self.total_mw > capacity_mw + 1e-9:
            raise ValueError("offer stack exceeds physical capacity")
        for segment in self.segments:
            if not price_floor - 1e-12 <= segment.price <= price_cap + 1e-12:
                raise ValueError(
                    f"segment price {segment.price} outside "
                    f"[{price_floor}, {price_cap}]"
                )

    @classmethod
    def flat(
        cls,
        generator_id: str,
        *,
        capacity_mw: float,
        price: float,
        rule_version: str,
    ) -> "OfferStack":
        """单段平价报价的便捷构造。"""
        return cls(
            generator_id=generator_id,
            segments=(BidSegment(mw=capacity_mw, price=price),),
            rule_version=rule_version,
        )
