"""V5 Award 对日前物理履约计划的硬约束。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ele_trading.domain.contracts import AwardedCommitment
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.operations.day_ahead_coupled import solve_day_ahead_operational

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
BESS = {
    "p_bcmax": 2.0,
    "p_bdmax": 2.0,
    "p_bceff": 0.95,
    "p_bdeff": 0.95,
    "socmin": 1.0,
    "socmax": 5.0,
    "socini": 3.0,
    "cap": 4.0,
}


def _commitment() -> AwardedCommitment:
    index = pd.date_range("2026-07-01 00:15", periods=4, freq="15min", tz="Asia/Shanghai")
    return AwardedCommitment(
        award_id="award-001",
        bid_id="bid-001",
        external_award_reference=None,
        market="test-market",
        product="energy",
        direction="sell",
        required_energy_mwh=pd.Series([0.25, 0.25, 0.0, 0.0], index=index),
        source_version="receipt-v1",
    )


def test_awarded_sell_energy_creates_delivery_floor_in_day_ahead_plan() -> None:
    """低价下本不放电的 BESS 仍须在成交时段交付已售能量。"""
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.dr.dr_enabled = False
    load = np.full(4, 0.75)
    price = np.full(4, 10.0)

    plan = solve_day_ahead_operational(
        load,
        price,
        BESS,
        config,
        awarded_commitment=_commitment(),
    )

    delivered = plan.resource_schedule["p_discharge"].iloc[:2].sum() * config.market.dt
    assert delivered >= 0.5 - 1e-9
