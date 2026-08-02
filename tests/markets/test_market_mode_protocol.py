"""MarketMode 协议与双结算插件接入的集成测试（v3 M4 / D-002）。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ele_trading.markets.protocol import MarketMode, SettlementEngine
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.markets.dual_settlement.mode import DUAL_SETTLEMENT_MODE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SINGLE_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
DUAL_YAML = PROJECT_ROOT / "configs" / "markets" / "dual_settlement.yaml"


# ------------------------------------------------------------------ #
#  协议符合性
# ------------------------------------------------------------------ #

@pytest.mark.parametrize(
    "mode",
    [SINGLE_SETTLEMENT_MODE, DUAL_SETTLEMENT_MODE],
    ids=["single_settlement", "dual_settlement"],
)
def test_mode_satisfies_protocol(mode):
    """两个市场模式都满足 MarketMode / SettlementEngine 协议。"""
    assert isinstance(mode, MarketMode)
    assert isinstance(mode.settlement, SettlementEngine)
    assert isinstance(mode.name, str) and mode.name


# ------------------------------------------------------------------ #
#  双结算经 Protocol 接入的集成用例
# ------------------------------------------------------------------ #

def test_dual_mode_loads_own_config_via_protocol():
    """双结算模式经统一 load_config 入口加载自己的配置对象。"""
    config = DUAL_SETTLEMENT_MODE.load_config(DUAL_YAML)
    assert config.settlement_mode == "band_deviation"
    assert config.settle_periods == 96
    assert 0.0 < config.lam_l < config.lam_u


def test_dual_engine_building_blocks_via_protocol():
    """双结算引擎构件经 SettlementEngine 协议调用，数值与插件一致。"""
    engine: SettlementEngine = DUAL_SETTLEMENT_MODE.settlement
    q_real = np.array([2.0, 3.0])
    p_real = np.array([400.0, 500.0])
    q_long = np.array([1.0, 1.5])
    p_long = np.array([300.0, 300.0])

    energy = engine.compute_energy_cost(q_real, p_real)
    np.testing.assert_allclose(energy, [800.0, 1500.0])

    diff = engine.compute_contract_difference(q_long, p_long, p_ref=p_real)
    np.testing.assert_allclose(diff, [-100.0, -300.0])


def test_dual_engine_builds_mode_specific_report():
    """双结算报告：cost_daily = C + Cpen_dayah + Cpen_long。"""
    report = DUAL_SETTLEMENT_MODE.settlement.build_settlement_report(
        c_daily=1000.0,
        cpen_dayah=50.0,
        cpen_long=20.0,
        cost_baseline=1200.0,
    )
    assert report.cost_daily == pytest.approx(1070.0)
    assert report.delta_cost == pytest.approx(130.0)


def test_dual_engine_report_rejects_missing_fields():
    with pytest.raises(ValueError, match="cpen_dayah"):
        DUAL_SETTLEMENT_MODE.settlement.build_settlement_report(
            c_daily=1000.0,
            cost_baseline=1200.0,
        )


def test_dual_engine_dr_settlement_explicitly_unsupported():
    """双结算当前无 DR 产品语义：显式失败而非伪造结果（不变量 6）。"""
    with pytest.raises(NotImplementedError, match="DR"):
        DUAL_SETTLEMENT_MODE.settlement.compute_dr_settlement(
            committed_qty=1.0,
            executed_window_discharge_mwh=1.0,
            baseline_qty=0.0,
            config=None,
        )


# ------------------------------------------------------------------ #
#  单结算模式经 Protocol 的行为不变（M4 兼容面）
# ------------------------------------------------------------------ #

def test_single_mode_loads_sectioned_config_via_protocol():
    config = SINGLE_SETTLEMENT_MODE.load_config(SINGLE_YAML)
    assert config.market.market_name == "single_settlement"
    assert config.market.dt == 0.25
    assert config.scenario.scenario_count == 20
    assert config.solver.solver_name == "cbc"


def test_single_engine_report_via_protocol():
    engine: SettlementEngine = SINGLE_SETTLEMENT_MODE.settlement
    q_real = np.array([2.0])
    p_real = np.array([400.0])
    report = engine.build_settlement_report(
        q_real=q_real,
        p_real=p_real,
        q_long=np.array([1.0]),
        p_long=np.array([300.0]),
        p_ref=p_real,
        baseline_cost=900.0,
    )
    # 电能 800 + 差价 1×(300−400) = −100 → total 700
    assert report.energy_cost == pytest.approx(800.0)
    assert report.contract_difference == pytest.approx(-100.0)
    assert report.total_cost == pytest.approx(700.0)
    assert report.delta_cost == pytest.approx(200.0)
