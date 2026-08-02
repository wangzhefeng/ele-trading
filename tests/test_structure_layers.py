"""包层级结构守卫：domain/markets/positions/operations/backtest 的依赖方向。

层级约定（下层不得 import 上层）：

```text
domain（最底层契约）
  ↑ markets（规则插件，可依赖 domain/utils）
  ↑ positions / operations（业务层，可依赖 domain/markets/下层库）
  ↑ trading（编排层，可依赖以上全部）
  ↑ backtest（回测层，驱动 trading 编排做无前瞻回放）
```

demand_response 为品种层，可依赖 domain/markets。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "ele_trading"

# 各包被禁止 import 的上层前缀
FORBIDDEN = {
    "domain": (
        "ele_trading.markets",
        "ele_trading.positions",
        "ele_trading.operations",
        "ele_trading.backtest",
        "ele_trading.trading",
        "ele_trading.demand_response",
        "ele_trading.forecasting",
        "ele_trading.scenario",
        "ele_trading.optimization",
        "ele_trading.data_provider",
    ),
    "markets": (
        "ele_trading.positions",
        "ele_trading.operations",
        "ele_trading.backtest",
        "ele_trading.trading",
        "ele_trading.demand_response",
    ),
    "positions": ("ele_trading.trading", "ele_trading.operations", "ele_trading.backtest"),
    "operations": ("ele_trading.trading", "ele_trading.backtest"),
    "trading": ("ele_trading.backtest",),
    "backtest": (),
    # v3 M6：用户侧为独立领域能力，只依赖 utils，不得耦合市场主链
    "user_side_dispatch": (
        "ele_trading.markets",
        "ele_trading.positions",
        "ele_trading.operations",
        "ele_trading.backtest",
        "ele_trading.trading",
        "ele_trading.demand_response",
        "ele_trading.forecasting",
        "ele_trading.scenario",
        "ele_trading.data_provider",
    ),
}


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _violations(package: str) -> list[str]:
    package_root = SOURCE_ROOT / package
    forbidden = FORBIDDEN[package]
    bad: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        if "todo" in path.relative_to(SOURCE_ROOT).parts:
            continue
        for module in _imported_modules(path):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in forbidden
            ):
                bad.append(
                    f"{path.relative_to(PROJECT_ROOT).as_posix()} -> {module}"
                )
    return bad


def test_domain_imports_no_upper_layer():
    """domain 是最底层契约：任何指向上层包的 import 必须结构失败。"""
    assert _violations("domain") == []


def test_markets_imports_no_business_layer():
    """markets 规则插件只可依赖 domain/utils，不得依赖业务层与编排层。"""
    assert _violations("markets") == []


def test_positions_does_not_import_upper_layers():
    assert _violations("positions") == []


def test_operations_does_not_import_upper_layers():
    assert _violations("operations") == []


def test_trading_does_not_import_backtest():
    assert _violations("trading") == []


def test_user_side_dispatch_stays_independent():
    """user_side_dispatch 为独立领域能力，不得 import 市场主链（v3 M6）。"""
    assert _violations("user_side_dispatch") == []


# ---------------- v3 M4：主链不得 import 具体市场模式插件 ----------------

CONCRETE_MODE_PREFIXES = (
    "ele_trading.markets.single_settlement",
    "ele_trading.markets.dual_settlement",
)
MAIN_CHAIN_PACKAGES = (
    "positions",
    "operations",
    "demand_response",
    "trading",
    "backtest",
)


def _concrete_mode_violations(package: str) -> list[str]:
    package_root = SOURCE_ROOT / package
    bad: list[str] = []
    for path in sorted(package_root.rglob("*.py")):
        if "todo" in path.relative_to(SOURCE_ROOT).parts:
            continue
        for module in _imported_modules(path):
            if any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in CONCRETE_MODE_PREFIXES
            ):
                bad.append(
                    f"{path.relative_to(PROJECT_ROOT).as_posix()} -> {module}"
                )
    return bad


@pytest.mark.parametrize("package", MAIN_CHAIN_PACKAGES)
def test_main_chain_imports_no_concrete_market_mode(package: str):
    """主链只依赖 markets.protocol / markets.sections（v3 M4 / D-002）。"""
    assert _concrete_mode_violations(package) == []
