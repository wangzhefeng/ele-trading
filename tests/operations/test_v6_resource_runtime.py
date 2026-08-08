"""V6-1：统一资源运行计划契约及多资源适配。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.operations import (
    ResourceOperationalPlan as ExportedResourceOperationalPlan,
)
from ele_trading.operations.multi_resource import (
    BESSUnit,
    DemandResponseUnit,
    RenewableUnit,
    solve_multi_resource,
)
from ele_trading.operations.resource_runtime import (
    PortfolioSettlementInput,
    ResourceActual,
    ResourceOperationalPlan,
    ResourceSchedule,
)


VALID_TIMES = pd.date_range("2026-08-09 00:00", periods=4, freq="h", tz="Asia/Shanghai")


def test_multi_resource_result_adapts_to_single_versioned_runtime_plan():
    assert ExportedResourceOperationalPlan is ResourceOperationalPlan
    result = solve_multi_resource(
        load_mwh=np.full(4, 5.0),
        price=np.array([10.0, 10.0, 100.0, 100.0]),
        bess_units=(
            BESSUnit(
                name="bess-a",
                soc0=3.0,
                soc_min=1.0,
                soc_max=5.0,
                p_charge_max=2.0,
                p_discharge_max=2.0,
                eta_charge=1.0,
                eta_discharge=1.0,
            ),
        ),
        dr_units=(
            DemandResponseUnit(
                name="dr-a",
                max_shift_down_mw=1.0,
                max_shift_up_mw=1.0,
                cost_per_mwh=1.0,
                window=(0, 4),
            ),
        ),
        renewable_units=(
            RenewableUnit(
                name="pv-a",
                available_mw=np.array([1.0, 1.0, 1.0, 1.0]),
                curtailment_cost_per_mwh=0.0,
            ),
        ),
        dt=1.0,
    )

    plan = ResourceOperationalPlan.from_multi_resource_result(
        result=result,
        valid_times=VALID_TIMES,
        dt_hours=1.0,
        plan_version="multi-resource-v1",
    )

    assert set(plan.schedules) == {"bess-a", "dr-a", "pv-a"}
    assert plan.schedules["bess-a"].resource_type == "bess"
    assert set(plan.schedules["bess-a"].interval_values) == {
        "charge_mwh",
        "discharge_mwh",
        "soc_mwh",
    }
    assert set(plan.schedules["dr-a"].interval_values) == {
        "shift_up_mwh",
        "shift_down_mwh",
    }
    assert set(plan.schedules["pv-a"].interval_values) == {
        "used_mwh",
        "curtailed_mwh",
    }
    assert plan.settlement_input.grid_import_mwh.index.equals(VALID_TIMES)
    assert plan.settlement_input.resource_delivery_mwh["bess-a"].equals(
        plan.schedules["bess-a"].interval_values["discharge_mwh"]
    )
    assert plan.settlement_input.resource_delivery_mwh["pv-a"].equals(
        plan.schedules["pv-a"].interval_values["used_mwh"]
    )


def test_runtime_plan_rejects_schedule_and_settlement_time_mismatch():
    schedule = ResourceSchedule(
        resource_id="bess-a",
        resource_type="bess",
        interval_values={
            "discharge_mwh": pd.Series([1.0], index=VALID_TIMES[:1]),
        },
        plan_version="v1",
    )
    settlement = PortfolioSettlementInput(
        grid_import_mwh=pd.Series([1.0], index=VALID_TIMES[1:2]),
        resource_delivery_mwh={
            "bess-a": pd.Series([1.0], index=VALID_TIMES[1:2]),
        },
    )

    with pytest.raises(ValueError, match="schedule interval index"):
        ResourceOperationalPlan(
            plan_version="v1",
            schedules={"bess-a": schedule},
            settlement_input=settlement,
        )


def test_runtime_plan_attaches_versioned_resource_actuals():
    schedule = ResourceSchedule(
        resource_id="bess-a",
        resource_type="bess",
        interval_values={"discharge_mwh": pd.Series([1.0], index=VALID_TIMES[:1])},
        plan_version="v1",
    )
    plan = ResourceOperationalPlan(
        plan_version="v1",
        schedules={"bess-a": schedule},
        settlement_input=PortfolioSettlementInput(
            grid_import_mwh=pd.Series([2.0], index=VALID_TIMES[:1]),
            resource_delivery_mwh={"bess-a": pd.Series([1.0], index=VALID_TIMES[:1])},
        ),
    )
    actual = ResourceActual(
        resource_id="bess-a",
        observed_at=VALID_TIMES[0],
        interval_values={"soc_mwh": pd.Series([2.0], index=VALID_TIMES[:1])},
        quality_flag="approved",
        source_version="meter-v1",
        revision="r1",
    )

    enriched = plan.with_actuals({"bess-a": actual})

    assert enriched.actuals == {"bess-a": actual}
    assert enriched.plan_version == plan.plan_version
