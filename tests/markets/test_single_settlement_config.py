"""Single-settlement sectioned config loading tests (v3 M2 / D-003)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ele_trading.markets.single_settlement.config_loader import load_market_config
from ele_trading.markets.single_settlement.contracts import (
    CURRENT_SCHEMA_VERSION,
    MarketConfig,
)
from ele_trading.utils.io import read_yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SINGLE_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"


def _write(tmp_path: Path, payload: dict) -> Path:
    """写出配置文件（JSON 是 YAML 子集，read_yaml 可正常解析）。"""
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_payload() -> dict:
    return read_yaml(SINGLE_YAML)


def test_loads_sectioned_config_into_typed_sections():
    config = load_market_config(SINGLE_YAML)

    assert config.schema_version == CURRENT_SCHEMA_VERSION
    assert config.market.dt == 0.25
    assert config.market.settlement_mode == "single_settlement"
    assert config.scenario.scenario_method == "lhs"
    assert config.bess.operational_power_margin == 0.80
    assert config.dr.dr_window_start < config.dr.dr_window_end
    assert config.monthly.monthly_price_floor < config.monthly.monthly_price_cap
    assert config.solver.solver_name == "cbc"


def test_default_construction_stays_valid():
    """MarketConfig() 默认构造必须仍然可用（测试与 demo 的兼容面）。"""
    config = MarketConfig()

    assert config.schema_version == CURRENT_SCHEMA_VERSION
    assert config.market.settle_periods == 96


def test_rejects_missing_schema_version(tmp_path):
    payload = _valid_payload()
    del payload["schema_version"]

    with pytest.raises(ValueError, match="schema_version"):
        load_market_config(_write(tmp_path, payload))


def test_rejects_wrong_schema_version(tmp_path):
    payload = _valid_payload()
    payload["schema_version"] = CURRENT_SCHEMA_VERSION + 1

    with pytest.raises(ValueError, match="schema_version"):
        load_market_config(_write(tmp_path, payload))


def test_rejects_missing_section(tmp_path):
    payload = _valid_payload()
    del payload["dr"]

    with pytest.raises(ValueError, match="missing=.*dr"):
        load_market_config(_write(tmp_path, payload))


def test_rejects_unknown_section_field(tmp_path):
    payload = _valid_payload()
    payload["bess"]["unknown_field"] = 1.0

    with pytest.raises(ValueError, match="unknown=.*unknown_field"):
        load_market_config(_write(tmp_path, payload))


def test_rejects_legacy_flat_format(tmp_path):
    """旧扁平格式（无 schema_version、long_recovery 独立区段）必须被拒绝。"""
    legacy = {
        "market": {
            "market_name": "single_settlement",
            "settlement_mode": "single_settlement",
            "settle_periods": 96,
            "dt": 0.25,
        },
        "long_recovery": {"long_recovery_lower_ratio": 0.9},
    }

    with pytest.raises(ValueError, match="schema_version"):
        load_market_config(_write(tmp_path, legacy))


def test_section_field_validation_fires_on_load(tmp_path):
    """子对象构造即校验：非法取值在加载时报错。"""
    payload = _valid_payload()
    payload["market"]["dt"] = 1.0

    with pytest.raises(ValueError, match="dt must be 0.25"):
        load_market_config(_write(tmp_path, payload))
