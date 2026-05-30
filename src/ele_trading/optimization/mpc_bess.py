from __future__ import annotations

import pandas as pd
from pulp import LpBinary, LpMaximize, LpProblem, LpVariable, PULP_CBC_CMD, lpSum, value

from ele_trading.utils import check_pulp_status


def solve_one_mpc_window(
    prices_window,
    soc0,
    horizon,
    soc_min=1.0,
    soc_max=10.0,
    p_ch_max=3.0,
    p_dis_max=3.0,
    eta_ch=0.95,
    eta_dis=0.95,
    deg_cost=0.01,
    dt=1.0,
    terminal_soc_fraction: float = 0.0,
):
    """求解单个 MPC 窗口。

    terminal_soc_fraction: 窗口末端 SOC 下界 = soc_min + fraction*(soc_max-soc_min)。
    0.0 表示不加终端约束（默认，向后兼容）。
    """
    T = range(horizon)
    m = LpProblem('bess_mpc_window', LpMaximize)

    p_ch = {t: LpVariable(f'p_ch_{t}', lowBound=0, upBound=p_ch_max) for t in T}
    p_dis = {t: LpVariable(f'p_dis_{t}', lowBound=0, upBound=p_dis_max) for t in T}
    soc = {t: LpVariable(f'soc_{t}', lowBound=soc_min, upBound=soc_max) for t in T}
    u_ch = {t: LpVariable(f'u_ch_{t}', cat=LpBinary) for t in T}
    u_dis = {t: LpVariable(f'u_dis_{t}', cat=LpBinary) for t in T}

    for t in T:
        m += u_ch[t] + u_dis[t] <= 1
        m += p_ch[t] <= p_ch_max * u_ch[t]
        m += p_dis[t] <= p_dis_max * u_dis[t]

        # 窗口内仍需保持 SOC 动态一致，且首时段以当前真实 SOC 为初值。
        if t == 0:
            m += soc[t] == soc0 + eta_ch * p_ch[t] * dt - (p_dis[t] * dt) / eta_dis
        else:
            m += soc[t] == soc[t - 1] + eta_ch * p_ch[t] * dt - (p_dis[t] * dt) / eta_dis

    # 终端 SOC 下界约束：防止 MPC 在预测窗口末尾过度放电
    if terminal_soc_fraction > 0.0:
        terminal_lb = soc_min + terminal_soc_fraction * (soc_max - soc_min)
        m += soc[horizon - 1] >= terminal_lb

    m += lpSum(
        prices_window[t] * (p_dis[t] - p_ch[t]) * dt - deg_cost * (p_ch[t] + p_dis[t]) * dt
        for t in T
    )

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "bess mpc window")

    return {
        'p_ch': value(p_ch[0]),
        'p_dis': value(p_dis[0]),
        'soc_next': value(soc[0]),
        'soc_terminal': value(soc[horizon - 1]),
        'obj': value(m.objective),
    }


def run_bess_mpc(
    prices: list[float],
    horizon: int,
    initial_soc: float,
    soc_min=1.0,
    soc_max=10.0,
    p_ch_max=3.0,
    p_dis_max=3.0,
    eta_ch=0.95,
    eta_dis=0.95,
    deg_cost=0.01,
    dt=1.0,
    terminal_soc_fraction: float = 0.0,
) -> pd.DataFrame:
    """运行储能滚动优化，并输出逐步执行结果。"""
    records = []
    soc_now = initial_soc

    for step in range(len(prices)):
        window = prices[step: step + horizon]
        if len(window) < horizon:
            window = window + [window[-1]] * (horizon - len(window))
        result = solve_one_mpc_window(
            prices_window=window,
            soc0=soc_now,
            horizon=horizon,
            soc_min=soc_min,
            soc_max=soc_max,
            p_ch_max=p_ch_max,
            p_dis_max=p_dis_max,
            eta_ch=eta_ch,
            eta_dis=eta_dis,
            deg_cost=deg_cost,
            dt=dt,
            terminal_soc_fraction=terminal_soc_fraction,
        )
        records.append(
            {
                'step': step,
                'price': float(prices[step]),
                'p_ch': float(result['p_ch']),
                'p_dis': float(result['p_dis']),
                'soc_next': float(result['soc_next']),
                'step_objective': float(result['obj']),
            }
        )
        soc_now = float(result['soc_next'])
    return pd.DataFrame(records)
