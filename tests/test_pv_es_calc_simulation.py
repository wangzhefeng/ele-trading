"""PV-storage simulation summary tests."""

from pathlib import Path
import importlib.util

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPTIMIZATION_PATH = PROJECT_ROOT / "src" / "pv_es_calc" / "optimization.py"
SIMULATION_PATH = PROJECT_ROOT / "src" / "pv_es_calc" / "simulation.py"
CONFIG_PATH = PROJECT_ROOT / "src" / "pv_es_calc" / "config" / "pv_es_calc.yaml"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_simulate_one_scale_returns_consistent_revenue(tmp_path):
    """Simulation should compute revenue as baseline net cost minus optimized net cost."""
    optimization = _load_module("pv_es_optimization_for_sim", OPTIMIZATION_PATH)
    simulation = _load_module("pv_es_simulation_entry", SIMULATION_PATH)
    config = optimization.load_pv_es_config(CONFIG_PATH)
    config["run"]["output_dir"] = str(tmp_path)
    config["run"]["start_time"] = "2025-01-01 00:00:00"
    config["run"]["end_time"] = "2025-01-02 00:00:00"
    config["run"]["method_version"] = "v4"
    optimization.run_one_scale(config, 100)

    summary = simulation.simulate_one_scale(config, 100)

    assert summary["revenue"] == pytest.approx(summary["baseline_cost"] - summary["opt_cost"])
    assert summary["grid_import_energy"] >= 0
    assert summary["pv_to_grid_energy"] >= 0


def test_run_simulation_summary_preserves_english_internal_columns(tmp_path):
    """Internal summary DataFrame should use English keys; export may add Chinese labels."""
    optimization = _load_module("pv_es_optimization_for_summary", OPTIMIZATION_PATH)
    simulation = _load_module("pv_es_simulation_summary", SIMULATION_PATH)
    config = optimization.load_pv_es_config(CONFIG_PATH)
    config["run"]["output_dir"] = str(tmp_path)
    config["run"]["start_time"] = "2025-01-01 00:00:00"
    config["run"]["end_time"] = "2025-01-02 00:00:00"
    config["run"]["es_scale_list"] = [100]
    config["run"]["method_version"] = "v4"
    optimization.run_capacity_search(config)

    summary_df = simulation.run_simulation_summary(config)

    assert "revenue" in summary_df.columns
    assert "revenue_收益" not in summary_df.columns
    assert (tmp_path / "opt_result-v4" / "estimate_result_scale_all_optim.csv").exists()
