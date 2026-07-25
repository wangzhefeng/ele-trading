"""Unit tests for market config loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ele_trading.trading.config_loader import load_market_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MENGXI_YAML = PROJECT_ROOT / "configs" / "market_mengxi.yaml"


class TestLoadMengxiConfig:
    def test_load_defaults(self):
        config = load_market_config(MENGXI_YAML)
        assert config.settlement_mode == "mengxi_band"
        assert config.lam_l == 0.95
        assert config.lam_u == 1.05
        assert config.dayahead_mode == "B"
        assert config.risk_max_step_ratio == 0.2
        assert config.risk_daily_qty_band == 0.15
        assert config.risk_long_band_check is True
        assert config.dayahead_price_reporting is False

    def test_settle_periods_validated(self, tmp_path):
        bad = MENGXI_YAML.read_text().replace("settle_periods: 96", "settle_periods: 50")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="settle_periods"):
            load_market_config(p)

    def test_mode_validated(self, tmp_path):
        bad = MENGXI_YAML.read_text().replace("mode: B", "mode: X")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="dayahead.mode"):
            load_market_config(p)

    def test_band_validated(self, tmp_path):
        bad = MENGXI_YAML.read_text().replace("lam_l: 0.95", "lam_l: 1.10")
        p = tmp_path / "bad.yaml"
        p.write_text(bad)
        with pytest.raises(ValueError, match="deviation band"):
            load_market_config(p)
