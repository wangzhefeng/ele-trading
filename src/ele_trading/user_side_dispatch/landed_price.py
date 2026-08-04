"""用户侧落地电价合成。

政策背景：

- 1656 号文（2026-03-01 起）：直接参与市场交易的经营主体不再执行政府
  规定的分时电价水平和时段，峰谷价格由市场交易形成；
- 1077 号文（2026-08-01 起）：第四监管周期输配电价生效，两部制把输配
  电费拆为按最大需量/变压器容量计收的容量部分和按电量计收的电量部分。

本模块把调度算法消费的 ``buy_price`` 从"外生给定序列"变为可审计的合成结果：

- ``catalogue`` 模式：落地价(t) = 目录电价(t)。目录电价是政府定价的销售
  电价，已包含全部构成，直接透传；
- ``market`` 模式：落地价(t) = 中长期价(t) × 覆盖率 + 现货价(t) × (1 - 覆盖率)
  + 输配电价电量部分 + 政府性基金及附加。

两部制容量部分（需量电费）不参与逐时段合成，由 ``TariffVersion.demand_charge_rate``
提供给调度输入的 ``demand_charge_rate`` 字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from pathlib import Path
from typing import Any, Sequence

from ..utils.io import read_yaml


class PriceMode(str, Enum):
    """落地电价的定价模式。"""

    CATALOGUE = "catalogue"
    MARKET = "market"


def _require_finite_non_negative(value: float, field_name: str) -> float:
    numeric = float(value)
    if not isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{field_name} must be a non-negative finite number")
    return numeric


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip()[:10])
    raise ValueError(f"cannot interpret {value!r} as a date")


# ---------------------------------------------------------------------------
# 版本化费率表（1077 号文类监管周期切换的载体）
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TariffVersion:
    """某一生效日起适用的输配电价与政府性基金费率。"""

    effective_from: date
    # 输配电价电量部分（元/kWh）
    energy_rate: float
    # 两部制容量部分，按最大需量计收（元/kW·月）
    demand_charge_rate: float
    # 政府性基金及附加（元/kWh）
    surcharge_rate: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "effective_from", _as_date(self.effective_from))
        for field_name in ("energy_rate", "demand_charge_rate", "surcharge_rate"):
            object.__setattr__(
                self,
                field_name,
                _require_finite_non_negative(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class TariffSchedule:
    """按生效日排序的费率版本序列；resolve 取 as_of 当日生效的最新版本。"""

    versions: tuple[TariffVersion, ...]

    def __post_init__(self) -> None:
        versions = tuple(self.versions)
        if not versions:
            raise ValueError("versions must not be empty")
        if not all(isinstance(item, TariffVersion) for item in versions):
            raise ValueError("versions must contain TariffVersion objects")
        effective_dates = [item.effective_from for item in versions]
        if len(set(effective_dates)) != len(effective_dates):
            raise ValueError("effective_from dates must be unique")
        object.__setattr__(
            self, "versions", tuple(sorted(versions, key=lambda item: item.effective_from))
        )

    def resolve(self, as_of: date | datetime | str) -> TariffVersion:
        target = _as_date(as_of)
        applicable = [
            item for item in self.versions if item.effective_from <= target
        ]
        if not applicable:
            raise ValueError(
                f"no tariff version effective on {target.isoformat()}; "
                f"earliest version starts {self.versions[0].effective_from.isoformat()}"
            )
        return applicable[-1]


def load_tariff_schedule(path: str | Path) -> TariffSchedule:
    """从 YAML 加载版本化费率表。

    YAML 结构::

        versions:
          - effective_from: "2026-08-01"
            energy_rate: 0.1467
            demand_charge_rate: 40.0
            surcharge_rate: 0.029
    """
    config = read_yaml(path)
    raw_versions = config.get("versions")
    if not isinstance(raw_versions, list) or not raw_versions:
        raise ValueError("tariff config must contain a non-empty 'versions' list")
    versions = tuple(
        TariffVersion(
            effective_from=_as_date(item["effective_from"]),
            energy_rate=float(item["energy_rate"]),
            demand_charge_rate=float(item["demand_charge_rate"]),
            surcharge_rate=float(item["surcharge_rate"]),
        )
        for item in raw_versions
    )
    return TariffSchedule(versions=versions)


# ---------------------------------------------------------------------------
# 落地价合成结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LandedPrice:
    """调度输入可直接消费的落地电价序列。"""

    buy_price: list[float]
    price_type: list[str]
    mode: PriceMode
    # 首个时间戳适用的两部制容量费率（元/kW·月），供 dispatch input 的
    # demand_charge_rate 字段使用；需量电费按月结算，不按逐时段切换版本。
    demand_charge_rate: float


# ---------------------------------------------------------------------------
# 落地价合成
# ---------------------------------------------------------------------------
def build_landed_price(
    timestamps: Sequence[Any],
    mode: PriceMode | str,
    tariff_schedule: TariffSchedule,
    *,
    catalogue_price: Sequence[float] | None = None,
    catalogue_price_type: Sequence[str] | None = None,
    mid_long_price: float | Sequence[float] | None = None,
    spot_price: Sequence[float] | None = None,
    mid_long_ratio: float = 1.0,
) -> LandedPrice:
    """按定价模式合成逐时段落地电价。

    catalogue 模式：``catalogue_price`` / ``catalogue_price_type`` 必填，
    目录电价已是完整销售电价，直接透传，不再叠加输配电价和基金附加。

    market 模式：``spot_price`` 必填；``mid_long_price`` 允许标量（按月
    签约的常用形态）或等长序列；交易电价 = 中长期价 × mid_long_ratio +
    现货价 × (1 - mid_long_ratio)，再叠加版本化输配电量电价和基金附加。
    中长期覆盖率简化为恒定比例的启发式，不建模逐时段偏差考核。

    market 模式的 ``price_type`` 由交易电价三分位推导（1656 号文后无政府
    规定时段）；price_type 仅供算法侧充放时段掩码等启发式用途，不参与
    成本计算。
    """
    mode = PriceMode(mode)
    n = len(timestamps)
    if n == 0:
        raise ValueError("timestamps must not be empty")
    if not 0.0 <= mid_long_ratio <= 1.0:
        raise ValueError("mid_long_ratio must be within [0, 1]")

    first_tariff = tariff_schedule.resolve(timestamps[0])

    if mode is PriceMode.CATALOGUE:
        if catalogue_price is None or catalogue_price_type is None:
            raise ValueError(
                "catalogue mode requires catalogue_price and catalogue_price_type"
            )
        buy_price = _as_float_list(catalogue_price, n, "catalogue_price")
        price_type = [str(item) for item in catalogue_price_type]
        if len(price_type) != n:
            raise ValueError("catalogue_price_type length must match timestamps")
        return LandedPrice(
            buy_price=buy_price,
            price_type=price_type,
            mode=mode,
            demand_charge_rate=first_tariff.demand_charge_rate,
        )

    # market 模式
    if spot_price is None:
        raise ValueError("market mode requires spot_price")
    spot = _as_float_list(spot_price, n, "spot_price")
    mid_long = _broadcast_mid_long(mid_long_price, n)

    buy_price = []
    energy_prices = []
    for index, timestamp in enumerate(timestamps):
        tariff = tariff_schedule.resolve(timestamp)
        energy = mid_long_ratio * mid_long[index] + (1.0 - mid_long_ratio) * spot[index]
        energy_prices.append(energy)
        buy_price.append(energy + tariff.energy_rate + tariff.surcharge_rate)

    return LandedPrice(
        buy_price=buy_price,
        price_type=_derive_price_type(energy_prices),
        mode=mode,
        demand_charge_rate=first_tariff.demand_charge_rate,
    )


def _as_float_list(values: Sequence[float], n: int, field_name: str) -> list[float]:
    result = [float(item) for item in values]
    if len(result) != n:
        raise ValueError(f"{field_name} length must match timestamps")
    if not all(isfinite(item) for item in result):
        raise ValueError(f"{field_name} must contain only finite numbers")
    return result


def _broadcast_mid_long(
    value: float | Sequence[float] | None, n: int
) -> list[float]:
    if value is None:
        return [0.0] * n
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not isfinite(numeric):
            raise ValueError("mid_long_price must be finite")
        return [numeric] * n
    return _as_float_list(value, n, "mid_long_price")


def _derive_price_type(energy_prices: list[float]) -> list[str]:
    """按交易电价秩次打 valley/flat/peak（1656 号文后的启发式替代）。

    价格最低/最高的约三分之一时段标 valley/peak，其余 flat；全部相等时
    无峰谷结构，统一标 flat。打标仅供算法侧充放时段掩码使用。
    """
    n = len(energy_prices)
    if min(energy_prices) == max(energy_prices):
        return ["flat"] * n
    third = n // 3
    labels = ["flat"] * n
    ranked = sorted(range(n), key=lambda index: (energy_prices[index], index))
    for index in ranked[:third]:
        labels[index] = "valley"
    for index in ranked[n - third:]:
        labels[index] = "peak"
    return labels
