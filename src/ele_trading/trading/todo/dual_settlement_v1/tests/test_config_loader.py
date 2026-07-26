"""Unit tests for market config loading and validation."""

from __future__ import annotations

from math import isnan
from pathlib import Path

import pytest

from ele_trading.trading.todo.dual_settlement_v1.config_loader import (
    load_market_config,
)

MENGXI_YAML = Path(__file__).resolve().parents[1] / "market_mengxi.yaml"


def _write_two_stage_config(
    path: Path,
    *,
    positive_cost: object,
    negative_cost: object,
) -> None:
    text = MENGXI_YAML.read_text()
    for field, value in {
        "scenario_deviation_cost_positive": positive_cost,
        "scenario_deviation_cost_negative": negative_cost,
    }.items():
        original = (
            f"  {field}: 0.25  # 元/MWh (TODO(rule-confirm))"
        )
        assert text.count(original) == 1
        yaml_value = (
            ".nan"
            if isinstance(value, float) and isnan(value)
            else str(value)
        )
        text = text.replace(
            original,
            f"  {field}: {yaml_value}  # 元/MWh (TODO(rule-confirm))",
        )
    path.write_text(text)


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

    def test_two_stage_scenario_deviation_costs_are_mapped(self, tmp_path):
        path = tmp_path / "market.yaml"
        _write_two_stage_config(
            path,
            positive_cost=1.75,
            negative_cost=2.25,
        )

        config = load_market_config(path)

        assert config.two_stage_scenario_deviation_cost_positive == 1.75
        assert config.two_stage_scenario_deviation_cost_negative == 2.25

    @pytest.mark.parametrize(
        ("positive_cost", "negative_cost", "invalid_field"),
        [
            (-0.01, 0.25, "positive"),
            (float("nan"), 0.25, "positive"),
            ("not-a-number", 0.25, "positive"),
            (0.25, -0.01, "negative"),
        ],
    )
    def test_two_stage_scenario_deviation_costs_are_validated(
        self,
        tmp_path,
        positive_cost,
        negative_cost,
        invalid_field,
    ):
        path = tmp_path / "market.yaml"
        _write_two_stage_config(
            path,
            positive_cost=positive_cost,
            negative_cost=negative_cost,
        )

        with pytest.raises(
            ValueError,
            match=(
                "two_stage.scenario_deviation_cost_"
                f"{invalid_field}"
            ),
        ):
            load_market_config(path)
