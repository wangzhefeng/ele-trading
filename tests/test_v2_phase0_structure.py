"""v2 模块边界与归档隔离结构测试。"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
MIGRATION_PACKAGES = (
    "data_provider",
    "forecasting",
    "optimization",
    "scenario",
    "utils",
)
ARCHIVED_DATA_PROVIDER_MODULES = (
    "cvxp_bess_sample.py",
    "user_side_bess_sample.py",
    "user_side_pv_bess_dispatch_sample.py",
    "user_side_pv_dispatch_sample.py",
    "user_side_pv_sample.py",
)
ARCHIVED_OPTIMIZATION_MODULES = (
    ("adapters", "dispatch_adapters.py"),
    ("adapters", "distributed_dispatch_adapters.py"),
    ("algorithms", "user_side_bess_dispatch_pulp.py"),
    ("algorithms", "user_side_bess_dispatch_cvxpy.py"),
    ("algorithms", "user_side_bess_distributed_dispatch_class.py"),
    ("algorithms", "user_side_renewable_bess_dispatch_class.py"),
    ("algorithms", "user_side_renewable_bess_distributed_dispatch_class.py"),
    ("algorithms", "user_side_renewable_dispatch_class.py"),
)
USER_SIDE_DISPATCH_ROOT = SOURCE_ROOT / "ele_trading" / "user_side_dispatch"


def _python_files(root: Path, *, exclude_todo: bool = False) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if not exclude_todo or "todo" not in path.relative_to(root).parts
    )


def _module_names(root: Path, package: str, *, exclude_todo: bool = False) -> list[str]:
    return [
        ".".join((package, *path.relative_to(root).with_suffix("").parts[:-1]))
        if path.name == "__init__.py"
        else ".".join((package, *path.relative_to(root).with_suffix("").parts))
        for path in _python_files(root, exclude_todo=exclude_todo)
    ]


def _import_without(blocked_prefix: str, module_names: list[str]) -> None:
    guard = """
import builtins
import importlib
from importlib.util import resolve_name
import sys

blocked = sys.argv[1]
original_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    package = globals.get("__package__") if globals else None
    resolved = resolve_name("." * level + name, package) if level and package else name
    if resolved == blocked or resolved.startswith(blocked + "."):
        raise ImportError(f"blocked import: {resolved}")
    return original_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
for module_name in sys.argv[2:]:
    importlib.import_module(module_name)
"""
    result = subprocess.run(
        [sys.executable, "-c", guard, blocked_prefix, *module_names],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_phase1a_active_packages_do_not_load_archived_or_cvxpy_modules():
    """active 包入口不可依赖 todo，且不能触发可选 CVXPY 导入。"""
    probe = """
import importlib
import sys

for module_name in sys.argv[1:]:
    importlib.import_module(module_name)
assert not any(
    name == "cvxpy" or name.startswith("cvxpy.") or ".todo" in name
    for name in sys.modules
), sorted(name for name in sys.modules if name == "cvxpy" or name.startswith("cvxpy.") or ".todo" in name)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            probe,
            "ele_trading",
            *(f"ele_trading.{package}" for package in MIGRATION_PACKAGES),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_phase1a_archived_non_cvxpy_imports_do_not_require_cvxpy():
    """归档包及其非 CVXPY 模块应在未安装 CVXPY 时仍可导入。"""
    _import_without(
        "cvxpy",
        [
            "ele_trading.user_side_dispatch",
            "ele_trading.user_side_dispatch.user_side_bess_sample",
            "ele_trading.user_side_dispatch.adapters.dispatch_adapters",
            "ele_trading.user_side_dispatch.algorithms.user_side_bess_dispatch_pulp",
        ],
    )


def test_phase1a_cvxpy_is_an_archived_user_side_optional_dependency():
    """CVXPY 不属于主依赖，只由归档用户侧 optional extra 声明。"""
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    optional_dependencies = pyproject["project"]["optional-dependencies"]

    assert not any(dependency.startswith("cvxpy") for dependency in dependencies)
    assert optional_dependencies["archived-user-side"] == ["cvxpy>=1.9.0"]


