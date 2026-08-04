"""用户侧落地电价合成测试（landed_price）。"""

from datetime import date

import pandas as pd
import pytest

from ele_trading.user_side_dispatch.landed_price import (
    LandedPrice,
    PriceMode,
    TariffSchedule,
    TariffVersion,
    build_landed_price,
    load_tariff_schedule,
)


def _schedule() -> TariffSchedule:
    return TariffSchedule(
        versions=(
            TariffVersion(
                effective_from=date(2026, 8, 1),
                energy_rate=0.15,
                demand_charge_rate=40.0,
                surcharge_rate=0.03,
            ),
            TariffVersion(
                effective_from=date(2026, 1, 1),
                energy_rate=0.10,
                demand_charge_rate=35.0,
                surcharge_rate=0.02,
            ),
        )
    )


def _timestamps(start: str, periods: int = 4) -> list[pd.Timestamp]:
    return list(pd.date_range(start=start, periods=periods, freq="h"))


# ---------------------------------------------------------------------------
# TariffSchedule 版本解析
# ---------------------------------------------------------------------------
def test_tariff_schedule_sorts_versions_and_resolves_latest_effective():
    schedule = _schedule()
    assert [v.effective_from for v in schedule.versions] == [
        date(2026, 1, 1),
        date(2026, 8, 1),
    ]
    assert schedule.resolve("2026-03-15").demand_charge_rate == 35.0
    assert schedule.resolve("2026-08-01").demand_charge_rate == 40.0
    assert schedule.resolve("2026-12-31").energy_rate == 0.15


def test_tariff_schedule_rejects_empty_and_duplicate_versions():
    with pytest.raises(ValueError, match="must not be empty"):
        TariffSchedule(versions=())
    version = TariffVersion(
        effective_from=date(2026, 1, 1),
        energy_rate=0.1,
        demand_charge_rate=35.0,
        surcharge_rate=0.02,
    )
    with pytest.raises(ValueError, match="unique"):
        TariffSchedule(versions=(version, version))


def test_tariff_schedule_resolve_rejects_date_before_first_version():
    with pytest.raises(ValueError, match="no tariff version effective"):
        _schedule().resolve("2025-12-31")


def test_tariff_version_rejects_negative_rates():
    with pytest.raises(ValueError, match="non-negative"):
        TariffVersion(
            effective_from=date(2026, 1, 1),
            energy_rate=-0.1,
            demand_charge_rate=35.0,
            surcharge_rate=0.02,
        )


# ---------------------------------------------------------------------------
# YAML 加载
# ---------------------------------------------------------------------------
def test_load_tariff_schedule_reads_demo_config():
    schedule = load_tariff_schedule(
        "configs/user_side_dispatch/tariff_schedule_demo.yaml"
    )
    assert len(schedule.versions) == 2
    assert schedule.resolve("2026-08-01").energy_rate == pytest.approx(0.1467)
    assert schedule.resolve("2026-01-15").demand_charge_rate == pytest.approx(38.0)


# ---------------------------------------------------------------------------
# catalogue 模式：目录电价透传
# ---------------------------------------------------------------------------
def test_catalogue_mode_passes_directory_price_through():
    timestamps = _timestamps("2026-03-01 00:00")
    result = build_landed_price(
        timestamps,
        PriceMode.CATALOGUE,
        _schedule(),
        catalogue_price=[0.28, 0.62, 1.05, 0.62],
        catalogue_price_type=["valley", "flat", "peak", "flat"],
    )
    assert isinstance(result, LandedPrice)
    assert result.buy_price == [0.28, 0.62, 1.05, 0.62]
    assert result.price_type == ["valley", "flat", "peak", "flat"]
    # 需量费率取首个时间戳生效版本（2026-03-01 → 第一版）
    assert result.demand_charge_rate == 35.0


def test_catalogue_mode_requires_price_and_price_type():
    timestamps = _timestamps("2026-03-01 00:00")
    with pytest.raises(ValueError, match="catalogue"):
        build_landed_price(timestamps, "catalogue", _schedule())


# ---------------------------------------------------------------------------
# market 模式：中长期 + 现货偏差 + 输配 + 基金
# ---------------------------------------------------------------------------
def test_market_mode_composes_mid_long_spot_and_regulated_rates():
    timestamps = _timestamps("2026-03-01 00:00")
    result = build_landed_price(
        timestamps,
        PriceMode.MARKET,
        _schedule(),
        mid_long_price=0.40,
        spot_price=[0.20, 0.40, 0.80, 0.40],
        mid_long_ratio=0.5,
    )
    # 交易电价 = 0.5×0.40 + 0.5×现货；落地价 = 交易电价 + 0.10 + 0.02
    expected_energy = [0.30, 0.40, 0.60, 0.40]
    assert result.buy_price == pytest.approx([e + 0.12 for e in expected_energy])
    # 三分位：0.30 谷、0.60 峰、0.40 平
    assert result.price_type == ["valley", "flat", "peak", "flat"]


def test_market_mode_applies_tariff_version_per_timestamp():
    # 跨 2026-08-01 费率切换边界：边界前后输配+基金从 0.12 变为 0.18
    timestamps = _timestamps("2026-07-31 22:00", periods=4)
    result = build_landed_price(
        timestamps,
        PriceMode.MARKET,
        _schedule(),
        mid_long_price=0.40,
        spot_price=[0.40, 0.40, 0.40, 0.40],
        mid_long_ratio=1.0,
    )
    assert result.buy_price == pytest.approx([0.52, 0.52, 0.58, 0.58])
    # 需量费率按首个时间戳解析，不逐时段切换
    assert result.demand_charge_rate == 35.0


def test_market_mode_accepts_mid_long_price_series():
    timestamps = _timestamps("2026-03-01 00:00")
    result = build_landed_price(
        timestamps,
        "market",
        _schedule(),
        mid_long_price=[0.38, 0.40, 0.42, 0.44],
        spot_price=[0.20, 0.40, 0.80, 0.40],
        mid_long_ratio=1.0,
    )
    assert result.buy_price == pytest.approx([0.50, 0.52, 0.54, 0.56])


def test_market_mode_requires_spot_price():
    timestamps = _timestamps("2026-03-01 00:00")
    with pytest.raises(ValueError, match="spot_price"):
        build_landed_price(
            timestamps,
            PriceMode.MARKET,
            _schedule(),
            mid_long_price=0.40,
        )


def test_build_landed_price_rejects_invalid_ratio_and_length_mismatch():
    timestamps = _timestamps("2026-03-01 00:00")
    with pytest.raises(ValueError, match="mid_long_ratio"):
        build_landed_price(
            timestamps,
            PriceMode.MARKET,
            _schedule(),
            spot_price=[0.4] * 4,
            mid_long_ratio=1.5,
        )
    with pytest.raises(ValueError, match="length"):
        build_landed_price(
            timestamps,
            PriceMode.MARKET,
            _schedule(),
            spot_price=[0.4] * 3,
        )
