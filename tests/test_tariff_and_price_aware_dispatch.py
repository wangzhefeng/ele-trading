"""Item 1 tests: 结构化 Tariff + 价格感知调度 + 电价提升优势 KPI.

Task 1 覆盖 Tariff 合同与 settle_monthly 消费 TOU；Task 2/3/4 在本文件追加价格感知调度与 KPI 测试。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.capacity_planning.tariff import DemandChargeConfig, Tariff
from ele_trading.capacity_planning.models.canonical_dispatch import canonical_dispatch
from ele_trading.capacity_planning.models.physics_contract import BESSPhysicsContract
from ele_trading.capacity_planning.settlement import settle_monthly


def _dispatch():
    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    return canonical_dispatch(
        load_kw=np.full(24, 100.0),
        generation_kw={"pv": np.array([0.0] * 6 + [200.0] * 6 + [0.0] * 12)},
        bess=BESSPhysicsContract(
            eta_charge=1.0, eta_discharge=1.0, soc_init_frac=0.0, soc_min_frac=0.0
        ),
        bess_capacity_kwh=300.0,
        timestamps=idx,
        dt_hours=1.0,
    )


def test_flat_tariff_equals_scalar_settlement():
    d = _dispatch()
    idx = d.timestamps
    tariff = Tariff.from_flat(
        idx, grid_buy_price=0.36, green_price=0.32, demand_charge_rate=0.0
    )
    s_tou = settle_monthly(
        d,
        tariff=tariff,
        ppa_price_yuan_per_kwh=0.246,
        baseline_price_yuan_per_kwh=0.36,
    )
    s_flat = settle_monthly(
        d,
        green_price_yuan_per_kwh=0.32,
        ppa_price_yuan_per_kwh=0.246,
        grid_buy_price_yuan_per_kwh=0.36,
        baseline_price_yuan_per_kwh=0.36,
    )
    assert s_tou.annual_summary["energy_charge_yuan"] == pytest.approx(
        s_flat.annual_summary["energy_charge_yuan"]
    )


def test_tou_prices_grid_buy_matches_manual():
    d = _dispatch()
    prices = np.where(np.arange(24) < 12, 0.30, 0.60)  # 上午谷、下午峰
    tariff = Tariff(
        timestamps=d.timestamps,
        grid_buy_price_yuan_per_kwh=prices,
        green_price_yuan_per_kwh=0.32,
        demand_charge=DemandChargeConfig(rate_yuan_per_kw=10.0),
    )
    tariff.validate(len(d.grid_buy_kwh))
    s = settle_monthly(
        d,
        tariff=tariff,
        ppa_price_yuan_per_kwh=0.246,
        baseline_price_yuan_per_kwh=0.45,
    )
    # energy_charge = Σ grid_buy_kwh[t]*price[t] + green_used*green_price（逐时）
    assert s.annual_summary["energy_charge_yuan"] == pytest.approx(
        float((d.grid_buy_kwh * prices).sum())
        + 0.32 * s.annual_summary["green_used_kwh"]
    )


# ---------------------------------------------------------------------------
# Task 2: price_aware_dispatch SELF_CONSUMPTION 模式
# ---------------------------------------------------------------------------


def test_price_aware_self_consumption_conserves_energy_and_prefers_peak_discharge():
    from ele_trading.capacity_planning.models.price_aware_dispatch import (
        DispatchMode,
        price_aware_dispatch,
    )

    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    load = np.full(24, 100.0)
    pv = np.array([0.0] * 6 + [200.0] * 6 + [0.0] * 12)  # 6-12 时有富余可充电
    prices = np.where(np.arange(24) < 18, 0.30, 0.60)  # 18-24 时为峰
    bess = BESSPhysicsContract(
        eta_charge=1.0, eta_discharge=1.0, soc_init_frac=0.0, soc_min_frac=0.0
    )
    r = price_aware_dispatch(
        load_kw=load,
        generation_kw={"pv": pv},
        bess=bess,
        bess_capacity_kwh=300.0,
        price_yuan_per_kwh=prices,
        timestamps=idx,
        dt_hours=1.0,
        mode=DispatchMode.SELF_CONSUMPTION,
    )
    # 守恒与 canonical 同
    np.testing.assert_allclose(
        r.generation_kwh, r.direct_used_kwh + r.charge_kwh + r.curtail_kwh
    )
    np.testing.assert_allclose(
        r.load_kwh, r.direct_used_kwh + r.discharge_kwh + r.grid_buy_kwh
    )
    # grid_charge 在自消纳模式下恒为 0
    assert float(r.grid_charge_kwh.sum()) == 0.0
    # 放电偏向峰时：18-24 时放电量 > 12-18 时放电量
    assert r.discharge_kwh[18:].sum() > r.discharge_kwh[12:18].sum()


# ---------------------------------------------------------------------------
# Task 3: price_aware_dispatch ARBITRAGE 模式（允许电网充电）
# ---------------------------------------------------------------------------


def test_price_aware_arbitrage_charges_from_grid_at_valley():
    from ele_trading.capacity_planning.models.price_aware_dispatch import (
        DispatchMode,
        price_aware_dispatch,
    )

    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    load = np.full(24, 100.0)
    pv = np.zeros(24)  # 无新能源，纯套利
    prices = np.where((np.arange(24) >= 2) & (np.arange(24) < 6), 0.20, 0.70)  # 2-6 谷、其余峰
    bess = BESSPhysicsContract(
        eta_charge=0.95, eta_discharge=0.95, soc_init_frac=0.0, soc_min_frac=0.0, c_rate=1.0
    )
    r = price_aware_dispatch(
        load_kw=load,
        generation_kw={"pv": pv},
        bess=bess,
        bess_capacity_kwh=100.0,
        price_yuan_per_kwh=prices,
        timestamps=idx,
        dt_hours=1.0,
        mode=DispatchMode.ARBITRAGE,
    )
    assert float(r.grid_charge_kwh.sum()) > 0.0  # 确实向网充电
    assert float(r.discharge_kwh.sum()) > 0.0  # 峰时放电
    # 守恒仍成立（grid_buy 仅计负荷侧购网，不含向网充电）
    np.testing.assert_allclose(
        r.load_kwh, r.direct_used_kwh + r.discharge_kwh + r.grid_buy_kwh
    )


# ---------------------------------------------------------------------------
# Task 4: 电价提升优势 KPI
# ---------------------------------------------------------------------------


def test_price_advantage_rewards_peak_discharge():
    # price_advantage = Σ discharge_kwh[t]*(price[t] - 月均价)；放电偏峰时应为正
    from ele_trading.capacity_planning.models.price_aware_dispatch import (
        DispatchMode,
        price_aware_dispatch,
    )

    idx = pd.date_range("2026-01-01", periods=24, freq="h")
    load = np.full(24, 100.0)
    pv = np.array([0.0] * 6 + [200.0] * 6 + [0.0] * 12)
    prices = np.where(np.arange(24) < 18, 0.30, 0.60)  # 18-24 峰
    bess = BESSPhysicsContract(
        eta_charge=1.0, eta_discharge=1.0, soc_init_frac=0.0, soc_min_frac=0.0
    )
    d = price_aware_dispatch(
        load_kw=load,
        generation_kw={"pv": pv},
        bess=bess,
        bess_capacity_kwh=300.0,
        price_yuan_per_kwh=prices,
        timestamps=idx,
        dt_hours=1.0,
        mode=DispatchMode.SELF_CONSUMPTION,
    )
    tariff = Tariff(
        timestamps=idx, grid_buy_price_yuan_per_kwh=prices, green_price_yuan_per_kwh=0.32
    )
    s = settle_monthly(
        d, tariff=tariff, ppa_price_yuan_per_kwh=0.246, baseline_price_yuan_per_kwh=0.45
    )
    mask = idx.to_period("M").astype(str) == s.monthly[0].month
    mean_p = float(prices[mask].mean())
    expected = float((d.discharge_kwh[mask] * (prices[mask] - mean_p)).sum())
    assert s.annual_summary["price_advantage_yuan"] == pytest.approx(expected, rel=1e-6)
    assert s.annual_summary["price_advantage_yuan"] >= -1e-6
