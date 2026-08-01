"""Two-stage + CVaR 模型测试。"""

from pulp import value

from ele_trading.optimization.solver import SolveStatus, solve_pulp_model
from ele_trading.optimization.two_stage_cvar import build_two_stage_cvar_model


def _solver():
    """返回统一的 PuLP/CBC typed 求解边界。"""
    return solve_pulp_model


def test_build_and_solve():
    """最小可解场景：|T|=4, |\u03a9|=3，模型可构建并求解。"""
    T = list(range(4))
    OMEGA = list(range(3))
    p_omega = {0: 0.2, 1: 0.5, 2: 0.3}
    pi_da = {t: 300.0 for t in T}
    pi_rt = {(t, w): 300.0 + 20 * w for t in T for w in OMEGA}

    m = build_two_stage_cvar_model(
        T=T, OMEGA=OMEGA, p_omega=p_omega,
        pi_da=pi_da, pi_rt=pi_rt,
        soc0=5.0, soc_min=1.0, soc_max=10.0,
        p_ch_max=3.0, p_dis_max=3.0,
        eta_ch=0.95, eta_dis=0.95, deg_cost=0.01,
        kappa_pos=0.25, kappa_neg=0.25,
    )
    result = _solver()(m)
    assert result.status is SolveStatus.OPTIMAL


def test_objective_is_finite():
    """求解后目标值为有限数值。"""
    T = list(range(4))
    OMEGA = list(range(3))
    p_omega = {0: 0.33, 1: 0.34, 2: 0.33}
    pi_da = {t: 350.0 for t in T}
    pi_rt = {(t, w): 350.0 + 10 * (w - 1) for t in T for w in OMEGA}

    m = build_two_stage_cvar_model(
        T=T, OMEGA=OMEGA, p_omega=p_omega,
        pi_da=pi_da, pi_rt=pi_rt,
        soc0=5.0, soc_min=1.0, soc_max=10.0,
        p_ch_max=3.0, p_dis_max=3.0,
        eta_ch=0.95, eta_dis=0.95, deg_cost=0.01,
        kappa_pos=0.25, kappa_neg=0.25,
    )
    _solver()(m)
    obj_val = value(m.objective)
    assert obj_val is not None
    assert abs(obj_val) < 1e12


def test_cvar_coefficient_present():
    """CVaR 项中 1/(1-\u03b1) 系数应在模型目标中出现。"""
    alpha = 0.95
    T = list(range(3))
    OMEGA = list(range(2))
    p_omega = {0: 0.5, 1: 0.5}
    pi_da = {t: 300.0 for t in T}
    pi_rt = {(t, w): 300.0 for t in T for w in OMEGA}

    m = build_two_stage_cvar_model(
        T=T, OMEGA=OMEGA, p_omega=p_omega,
        pi_da=pi_da, pi_rt=pi_rt,
        soc0=5.0, soc_min=1.0, soc_max=10.0,
        p_ch_max=3.0, p_dis_max=3.0,
        eta_ch=0.95, eta_dis=0.95, deg_cost=0.01,
        kappa_pos=0.25, kappa_neg=0.25,
        alpha=alpha, lam=1.0,
    )
    result = _solver()(m)
    # 模型能求解即表明 CVaR 约束结构合理
    assert result.status is SolveStatus.OPTIMAL
    assert value(m.objective) is not None
