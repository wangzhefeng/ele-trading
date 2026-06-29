import numpy as np
import pandas as pd
import pytest

from ele_trading.capacity_planning.bess_capacity_operating_planner import (
    BESSCapacityResult,
    BESSPlanConfig,
    simulate_bess_operation,
    plan_energy_system,
)


def _make_load_and_price(n: int = 48) -> tuple[pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range("2025-04-01", periods=n, freq="h")
    load = np.where((idx.hour >= 10) & (idx.hour < 22), 120.0, 80.0)
    price_type = np.where((idx.hour >= 0) & (idx.hour < 6), "谷", "平")
    price = np.where(price_type == "谷", 0.25, 0.9)
    price[(idx.hour >= 18) & (idx.hour < 22)] = 1.2
    price_type[(idx.hour >= 18) & (idx.hour < 22)] = "峰"

    df_load = pd.DataFrame({"Time": idx, "P_kw": load})
    df_price = pd.DataFrame({"Time": idx, "value": price, "type": price_type})
    return df_load, df_price


def test_plan_energy_system_requires_ele_price():
    df_load, _ = _make_load_and_price()

    with pytest.raises(ValueError, match="ele_price"):
        plan_energy_system(df_load)


def test_simulate_bess_operation_returns_timeseries_outputs():
    df_load, df_price = _make_load_and_price(24)
    cfg = BESSPlanConfig(version="optim", transform_capacity=140.0)

    result = simulate_bess_operation(
        df_load,
        ele_price=df_price,
        bess_kwh=100.0,
        cfg=cfg,
    )

    assert len(result["schedule_df"]) == len(df_load)
    assert len(result["es_charge_df"]) == len(df_load)
    assert len(result["total_load_df"]) == len(df_load)
    assert len(result["es_soc_df"]) == len(df_load) + 1
    assert np.isfinite(result["revenue"])
    assert result["profile_name"] == "optim"


def test_plan_energy_system_scans_capacity_and_returns_best_result():
    df_load, df_price = _make_load_and_price()
    cfg = BESSPlanConfig(
        version="basic",
        batt_hi_max_kwh=200.0,
        search_points=5,
        transform_capacity=200.0,
    )

    result = plan_energy_system(df_load, ele_price=df_price, cfg=cfg)

    assert isinstance(result, BESSCapacityResult)
    assert result.feasible is True
    assert result.bess_kwh >= 0.0
    assert result.profile_name == "basic"
    assert result.time_splitting == "day"
    assert result.schedule_df is not None
    assert len(result.schedule_df) == len(df_load)
