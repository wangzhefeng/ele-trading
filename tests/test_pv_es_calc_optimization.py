"""PV-storage optimization entry tests."""

from pathlib import Path
import importlib.util


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPTIMIZATION_PATH = PROJECT_ROOT / "src" / "pv_es_calc" / "optimization.py"
CONFIG_PATH = PROJECT_ROOT / "src" / "pv_es_calc" / "config" / "pv_es_calc.yaml"


def _load_optimization_module():
    spec = importlib.util.spec_from_file_location("pv_es_optimization_entry", OPTIMIZATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pv_es_config_loads_default_yaml():
    """Default YAML config should load and contain version mappings."""
    module = _load_optimization_module()

    config = module.load_pv_es_config(CONFIG_PATH)

    assert config["data"]["data_dir"].endswith("data/profit_calc/pv_es")
    assert config["run"]["method_version"] == "v4"
    assert set(config["version_methods"]) >= {"v1", "v2", "v3", "v4", "v5"}


def test_unified_entry_scripts_do_not_parse_cli_arguments():
    """Unified pv_es_calc scripts should read runtime settings only from YAML."""
    optimization_source = OPTIMIZATION_PATH.read_text(encoding="utf-8")
    simulation_source = (PROJECT_ROOT / "src" / "pv_es_calc" / "simulation.py").read_text(
        encoding="utf-8"
    )

    assert "argparse" not in optimization_source
    assert "argparse" not in simulation_source
    assert "--method-version" not in optimization_source + simulation_source
    assert "--config" not in optimization_source + simulation_source


def test_run_one_scale_returns_strategy_and_writes_csv(tmp_path):
    """run_one_scale should return a strategy frame and write a schedule CSV."""
    module = _load_optimization_module()
    config = module.load_pv_es_config(CONFIG_PATH)
    config["run"]["output_dir"] = str(tmp_path)
    config["run"]["start_time"] = "2025-01-01 00:00:00"
    config["run"]["end_time"] = "2025-01-02 00:00:00"
    config["run"]["method_version"] = "v4"

    result = module.run_one_scale(config, 100)

    assert not result.empty
    assert "grid_import" in result.columns
    assert (tmp_path / "opt_result-v4" / "es_scale_experiment_optim" / "schedule_result_scale_100.csv").exists()


def test_run_capacity_search_uses_configured_scale_list(tmp_path):
    """Capacity search should honor the small configured scale list."""
    module = _load_optimization_module()
    config = module.load_pv_es_config(CONFIG_PATH)
    config["run"]["output_dir"] = str(tmp_path)
    config["run"]["start_time"] = "2025-01-01 00:00:00"
    config["run"]["end_time"] = "2025-01-02 00:00:00"
    config["run"]["es_scale_list"] = [0, 100]
    config["run"]["method_version"] = "v5"
    config["run"]["max_workers"] = 2

    results = module.run_capacity_search(config)

    assert [scale for scale, _ in results] == [0, 100]


def test_version_methods_from_yaml_override_scheduler_defaults(tmp_path):
    """Version method weights in YAML config should drive the scheduler."""
    module = _load_optimization_module()
    config = module.load_pv_es_config(CONFIG_PATH)
    config["run"]["output_dir"] = str(tmp_path)
    config["run"]["start_time"] = "2025-01-01 00:00:00"
    config["run"]["end_time"] = "2025-01-02 00:00:00"
    config["run"]["method_version"] = "v4"
    config["version_methods"]["v4"]["dispatch_mode"] = "rule"

    result = module.run_one_scale(config, 100)

    # Rule mode never charges from PV in the current v5-style implementation.
    assert result["pv_to_battery"].sum() == 0


def test_unknown_method_version_fails_during_config_validation():
    """run.method_version must be present in version_methods."""
    module = _load_optimization_module()
    config = module.load_pv_es_config(CONFIG_PATH)
    config["run"]["method_version"] = "missing"

    try:
        module.validate_pv_es_config(config)
    except ValueError as exc:
        assert "version_methods" in str(exc)
    else:
        raise AssertionError("missing method version should fail validation")
