from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from pulp import value

from ele_trading.optimization.solver import SolveStatus, solve_pulp_model
from ele_trading.optimization.two_stage_cvar import build_two_stage_cvar_model
from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.contracts import MarketConfig


MENGXI_YAML = PROJECT_ROOT / 'configs' / 'market_mengxi.yaml'


def build_demo_model(market_config: MarketConfig):
    """Build the demo model with market-owned scenario cost proxies."""
    T = list(range(4))
    OMEGA = ['low', 'base', 'high']
    p_omega = {'low': 0.2, 'base': 0.5, 'high': 0.3}
    pi_da = {t: 300.0 for t in T}
    pi_rt = {
        (t, w): {'low': 250.0, 'base': 300.0, 'high': 380.0}[w]
        for t in T for w in OMEGA
    }
    return build_two_stage_cvar_model(
        T=T, OMEGA=OMEGA, p_omega=p_omega,
        pi_da=pi_da, pi_rt=pi_rt,
        soc0=5.0, soc_min=1.0, soc_max=10.0,
        p_ch_max=3.0, p_dis_max=3.0,
        eta_ch=0.95, eta_dis=0.95,
        deg_cost=0.01,
        kappa_pos=(
            market_config.two_stage_scenario_deviation_cost_positive
        ),
        kappa_neg=(
            market_config.two_stage_scenario_deviation_cost_negative
        ),
        dt=1.0,
        alpha=0.95, lam=0.1,
    )


if __name__ == '__main__':
    config = load_market_config(MENGXI_YAML)
    model = build_demo_model(config)
    result = solve_pulp_model(model)

    print('=== Two-stage + CVaR 求解结果 ===')
    print(f'求解状态: {result.status.value}')
    if result.status is SolveStatus.OPTIMAL:
        print(f'目标函数值: {value(model.objective):.4f}')
        bid_variables = sorted(
            (
                variable
                for variable in model.variables()
                if variable.name.startswith('first_stage_bid_')
            ),
            key=lambda variable: variable.name,
        )
        for t, variable in enumerate(bid_variables):
            print(f'  t={t}: q={value(variable):.3f}')
    else:
        print('求解未达最优，请检查约束设置')
