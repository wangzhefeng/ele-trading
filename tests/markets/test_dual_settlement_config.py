"""Unit tests for dual-settlement config loading and validation.

按插件结算字段子集适配（v1 归档中的 two_stage/bid/risk/策略字段不随插件移植）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ele_trading.markets.dual_settlement.config_loader import load_market_config

DUAL_YAML = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "markets"
    / "dual_settlement.yaml"
)


class TestLoadDualSettlementConfig:
    def test_load_defaults(self):
        config = load_market_config(DUAL_YAML)
        assert config.settlement_mode == "band_deviation"
        assert config.settle_periods == 96
        assert config.lam_l == 0.95
        assert config.lam_u == 1.05
        assert config.lam_l_long == 0.90
        assert config.lam_u_long == 1.05
        assert config.m_long == 1.2
        assert config.cpen_long_applies_to_storage is True

    def test_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_market_config(tmp_path / "nonexistent.yaml")

    def test_settle_periods_validated(self, tmp_path):
        bad = DUAL_YAML.read_text().replace("settle_periods: 96", "settle_periods: 50")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="settle_periods"):
            load_market_config(p)

    def test_settlement_mode_validated(self, tmp_path):
        bad = DUAL_YAML.read_text().replace(
            "settlement_mode: band_deviation", "settlement_mode: other_mode"
        )
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="band_deviation"):
            load_market_config(p)

    def test_band_validated(self, tmp_path):
        bad = DUAL_YAML.read_text().replace("lam_l: 0.95", "lam_l: 1.10")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="deviation band"):
            load_market_config(p)

    def test_mid_long_band_validated(self, tmp_path):
        bad = DUAL_YAML.read_text().replace("lam_l_long: 0.90", "lam_l_long: 1.10")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="mid-long band"):
            load_market_config(p)
