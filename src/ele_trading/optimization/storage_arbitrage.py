from __future__ import annotations

from pulp import LpBinary, LpMaximize, LpProblem, LpVariable, COIN_CMD, lpSum, value

from .interfaces import StorageArbitrageResult


def solve_storage_arbitrage(
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
    m = LpProblem('storage_arbitrage', LpMaximize)

    p_ch = {t: LpVariable(f'p_ch_{t}', lowBound=0, upBound=p_ch_max) for t in T}
    p_dis = {t: LpVariable(f'p_dis_{t}', lowBound=0, upBound=p_dis_max) for t in T}
    soc = {t: LpVariable(f'soc_{t}', lowBound=soc_min, upBound=soc_max) for t in T}
    u_ch = {t: LpVariable(f'u_ch_{t}', cat=LpBinary) for t in T}
    u_dis = {t: LpVariable(f'u_dis_{t}', cat=LpBinary) for t in T}

    for t in T:
        # 互斥约束：同一时刻不能同时充电和放电。
        m += u_ch[t] + u_dis[t] <= 1
        m += p_ch[t] <= p_ch_max * u_ch[t]
        m += p_dis[t] <= p_dis_max * u_dis[t]

        # SOC 递推体现储能跨时段耦合，是储能优化的核心动态约束。
        if t == 0:
            m += soc[t] == soc0 + eta_ch * p_ch[t] * dt - (p_dis[t] * dt) / eta_dis
        else:
            m += soc[t] == soc[t - 1] + eta_ch * p_ch[t] * dt - (p_dis[t] * dt) / eta_dis

    if enforce_terminal_soc:
        # 若要求日终回到初始 SOC，可避免“透支未来”带来的不公平套利。
        m += soc[len(prices) - 1] == soc0

    m += lpSum(
        prices[t] * (p_dis[t] - p_ch[t]) * dt - deg_cost * (p_ch[t] + p_dis[t]) * dt
        for t in T
    )

    m.solve(COIN_CMD(msg=False))

    return {
        'objective': value(m.objective),
        'p_ch': [value(p_ch[t]) for t in T],
        'p_dis': [value(p_dis[t]) for t in T],
        'soc': [value(soc[t]) for t in T],
    }


def solve_storage_arbitrage_typed(**kwargs) -> StorageArbitrageResult:
    result = solve_storage_arbitrage(**kwargs)
    return StorageArbitrageResult(**result)
