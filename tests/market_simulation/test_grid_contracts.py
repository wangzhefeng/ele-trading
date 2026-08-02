"""v5 V5-0：市场网架快照契约。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.market_simulation.grid.contracts import (
    Branch,
    Bus,
    Generator,
    GridSnapshot,
)


def _valid_grid() -> GridSnapshot:
    return GridSnapshot(
        as_of=pd.Timestamp("2026-08-01 00:00", tz="Asia/Shanghai"),
        version="three-bus-v1",
        buses=(Bus("b1"), Bus("b2"), Bus("b3")),
        branches=(
            Branch("l12", "b1", "b2", susceptance=10.0, thermal_limit_mw=100.0),
            Branch("l23", "b2", "b3", susceptance=8.0, thermal_limit_mw=80.0),
        ),
        generators=(
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=100.0,
                ramp_up_mw=100.0,
                ramp_down_mw=100.0,
                marginal_cost=100.0,
            ),
        ),
        reserve_requirement_mw=5.0,
    )


def test_grid_snapshot_exposes_unique_validated_assets():
    grid = _valid_grid()

    assert grid.bus_ids == frozenset({"b1", "b2", "b3"})
    assert grid.branch_ids == frozenset({"l12", "l23"})
    assert grid.generator_ids == frozenset({"g1"})


def test_grid_snapshot_rejects_branch_referencing_unknown_bus():
    with pytest.raises(ValueError, match="unknown bus"):
        GridSnapshot(
            as_of=pd.Timestamp("2026-08-01", tz="UTC"),
            version="invalid-grid",
            buses=(Bus("b1"),),
            branches=(
                Branch("l12", "b1", "missing", susceptance=1.0, thermal_limit_mw=10.0),
            ),
            generators=(),
        )


def test_generator_rejects_inverted_capacity_range():
    with pytest.raises(ValueError, match="p_min_mw cannot exceed p_max_mw"):
        Generator(
            generator_id="g1",
            bus_id="b1",
            p_min_mw=20.0,
            p_max_mw=10.0,
            ramp_up_mw=10.0,
            ramp_down_mw=10.0,
            marginal_cost=100.0,
        )


def test_grid_snapshot_rejects_duplicate_asset_ids():
    with pytest.raises(ValueError, match="bus IDs must be unique"):
        GridSnapshot(
            as_of=pd.Timestamp("2026-08-01", tz="UTC"),
            version="duplicate-grid",
            buses=(Bus("b1"), Bus("b1")),
            branches=(),
            generators=(),
        )
