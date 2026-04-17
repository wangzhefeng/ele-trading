from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pyomo.environ import SolverFactory, value
from pyomo.opt import TerminationCondition
from ele_trading.optimization.two_stage_cvar import build_two_stage_cvar_model


if __name__ == '__main__':
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
    print('=== Two-stage + CVaR 求解结果 ===')
    print(f'求解状态: {result.solver.termination_condition}')
    if result.solver.termination_condition == TerminationCondition.optimal:
        print(f'目标函数值: {value(model.obj):.4f}')
        for t in T:
            print(f'  t={t}: q={value(model.q[t]):.3f}')
        for w in OMEGA:
            print(f'  场景 {w}: R={value(model.R[w]):.3f}')
    else:
        print('求解未达最优，请检查约束设置')
