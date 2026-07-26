from __future__ import annotations

from pulp import LpMaximize, LpProblem, PULP_CBC_CMD, lpSum, value

from ele_trading.utils import check_pulp_status
from .bess_model import BESSConfig, add_bess_constraints
from .contracts import BESSArbitrageResult


def solve_bess_arbitrage(
    prices,
    soc0=5.0,
    soc_min=1.0,
    soc_max=10.0,
    p_ch_max=3.0,
    p_dis_max=3.0,
    eta_ch=0.95,
    eta_dis=0.95,
    deg_cost=0.01,
    dt=1.0,
    enforce_terminal_soc=False,
):
    """求解单市场储能套利问题。

    这是文档 23.5 的工程化包装版本：
    - 决策变量包括充电、放电、SOC 与互斥状态。
    - 目标是最大化电价套利收益减去退化成本。
    """
    T = range(len(prices))
    m = LpProblem('bess_arbitrage', LpMaximize)
    bess = add_bess_constraints(
        m,
        T,
        BESSConfig(
            soc0=soc0,
            soc_min=soc_min,
            soc_max=soc_max,
            p_ch_max=p_ch_max,
            p_dis_max=p_dis_max,
            eta_ch=eta_ch,
            eta_dis=eta_dis,
            dt=dt,
            terminal_soc=soc0 if enforce_terminal_soc else None,
        ),
        prefix="arbitrage",
    )

    m += lpSum(
        prices[t]
        * (bess.p_discharge[t] - bess.p_charge[t])
        * dt
        - deg_cost
        * (bess.p_charge[t] + bess.p_discharge[t])
        * dt
        for t in T
    )

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "bess arbitrage")

    return {
        'objective': value(m.objective),
        'p_ch': [value(bess.p_charge[t]) for t in T],
        'p_dis': [value(bess.p_discharge[t]) for t in T],
        'soc': [value(bess.soc[t]) for t in T],
    }


def solve_bess_arbitrage_typed(**kwargs) -> BESSArbitrageResult:
    result = solve_bess_arbitrage(**kwargs)
    return BESSArbitrageResult(**result)
