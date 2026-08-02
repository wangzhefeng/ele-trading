"""v5 V5-5：报价契约、logit 行为与 ABM 出清。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.market_simulation.behavior import (
    AgentBasedMarket,
    LogitMarkupPolicy,
    empirical_markup_distribution,
)
from ele_trading.market_simulation.bidding import BidSegment, OfferStack
from ele_trading.market_simulation.grid.contracts import (
    Branch,
    Bus,
    Generator,
    GridSnapshot,
)

AS_OF = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")


def test_offer_stack_enforces_monotone_prices_and_bounds():
    with pytest.raises(ValueError, match="non-decreasing"):
        OfferStack(
            generator_id="g1",
            segments=(
                BidSegment(mw=10.0, price=300.0),
                BidSegment(mw=10.0, price=200.0),
            ),
            rule_version="rule-v1",
        )

    stack = OfferStack(
        generator_id="g1",
        segments=(
            BidSegment(mw=10.0, price=100.0),
            BidSegment(mw=10.0, price=200.0),
        ),
        rule_version="rule-v1",
    )
    assert stack.total_mw == 20.0
    assert stack.marginal_price == 200.0
    assert stack.average_price == 150.0
    stack.validate_against(capacity_mw=20.0, price_floor=0.0, price_cap=500.0)
    with pytest.raises(ValueError, match="capacity"):
        stack.validate_against(capacity_mw=15.0, price_floor=0.0, price_cap=500.0)
    with pytest.raises(ValueError, match="outside"):
        stack.validate_against(capacity_mw=20.0, price_floor=150.0, price_cap=500.0)


def test_bid_segment_validation():
    with pytest.raises(ValueError, match="positive"):
        BidSegment(mw=0.0, price=100.0)
    with pytest.raises(ValueError, match="finite"):
        BidSegment(mw=1.0, price=float("nan"))


def test_logit_policy_extremes_and_alignment():
    policy = LogitMarkupPolicy([1.0, 1.5, 2.0], temperature=0.0)
    probabilities = policy.probabilities([0.0, 5.0, 1.0])
    assert probabilities.tolist() == [0.0, 1.0, 0.0]

    hot = LogitMarkupPolicy([1.0, 1.5, 2.0], temperature=1e6)
    probabilities = hot.probabilities([0.0, 5.0, 1.0])
    assert np.allclose(probabilities, 1.0 / 3.0, atol=1e-3)

    with pytest.raises(ValueError, match="align"):
        policy.probabilities([1.0])
    with pytest.raises(ValueError, match="positive"):
        LogitMarkupPolicy([0.0, 1.0], temperature=1.0)


def test_empirical_markup_distribution_recovers_frequencies():
    grid = [1.0, 1.2, 1.5]
    samples = [1.0] * 6 + [1.19, 1.21] + [1.5] * 2
    distribution = empirical_markup_distribution(samples, grid)
    assert distribution == pytest.approx([0.6, 0.2, 0.2])


def _monopoly_grid() -> GridSnapshot:
    return GridSnapshot(
        as_of=AS_OF,
        version="abm-monopoly",
        buses=(Bus("b1"),),
        branches=(),
        generators=(
            Generator(
                generator_id="monopolist",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=100.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ),
    )


def test_abm_monopolist_learns_to_raise_markup_toward_cap():
    market = AgentBasedMarket(
        _monopoly_grid(),
        markup_grid=[1.0, 1.5, 2.0, 3.0],
        temperature=50.0,
        learning_rate=0.6,
        price_cap=350.0,
    )
    result = market.run({"b1": 50.0}, periods=40, seed=11)

    history = result.markup_history["monopolist"]
    # 垄断者逐步学会抬高报价；末 10 轮平均加成显著高于首轮随机水平，
    # 且报价被 price_cap 截断在 350（100 × 3.0 → 300 < 350）
    assert np.mean(history[-10:]) > np.mean(history[:10])
    final_offers = result.rounds[-1].offers["monopolist"]
    assert final_offers.marginal_price <= 350.0
    assert result.rounds[-1].clearing.lmp["b1"] > 100.0
    assert result.rounds[-1].profits["monopolist"] > 0.0


def test_abm_competitive_duopoly_keeps_price_near_cost():
    grid = GridSnapshot(
        as_of=AS_OF,
        version="abm-duopoly",
        buses=(Bus("b1"),),
        branches=(),
        generators=(
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=80.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
            Generator(
                generator_id="g2",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=80.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ),
    )
    market = AgentBasedMarket(
        grid,
        markup_grid=[1.0, 1.2, 1.5, 2.0],
        temperature=100.0,
        learning_rate=0.5,
    )
    # 负荷 60 < 单机容量 80：任一台都可独供，抬价者丢量
    result = market.run({"b1": 60.0}, periods=60, seed=7)

    tail_lmp = result.lmp_series["b1"][-20:]
    assert np.median(tail_lmp) <= 120.0 + 1e-6


def test_abm_reproducible_with_same_seed():
    market = AgentBasedMarket(
        _monopoly_grid(),
        markup_grid=[1.0, 1.5, 2.0],
        temperature=100.0,
    )
    first = market.run({"b1": 50.0}, periods=10, seed=3)
    second = market.run({"b1": 50.0}, periods=10, seed=3)

    assert first.lmp_series == second.lmp_series
    assert first.markup_history == second.markup_history


def test_abm_uses_network_congestion_in_clearing():
    grid = GridSnapshot(
        as_of=AS_OF,
        version="abm-congested",
        buses=(Bus("b1"), Bus("b2")),
        branches=(
            Branch(
                branch_id="line",
                from_bus="b1",
                to_bus="b2",
                susceptance=10.0,
                thermal_limit_mw=10.0,
            ),
        ),
        generators=(
            Generator(
                generator_id="cheap",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=60.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
            Generator(
                generator_id="local",
                bus_id="b2",
                p_min_mw=0.0,
                p_max_mw=60.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=300.0,
            ),
        ),
    )
    market = AgentBasedMarket(
        grid,
        markup_grid=[1.0],
        temperature=0.0,
        strategic_generator_ids=(),
    )
    result = market.run({"b1": 0.0, "b2": 60.0}, periods=1, seed=1)

    clearing = result.rounds[0].clearing
    assert clearing.branch_flows_mw["line"] == pytest.approx(10.0, abs=1e-4)
    assert clearing.lmp["b2"] == pytest.approx(300.0, abs=1e-4)
    assert result.rounds[0].profits["local"] == pytest.approx(0.0, abs=1e-4)
