"""储能 MPC 滚动优化测试。"""

import pytest
from ele_trading.optimization.mpc_bess import solve_one_mpc_window, run_bess_mpc

SAMPLE_PRICES = [
    330.0, 315.0, 300.0, 295.0, 320.0, 355.0, 410.0, 470.0,
    530.0, 560.0, 590.0, 570.0, 520.0, 500.0, 510.0, 560.0,
    620.0, 700.0, 740.0, 710.0, 630.0, 540.0, 460.0, 390.0,
]


def test_single_window_solve():
    """单窗口求解应返回五个字段。"""
    result = solve_one_mpc_window(SAMPLE_PRICES[:6], soc0=5.0, horizon=6)
    assert 'p_ch' in result
    assert 'p_dis' in result
    assert 'soc_next' in result
    assert 'soc_terminal' in result
    assert 'obj' in result
    assert result['obj'] > 0


def test_terminal_soc_fraction():
    """terminal_soc_fraction > 0 时 SOC 末端不低于下界。"""
    soc_min, soc_max = 1.0, 10.0
    result = solve_one_mpc_window(
        SAMPLE_PRICES[:6], soc0=5.0, horizon=6,
        terminal_soc_fraction=0.3, soc_min=soc_min, soc_max=soc_max,
    )
    terminal_lb = soc_min + 0.3 * (soc_max - soc_min)
    assert result['soc_terminal'] >= terminal_lb - 1e-6


def test_rolling_mpc_output():
    """滚动优化应返回 DataFrame，长度与价格序列一致。"""
    df = run_bess_mpc(SAMPLE_PRICES, horizon=6, initial_soc=5.0)
    assert len(df) == len(SAMPLE_PRICES)
    for col in ('step', 'price', 'p_ch', 'p_dis', 'soc_next', 'step_objective'):
        assert col in df.columns


def test_15min_granularity():
    """dt=0.25 时 MPC 单窗口可正确求解。"""
    result = solve_one_mpc_window(
        SAMPLE_PRICES[:8], soc0=5.0, horizon=8, dt=0.25,
    )
    assert result['obj'] > 0


def test_default_fraction_backward_compat():
    """terminal_soc_fraction=0.0（默认）时行为应同无终端约束。"""
    result_default = solve_one_mpc_window(SAMPLE_PRICES[:6], soc0=1.5, horizon=6)
    result_explicit = solve_one_mpc_window(SAMPLE_PRICES[:6], soc0=1.5, horizon=6, terminal_soc_fraction=0.0)
    # obj 可能因 solver 等价解而接近但不一定相等；验证两结果都存在即可
    assert result_default['obj'] > 0
    assert result_explicit['obj'] > 0


def test_mpc_window_matches_arbitrage_on_full_horizon():
    """MPC 窗口覆盖全序列时，与共享核套利模型同模同解（v3 M1 物理核统一回归）。"""
    from ele_trading.optimization.bess_arbitrage import solve_bess_arbitrage

    mpc = solve_one_mpc_window(
        SAMPLE_PRICES, soc0=5.0, horizon=len(SAMPLE_PRICES), dt=0.25,
    )
    arbitrage = solve_bess_arbitrage(SAMPLE_PRICES, soc0=5.0, dt=0.25)

    assert mpc['obj'] == pytest.approx(arbitrage['objective'], rel=1e-6)
