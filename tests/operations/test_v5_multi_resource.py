"""v5 V5-3（§11.1）：多资源联合优化（BESS 群 + DR + 新能源限电）。"""

from __future__ import annotations

import numpy as np
import pytest

from ele_trading.operations.multi_resource import (
    BESSUnit,
    DemandResponseUnit,
    RenewableUnit,
    solve_multi_resource,
)


DT = 1.0


def _bess(name: str, **overrides) -> BESSUnit:
    params = {
        "soc0": 3.0, "soc_min": 1.0, "soc_max": 5.0,
        "p_charge_max": 2.0, "p_discharge_max": 2.0,
        "eta_charge": 1.0, "eta_discharge": 1.0,
        "degradation_cost_per_mwh": 0.0,
        "terminal_soc_min": None,
    }
    params.update(overrides)
    return BESSUnit(name=name, **params)


def test_bess_fleet_arbitrages_and_respects_per_unit_limits():
    # 高价窗口足够长：两台都必须满充+动用初始 SOC 才能覆盖放电功率
    load = np.array([0.0, 0.0, 6.0, 6.0, 6.0, 6.0])
    price = np.array([50.0, 50.0, 500.0, 500.0, 500.0, 500.0])
    result = solve_multi_resource(
        load_mwh=load,
        price=price,
        bess_units=(
            _bess("bess-a"),
            _bess("bess-b", p_charge_max=1.0, p_discharge_max=1.0),
        ),
        dt=DT,
    )

    schedule = result.resource_schedules
    # 两台都在低价窗口（step 0-1 等价退化）充满各自容量、b 受 1MW 上限约束
    assert sum(schedule["bess-a"]["p_charge"][:2]) == pytest.approx(2.0, abs=1e-4)
    assert sum(schedule["bess-b"]["p_charge"][:2]) == pytest.approx(2.0, abs=1e-4)
    assert max(schedule["bess-b"]["p_charge"]) <= 1.0 + 1e-4
    assert schedule["bess-a"]["p_discharge"][2] == pytest.approx(2.0, abs=1e-4)
    assert schedule["bess-b"]["p_discharge"][2] == pytest.approx(1.0, abs=1e-4)
    # 低价窗口总购电 = 两台充电量；高价时段放电顶掉部分购电但禁止上网
    assert sum(result.grid_import_mwh[:2]) == pytest.approx(4.0, abs=1e-4)
    assert result.grid_import_mwh[2] == pytest.approx(3.0, abs=1e-4)
    # 相对无储能基线显著降本，但无上网时成本不可能为负
    baseline_cost = float(np.sum(load * price))
    assert result.expected_cost < baseline_cost * 0.7


def test_dr_shifts_energy_from_high_to_low_price_with_neutral_window():
    load = np.full(4, 10.0)
    price = np.array([50.0, 50.0, 500.0, 500.0])
    dr = DemandResponseUnit(
        name="dr-1",
        max_shift_down_mw=2.0,
        max_shift_up_mw=2.0,
        cost_per_mwh=1.0,
        window=(0, 4),
    )
    result = solve_multi_resource(
        load_mwh=load,
        price=price,
        dr_units=(dr,),
        dt=DT,
    )

    down = result.dr_schedules["dr-1"]["shift_down_mw"]
    up = result.dr_schedules["dr-1"]["shift_up_mw"]
    # 高价时段削峰、低价时段回补，窗口内能量中性
    assert down[2] == pytest.approx(2.0, abs=1e-4)
    assert down[3] == pytest.approx(2.0, abs=1e-4)
    assert sum(up) == pytest.approx(sum(down), abs=1e-4)
    assert up[0] + up[1] == pytest.approx(4.0, abs=1e-4)


def test_renewable_curtails_when_price_negative():
    load = np.full(2, 4.0)
    price = np.array([-100.0, 300.0])
    pv = RenewableUnit(
        name="pv-1",
        available_mw=np.array([4.0, 4.0]),
        curtailment_cost_per_mwh=0.0,
    )
    result = solve_multi_resource(
        load_mwh=load,
        price=price,
        renewable_units=(pv,),
        dt=DT,
    )

    used = result.renewable_schedules["pv-1"]["used_mw"]
    # 负价时段全部限电（禁止上网），正价时段全部自用
    assert used[0] == pytest.approx(0.0, abs=1e-4)
    assert used[1] == pytest.approx(4.0, abs=1e-4)


def test_scenario_cvar_reduces_tail_loss_vs_expectation_only():
    load = np.zeros(2)
    price = np.array([100.0, 100.0])
    scenarios = {
        "base": np.array([100.0, 100.0]),
        "spike": np.array([100.0, 2000.0]),
    }
    probabilities = {"base": 0.9, "spike": 0.1}
    bess = _bess(
        "bess-a",
        soc0=3.0,
        terminal_soc_min=3.0,
        degradation_cost_per_mwh=5.0,
    )

    expectation_only = solve_multi_resource(
        load_mwh=load,
        price=price,
        bess_units=(bess,),
        dt=DT,
        scenario_prices=scenarios,
        scenario_probabilities=probabilities,
        cvar_weight=0.0,
    )
    risk_averse = solve_multi_resource(
        load_mwh=load,
        price=price,
        bess_units=(bess,),
        dt=DT,
        scenario_prices=scenarios,
        scenario_probabilities=probabilities,
        cvar_weight=5.0,
        cvar_alpha=0.9,
    )

    # 风险厌恶解在 spike 场景的成本不得高于纯期望解
    assert (
        risk_averse.scenario_costs["spike"]
        <= expectation_only.scenario_costs["spike"] + 1e-4
    )
    assert risk_averse.cvar is not None


def test_validation_errors():
    with pytest.raises(ValueError, match="aligned"):
        solve_multi_resource(
            load_mwh=np.zeros(3),
            price=np.zeros(2),
            dt=DT,
        )
    with pytest.raises(ValueError, match="available_mw"):
        solve_multi_resource(
            load_mwh=np.zeros(2),
            price=np.zeros(2),
            renewable_units=(
                RenewableUnit(
                    name="pv-x",
                    available_mw=np.array([1.0]),
                    curtailment_cost_per_mwh=0.0,
                ),
            ),
            dt=DT,
        )
    with pytest.raises(ValueError, match="probability"):
        solve_multi_resource(
            load_mwh=np.zeros(2),
            price=np.zeros(2),
            scenario_prices={"a": np.zeros(2)},
            scenario_probabilities={"a": 0.5},
            dt=DT,
        )
    with pytest.raises(ValueError, match="duplicate"):
        solve_multi_resource(
            load_mwh=np.zeros(2),
            price=np.zeros(2),
            bess_units=(_bess("dup"), _bess("dup")),
            dt=DT,
        )
