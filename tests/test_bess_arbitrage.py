"""储能单市场套利优化测试。"""

import pytest
from ele_trading.optimization.bess_arbitrage import solve_bess_arbitrage, solve_bess_arbitrage_typed

SAMPLE_PRICES = [
    320.0, 300.0, 285.0, 270.0, 260.0, 255.0, 280.0, 340.0,
    420.0, 510.0, 560.0, 540.0, 500.0, 470.0, 450.0, 460.0,
    520.0, 610.0, 690.0, 720.0, 680.0, 540.0, 430.0, 360.0,
]


def test_basic_solve():
    """基本求解：应返回四个字段且目标值 > 0。"""
    result = solve_bess_arbitrage(SAMPLE_PRICES)
    assert 'objective' in result
    assert 'p_ch' in result
    assert 'p_dis' in result
    assert 'soc' in result
    assert result['objective'] > 0
    assert len(result['p_ch']) == 24
    assert len(result['p_dis']) == 24
    assert len(result['soc']) == 24


def test_enforce_terminal_soc():
    """enforce_terminal_soc=True 时日终 SOC 应回到初值。"""
    result = solve_bess_arbitrage(SAMPLE_PRICES, soc0=5.0, enforce_terminal_soc=True)
    assert result['objective'] > 0
    assert abs(result['soc'][-1] - 5.0) < 1e-6


def test_no_terminal_soc():
    """不强制终端 SOC 时，SOC 应自然演化。"""
    result = solve_bess_arbitrage(SAMPLE_PRICES, soc0=5.0, enforce_terminal_soc=False)
    assert result['objective'] > 0


def test_15min_granularity():
    """dt=0.25 时应可正确求解。"""
    result = solve_bess_arbitrage(SAMPLE_PRICES[:8], dt=0.25)
    assert result['objective'] > 0


def test_typed_interface():
    """typed 包装器应返回 BESSArbitrageResult。"""
    result = solve_bess_arbitrage_typed(prices=SAMPLE_PRICES)
    assert hasattr(result, 'objective')
    assert hasattr(result, 'p_ch')
    assert hasattr(result, 'p_dis')
    assert hasattr(result, 'soc')
    assert len(result.p_ch) == 24
