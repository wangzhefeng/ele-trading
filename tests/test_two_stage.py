from pyomo.environ import SolverFactory, value
from ele_trading.optimization.two_stage_cvar import build_two_stage_cvar_model


def test_two_stage_skeleton_can_build():
    model = build_two_stage_cvar_model(
        T=list(range(4)),
        OMEGA=['low', 'base', 'high'],
        p_omega={'low': 0.2, 'base': 0.5, 'high': 0.3},
        pi_da={t: 300.0 for t in range(4)},
        pi_rt={(t, w): {'low': 250.0, 'base': 300.0, 'high': 380.0}[w]
               for t in range(4) for w in ['low', 'base', 'high']},
        soc0=5.0,
        soc_min=1.0,
        soc_max=10.0,
        p_ch_max=3.0,
        p_dis_max=3.0,
        eta_ch=0.95,
        eta_dis=0.95,
        deg_cost=0.01,
        dt=1.0,
    )
    assert len(model.T) == 4
    assert len(model.OMEGA) == 3
    assert len(model.cvar_cons) == 3


def test_two_stage_is_solvable():
    T = list(range(4))
    OMEGA = ['low', 'base', 'high']
    p_omega = {'low': 0.2, 'base': 0.5, 'high': 0.3}
    pi_da = {t: 300.0 for t in T}
    pi_rt = {(t, w): {'low': 250.0, 'base': 300.0, 'high': 380.0}[w]
             for t in T for w in OMEGA}

    model = build_two_stage_cvar_model(
        T=T, OMEGA=OMEGA, p_omega=p_omega,
        pi_da=pi_da, pi_rt=pi_rt,
        soc0=5.0, soc_min=1.0, soc_max=10.0,
        p_ch_max=3.0, p_dis_max=3.0,
        eta_ch=0.95, eta_dis=0.95,
        deg_cost=0.01, dt=1.0,
        alpha=0.95, lam=0.1,
    )
    solver = SolverFactory('glpk')
    if not solver.available():
        solver = SolverFactory('cbc')
    result = solver.solve(model, tee=False)
    from pyomo.opt import TerminationCondition
    assert result.solver.termination_condition == TerminationCondition.optimal
    obj_val = value(model.obj)
    assert obj_val is not None
    assert obj_val > -1e9
