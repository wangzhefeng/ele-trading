"""Phase 1B active-package ownership boundaries."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "ele_trading"
REMOVED_ACTIVE_PACKAGES = ("control", "demand", "evaluation")
REMOVED_ACTIVE_UTILITIES = (
    "demand_charge.py",
    "energy_price.py",
    "pv_es_plot.py",
    "bess_charge_discharge_plot.py",
    "plot_ts.py",
)
REMOVED_IMPORT_PREFIXES = tuple(f"ele_trading.{package}" for package in REMOVED_ACTIVE_PACKAGES)


def _active_python_files() -> list[Path]:
    roots = (SOURCE_ROOT, PROJECT_ROOT / "app", PROJECT_ROOT / "tests")
    return [
        path
        for root in roots
        for path in root.rglob("*.py")
        if "todo" not in path.relative_to(root).parts
    ]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_phase1b_active_packages_are_removed_and_backtest_owns_backtest_and_metrics():
    """Backtest package owns backtest/metrics and the chain is exposed via the unified app."""
    assert all(not (SOURCE_ROOT / package).exists() for package in REMOVED_ACTIVE_PACKAGES)
    assert (SOURCE_ROOT / "backtest" / "backtest.py").is_file()
    assert (SOURCE_ROOT / "backtest" / "metrics.py").is_file()
    assert (PROJECT_ROOT / "app" / "trading" / "run_pipeline.py").is_file()
    assert not (PROJECT_ROOT / "app" / "evaluation").exists()


def test_phase1b_active_code_does_not_import_removed_packages_or_todo():
    """Active source, apps, and normal tests cannot reach removed package APIs."""
    for path in _active_python_files():
        imports = _imports(path)
        assert not any(
            module == prefix or module.startswith(f"{prefix}.")
            for module in imports
            for prefix in REMOVED_IMPORT_PREFIXES
        ), path
        if path.is_relative_to(SOURCE_ROOT):
            assert not any(module == "todo" or ".todo" in module for module in imports), path


def test_phase1b_active_utils_exclude_user_side_billing_and_plotting_helpers():
    """Active utils retain generic helpers only."""
    utils_root = SOURCE_ROOT / "utils"
    assert all(not (utils_root / module).exists() for module in REMOVED_ACTIVE_UTILITIES)
    utils_init = (utils_root / "__init__.py").read_text(encoding="utf-8")
    assert "monthly_peak_demand_cost" not in utils_init
    assert "flatten_valley_price_diff" not in utils_init
    assert "plot_strategy_power_detail" not in utils_init
