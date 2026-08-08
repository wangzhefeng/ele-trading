"""v5 V5-4：SCED / SCUC / 后定价 / N-1 标准算例。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.market_simulation.contingency import run_n1_screening
from ele_trading.market_simulation.grid.contracts import (
    Branch,
    Bus,
    Generator,
    GridSnapshot,
)
from ele_trading.market_simulation.sced import solve_sced
from ele_trading.market_simulation.scuc import (
    compute_uplift,
    price_from_commitment,
    solve_scuc,
)

AS_OF = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")


def _grid(branches, generators, reserve: float = 0.0) -> GridSnapshot:
    return GridSnapshot(
        as_of=AS_OF,
        version="test-grid-v1",
        buses=(Bus("b1"), Bus("b2")),
        branches=tuple(branches),
        generators=tuple(generators),
        reserve_requirement_mw=reserve,
    )


def _two_bus_line(limit: float = 1e6) -> Branch:
    return Branch(
        branch_id="line-1",
        from_bus="b1",
        to_bus="b2",
        susceptance=10.0,
        thermal_limit_mw=limit,
    )


def test_uncongested_dispatch_matches_economic_counterexample():
    """复算此前构造的反例：50/10、成本 8000、平衡对偶价 300。"""
    grid = _grid(
        [_two_bus_line()],
        [
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=50.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
            Generator(
                generator_id="g2",
                bus_id="b2",
                p_min_mw=0.0,
                p_max_mw=20.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=300.0,
            ),
        ],
    )
    result = solve_sced(grid, {"b1": 0.0, "b2": 60.0})

    assert result.dispatch_mw["g1"] == pytest.approx(50.0, abs=1e-4)
    assert result.dispatch_mw["g2"] == pytest.approx(10.0, abs=1e-4)
    assert result.energy_cost == pytest.approx(8000.0, abs=1e-2)
    # 无阻塞：全网同一价格 = 边际机组 g2 的边际成本（不是最低已开机成本 100）
    assert result.lmp["b1"] == pytest.approx(300.0, abs=1e-4)
    assert result.lmp["b2"] == pytest.approx(300.0, abs=1e-4)
    assert result.active_branch_ids == ()
    assert sum(result.load_shed_mw.values()) == pytest.approx(0.0)


def test_congestion_separates_lmp_and_marks_active_branch():
    grid = _grid(
        [_two_bus_line(limit=10.0)],
        [
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=60.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
            Generator(
                generator_id="g2",
                bus_id="b2",
                p_min_mw=0.0,
                p_max_mw=60.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=300.0,
            ),
        ],
    )
    result = solve_sced(grid, {"b1": 0.0, "b2": 60.0})

    assert result.branch_flows_mw["line-1"] == pytest.approx(10.0, abs=1e-4)
    assert result.dispatch_mw["g1"] == pytest.approx(10.0, abs=1e-4)
    assert result.dispatch_mw["g2"] == pytest.approx(50.0, abs=1e-4)
    assert result.active_branch_ids == ("line-1",)
    assert result.lmp["b1"] == pytest.approx(100.0, abs=1e-4)
    assert result.lmp["b2"] == pytest.approx(300.0, abs=1e-4)
    # 阻塞租金 = (LMP_to - LMP_from) × flow
    congestion_rent = (
        result.lmp["b2"] - result.lmp["b1"]
    ) * result.branch_flows_mw["line-1"]
    assert congestion_rent == pytest.approx(2000.0, abs=1e-2)


def test_load_shed_is_priced_at_voll():
    grid = _grid(
        [_two_bus_line()],
        [
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=40.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ],
    )
    result = solve_sced(grid, {"b1": 0.0, "b2": 100.0}, voll=5000.0)

    assert result.load_shed_mw["b2"] == pytest.approx(60.0, abs=1e-4)
    assert result.lmp["b1"] == pytest.approx(5000.0, abs=1e-2)
    assert result.lmp["b2"] == pytest.approx(5000.0, abs=1e-2)


def test_scuc_min_up_binds_and_post_pricing_computes_uplift():
    grid = _grid(
        [_two_bus_line()],
        [
            Generator(
                generator_id="coal",
                bus_id="b1",
                p_min_mw=10.0,
                p_max_mw=50.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=300.0,
                minimum_up_periods=2,
            ),
            Generator(
                generator_id="peaker",
                bus_id="b2",
                p_min_mw=0.0,
                p_max_mw=30.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ],
    )
    # 中间时段负荷 55 > peaker 上限，coal 必须开；两侧小负荷本来用 peaker 更便宜
    load = {"b1": [0.0, 0.0, 0.0], "b2": [12.0, 55.0, 12.0]}

    # 无最小开机约束时 coal 只在中间时段开机（构造一个 min_up=0 的对照网格）
    grid_free = _grid(
        [_two_bus_line()],
        [
            Generator(
                generator_id="coal",
                bus_id="b1",
                p_min_mw=10.0,
                p_max_mw=50.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=300.0,
            ),
            grid.generators[1],
        ],
    )
    relaxed = solve_scuc(grid_free, load)
    assert relaxed.commitment["coal"] == (False, True, False)

    scuc = solve_scuc(grid, load)
    # min_up=2：coal 必须连续开两个时段且覆盖中间时段
    assert scuc.commitment["coal"] in ((True, True, False), (False, True, True))
    assert sum(scuc.commitment["coal"]) == 2
    assert scuc.dispatch_mw["coal"][1] + scuc.dispatch_mw["peaker"][1] == pytest.approx(
        55.0, abs=1e-4
    )

    # 后定价：显式固定 (on, on, off)，时段 0 coal 压 p_min，peaker 边际 → LMP 100
    commitment = {
        "coal": (True, True, False),
        "peaker": (True, True, True),
    }
    priced = price_from_commitment(grid, load, commitment)
    assert len(priced) == 3
    assert priced[0].dispatch_mw["coal"] == pytest.approx(10.0, abs=1e-4)
    assert priced[0].lmp["b2"] == pytest.approx(100.0, abs=1e-4)

    uplift = compute_uplift(grid, priced)
    # coal 时段 0 能量收入 100×10=1000 < 报价成本 300×10=3000 → 缺额 2000
    assert uplift.per_generator_shortfall["coal"] == pytest.approx(2000.0, abs=1e-2)
    # peaker 始终边际或更便宜，无缺额
    assert uplift.per_generator_shortfall["peaker"] == pytest.approx(0.0, abs=1e-2)


def test_n1_screening_reports_each_in_service_branch():
    grid = GridSnapshot(
        as_of=AS_OF,
        version="triangle-v1",
        buses=(Bus("b1"), Bus("b2"), Bus("b3")),
        branches=(
            Branch(
                branch_id="l12",
                from_bus="b1",
                to_bus="b2",
                susceptance=10.0,
                thermal_limit_mw=100.0,
            ),
            Branch(
                branch_id="l23",
                from_bus="b2",
                to_bus="b3",
                susceptance=10.0,
                thermal_limit_mw=100.0,
            ),
            Branch(
                branch_id="l13",
                from_bus="b1",
                to_bus="b3",
                susceptance=10.0,
                thermal_limit_mw=100.0,
            ),
        ),
        generators=(
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=100.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ),
    )
    report = run_n1_screening(grid, {"b1": 0.0, "b2": 30.0, "b3": 40.0})

    assert len(report.outcomes) == 3
    assert report.secure
    assert {outcome.contingency_id for outcome in report.outcomes} == {
        "l12",
        "l23",
        "l13",
    }
    assert report.worst_contingency_id is not None


def test_sced_rejects_invalid_inputs():
    grid = _grid(
        [_two_bus_line()],
        [
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=10.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ],
    )
    with pytest.raises(ValueError, match="voll"):
        solve_sced(grid, {"b2": 5.0}, voll=0.0)
    with pytest.raises(ValueError, match="load_mw"):
        solve_sced(grid, {"b2": -1.0})
    with pytest.raises(ValueError, match="unknown generators"):
        solve_sced(grid, {"b2": 5.0}, fixed_commitment={"gx": True})


def test_sced_and_scuc_propagate_solver_failure_instead_of_zero_results():
    """求解器进程失败不能被抽取为全零出清结果。"""
    grid = _grid(
        [_two_bus_line()],
        [
            Generator(
                generator_id="g1",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=10.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ],
    )

    with pytest.raises(RuntimeError, match="sced solve failed: error"):
        solve_sced(grid, {"b2": 5.0}, solver=object())
    with pytest.raises(RuntimeError, match="scuc solve failed: error"):
        solve_scuc(grid, {"b1": [0.0], "b2": [5.0]}, solver=object())
