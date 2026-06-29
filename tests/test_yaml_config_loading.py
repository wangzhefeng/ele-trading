from pathlib import Path


def test_yaml_config_loading_goes_through_read_yaml():
    """配置 YAML 读取应统一走 ele_trading.utils.io.read_yaml。"""
    project_root = Path(__file__).resolve().parents[1]
    allowed = {
        project_root / "src" / "ele_trading" / "utils" / "io.py",
    }
    blocked_tokens = (
        "yaml." + "safe_load",
        "import " + "yaml",
        "from " + "yaml",
    )

    offenders = []
    for root_name in ("src", "app", "tests"):
        for path in (project_root / root_name).rglob("*.py"):
            if path in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in blocked_tokens):
                offenders.append(path.relative_to(project_root).as_posix())

    assert offenders == []


def test_plain_yaml_loader_wrappers_are_not_exported():
    """只返回 dict 的配置读取薄包装不应作为 data_provider API 暴露。"""
    import ele_trading.data_provider as data_provider

    removed_exports = [
        "load_user_side_bess_dispatch_config",
        "load_user_side_pv_dispatch_config",
        "load_user_side_pv_bess_dispatch_config",
    ]

    assert [name for name in removed_exports if hasattr(data_provider, name)] == []


def test_plain_yaml_loader_wrappers_are_removed_from_app_scripts():
    """app 脚本不应保留仅委托 read_yaml 的本地配置 loader。"""
    project_root = Path(__file__).resolve().parents[1]
    targets = [
        project_root / "app" / "legacy" / "run_legacy_data_preparation.py",
        project_root / "app" / "legacy" / "run_wind_pv_legacy_profit_eval.py",
        project_root / "app" / "legacy" / "run_wind_pv_legacy_market_trading.py",
    ]

    offenders = []
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "def load_bridge_config" in text or "def load_config" in text:
            offenders.append(path.relative_to(project_root).as_posix())

    assert offenders == []
