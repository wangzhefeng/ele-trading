"""V5 多资源日内重优化的资源级契约。"""

from __future__ import annotations

import numpy as np

from ele_trading.operations.multi_resource import BESSUnit, solve_multi_resource
from ele_trading.operations.multi_resource_intraday import solve_multi_resource_intraday


def _bess(name: str, soc0: float) -> BESSUnit:
    return BESSUnit(
        name=name,
        soc0=soc0,
        soc_min=1.0,
        soc_max=5.0,
        p_charge_max=2.0,
        p_discharge_max=2.0,
        eta_charge=1.0,
        eta_discharge=1.0,
    )


def test_multi_resource_intraday_uses_each_resource_actual_soc_and_freezes_prefix():
    units = (_bess("bess-a", 4.0), _bess("bess-b", 4.0))
    previous = solve_multi_resource(
        load_mwh=np.array([4.0, 4.0, 4.0, 4.0]),
        price=np.array([10.0, 10.0, 100.0, 100.0]),
        bess_units=units,
        dt=1.0,
    )

    result = solve_multi_resource_intraday(
        load_mwh=np.array([4.0, 4.0]),
        price=np.array([100.0, 100.0]),
        bess_units=units,
        previous_result=previous,
        executed_count=2,
        actual_soc_mwh={"bess-a": 1.0, "bess-b": 3.0},
        dt=1.0,
    )

    assert result.fallback_used is False
    assert result.executed_prefix["bess-a"]["p_discharge"] == previous.resource_schedules[
        "bess-a"
    ]["p_discharge"][:2]
    assert result.initial_soc_mwh == {"bess-a": 1.0, "bess-b": 3.0}


def test_multi_resource_intraday_uses_soc_safe_fallback_after_solver_failure():
    units = (_bess("bess-a", 4.0),)
    previous = solve_multi_resource(
        load_mwh=np.array([4.0, 4.0, 4.0, 4.0]),
        price=np.array([10.0, 10.0, 100.0, 100.0]),
        bess_units=units,
        dt=1.0,
    )

    result = solve_multi_resource_intraday(
        load_mwh=np.array([4.0, 4.0]),
        price=np.array([100.0, 100.0]),
        bess_units=units,
        previous_result=previous,
        executed_count=2,
        actual_soc_mwh={"bess-a": 1.0},
        dt=1.0,
        solver=object(),
    )

    assert result.fallback_used is True
    assert result.fallback_reason == "multi-resource solve failed: error"
    assert min(result.resource_schedules["bess-a"]["soc"]) >= 1.0
