"""V5 多资源日内重优化的资源级契约。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ele_trading.operations.multi_resource import (
    BESSUnit,
    DemandResponseUnit,
    RenewableUnit,
    solve_multi_resource,
)
from ele_trading.operations.multi_resource_intraday import solve_multi_resource_intraday
from ele_trading.operations.resource_runtime import ResourceActual


def _actual_index() -> pd.DatetimeIndex:
    return pd.DatetimeIndex([
        pd.Timestamp("2026-08-09 02:00", tz="Asia/Shanghai"),
    ])


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


def test_multi_resource_intraday_accepts_versioned_bess_actual_soc():
    unit = _bess("bess-a", 4.0)
    previous = solve_multi_resource(
        load_mwh=np.array([4.0, 4.0, 4.0, 4.0]),
        price=np.array([10.0, 10.0, 100.0, 100.0]),
        bess_units=(unit,),
        dt=1.0,
    )
    actual = ResourceActual(
        resource_id="bess-a",
        observed_at=pd.Timestamp("2026-08-09 02:00", tz="Asia/Shanghai"),
        interval_values={
            "soc_mwh": pd.Series(
                [1.5],
                index=pd.DatetimeIndex([
                    pd.Timestamp("2026-08-09 02:00", tz="Asia/Shanghai"),
                ]),
            ),
        },
        quality_flag="approved",
        source_version="meter-v1",
        revision="r1",
    )

    result = solve_multi_resource_intraday(
        load_mwh=np.array([4.0, 4.0]),
        price=np.array([100.0, 100.0]),
        bess_units=(unit,),
        previous_result=previous,
        executed_count=2,
        actuals={"bess-a": actual},
        dt=1.0,
    )

    assert result.initial_soc_mwh == {"bess-a": 1.5}
    assert result.grid_import_mwh is not None
    assert result.grid_import_mwh.shape == (2,)


def test_multi_resource_intraday_caps_renewable_suffix_by_actual_availability():
    unit = _bess("bess-a", 4.0)
    renewable = RenewableUnit(
        name="pv-a",
        available_mw=np.full(4, 2.0),
        curtailment_cost_per_mwh=0.0,
    )
    previous = solve_multi_resource(
        load_mwh=np.full(4, 4.0),
        price=np.array([10.0, 10.0, 100.0, 100.0]),
        bess_units=(unit,),
        renewable_units=(renewable,),
        dt=1.0,
    )
    actuals = {
        "bess-a": ResourceActual(
            resource_id="bess-a",
            observed_at=pd.Timestamp("2026-08-09 02:00", tz="Asia/Shanghai"),
            interval_values={"soc_mwh": pd.Series([2.0], index=_actual_index())},
            quality_flag="approved",
            source_version="meter-v1",
            revision="r1",
        ),
        "pv-a": ResourceActual(
            resource_id="pv-a",
            observed_at=pd.Timestamp("2026-08-09 02:00", tz="Asia/Shanghai"),
            interval_values={"available_mw": pd.Series([0.5], index=_actual_index())},
            quality_flag="approved",
            source_version="meter-v1",
            revision="r1",
        ),
    }

    result = solve_multi_resource_intraday(
        load_mwh=np.full(2, 4.0),
        price=np.array([100.0, 100.0]),
        bess_units=(unit,),
        renewable_units=(RenewableUnit("pv-a", np.full(2, 2.0), 0.0),),
        previous_result=previous,
        executed_count=2,
        actuals=actuals,
        dt=1.0,
    )

    assert max(result.renewable_schedules["pv-a"]["used_mw"]) <= 0.5


def test_multi_resource_intraday_carries_actual_dr_energy_balance_to_suffix():
    unit = _bess("bess-a", 4.0)
    dr = DemandResponseUnit("dr-a", 1.0, 1.0, 0.0, (0, 4))
    previous = solve_multi_resource(
        load_mwh=np.full(4, 4.0),
        price=np.array([10.0, 10.0, 100.0, 100.0]),
        bess_units=(unit,),
        dr_units=(dr,),
        dt=1.0,
    )
    actuals = {
        "bess-a": ResourceActual(
            resource_id="bess-a",
            observed_at=pd.Timestamp("2026-08-09 02:00", tz="Asia/Shanghai"),
            interval_values={"soc_mwh": pd.Series([2.0], index=_actual_index())},
            quality_flag="approved",
            source_version="meter-v1",
            revision="r1",
        ),
        "dr-a": ResourceActual(
            resource_id="dr-a",
            observed_at=pd.Timestamp("2026-08-09 02:00", tz="Asia/Shanghai"),
            interval_values={
                "shift_down_mwh": pd.Series([1.0], index=_actual_index()),
                "shift_up_mwh": pd.Series([0.0], index=_actual_index()),
            },
            quality_flag="approved",
            source_version="meter-v1",
            revision="r1",
        ),
    }

    result = solve_multi_resource_intraday(
        load_mwh=np.full(2, 4.0),
        price=np.array([100.0, 100.0]),
        bess_units=(unit,),
        dr_units=(DemandResponseUnit("dr-a", 1.0, 1.0, 0.0, (0, 2)),),
        previous_result=previous,
        executed_count=2,
        actuals=actuals,
        dt=1.0,
    )

    schedule = result.dr_schedules["dr-a"]
    assert sum(schedule["shift_up_mw"]) - sum(schedule["shift_down_mw"]) == 1.0
