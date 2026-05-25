"""PV-storage unified scheduler tests."""

from pathlib import Path
import importlib.util

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER_PATH = (
    PROJECT_ROOT
    / "src"
    / "pv_es_calc"
    / "optimization"
    / "EsArbitraryRangeScheduler_withMaxDemand.py"
)
DATA_DIR = PROJECT_ROOT / "data" / "profit_calc" / "pv_es"
EXPECTED_COLUMNS = {
    "value",
    "pv_to_load",
    "pv_to_battery",
    "pv_to_grid",
    "grid_to_load",
    "grid_to_battery",
    "battery_charge",
    "battery_discharge",
    "grid_import",
    "soc",
    "net_load_after_dispatch",
}


def _load_scheduler_class():
    spec = importlib.util.spec_from_file_location("pv_es_unified_scheduler", SCHEDULER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.EsArbitraryRangeScheduler_withMaxDemand


def _load_input_frame(days: int = 1) -> pd.DataFrame:
    demand = pd.read_csv(DATA_DIR / "demand_load.csv")
    pv = pd.read_csv(DATA_DIR / "pv_load.csv", encoding="utf-8-sig")
    price = pd.read_csv(DATA_DIR / "ele_price.csv", encoding="utf-8-sig")
    for frame in (demand, pv, price):
        frame["time"] = pd.to_datetime(frame["time"])
    end_time = demand["time"].min() + pd.Timedelta(days=days)
    merged = (
        demand.rename(columns={"value": "demand_load"})
        .merge(pv.rename(columns={"value": "pv_load"}), on="time")
        .merge(price.rename(columns={"value": "ele_price", "type": "ele_type"}), on="time")
    )
    return merged[merged["time"] < end_time]


def _run_scheduler(method_version: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = _load_input_frame()
    scheduler_cls = _load_scheduler_class()
    scheduler = scheduler_cls(
        schedule_time_range=frame["time"].tolist(),
        demand_load=frame["demand_load"].tolist(),
        ele_prices=frame["ele_price"].tolist(),
        ele_types=frame["ele_type"].tolist(),
        pv_load=frame["pv_load"].tolist(),
        devices_info=[
            {
                "usable_depth": 0.90,
                "charge_loss": 0.92,
                "discharge_loss": 0.95,
                "es_charge_max": 100.0,
                "es_charge_min": -100.0,
                "es_capacity_max": 200.0,
                "es_capacity_min": 0.0,
                "transform_capacity": 1600.0,
            }
        ],
        current_soc_list=[0.0],
        max_demand_price=33.8,
        freq_minutes=15,
        method_version=method_version,
    )
    return frame, scheduler.run()[0]


def test_unified_scheduler_outputs_expected_columns_for_all_versions():
    """v1-v5 should all return the same strategy output schema."""
    for version in ("v1", "v2", "v3", "v4", "v5"):
        _, result = _run_scheduler(version)
        assert EXPECTED_COLUMNS.issubset(result.columns)
        assert len(result) == 96


def test_unified_scheduler_preserves_energy_balance_for_all_versions():
    """PV allocation, demand supply, and grid import identities must hold."""
    for version in ("v1", "v2", "v3", "v4", "v5"):
        frame, result = _run_scheduler(version)
        np.testing.assert_allclose(
            result["pv_to_load"] + result["pv_to_battery"] + result["pv_to_grid"],
            frame["pv_load"],
            atol=1e-3,
        )
        np.testing.assert_allclose(
            result["pv_to_load"] + result["battery_discharge"] + result["grid_to_load"],
            frame["demand_load"],
            atol=1e-3,
        )
        np.testing.assert_allclose(
            result["grid_import"],
            result["grid_to_load"] + result["grid_to_battery"],
            atol=1e-3,
        )
        np.testing.assert_allclose(
            result["value"],
            np.where(
                np.abs(result["battery_discharge"] - result["battery_charge"]) < 0.1,
                0.0,
                np.around(result["battery_discharge"] - result["battery_charge"], decimals=3),
            ),
            atol=1e-3,
        )


def test_unified_scheduler_exposes_version_parameters():
    """Version settings should map v2/v3/v4 to distinct noon PV preferences."""
    scheduler_cls = _load_scheduler_class()

    assert scheduler_cls.version_parameters("v1")["dispatch_mode"] == "lp"
    assert scheduler_cls.version_parameters("v2")["noon_pv_to_battery_priority_weight"] > 0
    assert scheduler_cls.version_parameters("v3")["noon_pv_to_load_priority_weight"] > 0
    assert scheduler_cls.version_parameters("v4")["noon_pv_to_grid_penalty_weight"] > 0
    assert scheduler_cls.version_parameters("v5")["dispatch_mode"] == "rule"