def test_phase1a_user_side_modules_exist_only_in_archives():
    """用户侧源码不再留在 active data_provider 或 optimization。"""
    data_provider_root = SOURCE_ROOT / "ele_trading" / "data_provider"
    optimization_root = SOURCE_ROOT / "ele_trading" / "optimization"

    assert (USER_SIDE_DISPATCH_ROOT / "__init__.py").is_file()
    assert (USER_SIDE_DISPATCH_ROOT / "README.md").is_file()
    assert (USER_SIDE_DISPATCH_ROOT / "adapters" / "__init__.py").is_file()
    assert (USER_SIDE_DISPATCH_ROOT / "algorithms" / "__init__.py").is_file()
    assert all(
        (USER_SIDE_DISPATCH_ROOT / module_name).is_file()
        and not (data_provider_root / module_name).exists()
        for module_name in ARCHIVED_DATA_PROVIDER_MODULES
    )
    assert all(
        (USER_SIDE_DISPATCH_ROOT / subdir / module_name).is_file()
        and not (optimization_root / module_name).exists()
        for subdir, module_name in ARCHIVED_OPTIMIZATION_MODULES
    )
    assert not (data_provider_root / "todo").exists()
    assert not (optimization_root / "todo").exists()


def test_phase1a_contracts_are_split_between_active_and_archived_packages():
    """active 仅保留通用结果契约，用户侧与 CVXPY 契约只在归档包。"""
    optimization_root = SOURCE_ROOT / "ele_trading" / "optimization"
    assert (optimization_root / "contracts.py").is_file()
    assert not (optimization_root / "interfaces.py").exists()

    active_contracts = (optimization_root / "contracts.py").read_text(encoding="utf-8")
    archived_contracts = (USER_SIDE_DISPATCH_ROOT / "interfaces.py").read_text(encoding="utf-8")
    assert "BESSArbitrageResult" in active_contracts
    assert "MPCStepResult" in active_contracts
    assert "UserSideBESSParams" not in active_contracts
    assert "UserSideBESSParams" in archived_contracts
    assert "CvxpBESSDispatchInput" in archived_contracts


def test_phase1a_active_package_has_no_user_side_or_todo_exports():
    """active optimization API 不提供用户侧、分布式或 todo 兼容出口。"""
    probe = """
import importlib

optimization = importlib.import_module("ele_trading.optimization")
data_provider = importlib.import_module("ele_trading.data_provider")
for package, names in {
    optimization: (
        "run_user_side_bess_dispatch",
        "run_user_side_renewable_bess_dispatch",
        "run_user_side_renewable_bess_distributed_dispatch",
        "run_cvxp_bess_dispatch",
    ),
    data_provider: (
        "build_user_side_bess_dispatch_input",
        "build_cvxp_bess_dispatch_input",
    ),
}.items():
    for name in names:
        try:
            getattr(package, name)
        except AttributeError:
            continue
        raise AssertionError(f"unexpected active export: {package.__name__}.{name}")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_user_side_dispatch_is_active_independent_domain():
    """v3 D-001/M6：用户侧已恢复为活动独立领域，进入常规 pytest 收集，
    且结构守卫 test_user_side_dispatch_stays_independent 强制其只依赖 utils。
    pyproject 不再通过 norecursedirs 排除该目录。"""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "norecursedirs" not in pyproject, (
        "v3 D-001/M6 已恢复用户侧为活动领域，pyproject 不得再用 norecursedirs 排除"
    )


def test_investment_estimation_production_sources_do_not_import_active_ele_trading():
    """投资收益测算包必须保持自包含，避免反向依赖交易主链路。"""
    production_root = SOURCE_ROOT / "investment_estimation"
    _import_without(
        "ele_trading",
        _module_names(production_root, "investment_estimation", exclude_todo=True),
    )
