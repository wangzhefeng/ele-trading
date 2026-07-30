"""储能 MPC（模型预测控制）滚动优化。

在电价序列上滚动执行：每一步用未来 horizon 窗口的价格预测求解一个
有限时域套利子问题，只执行窗口第 1 时段的充放电决策，然后以新的
SOC 为初值向前滚动。
"""

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
    """求解单个 MPC 预测窗口的储能套利子问题。

    terminal_soc_fraction: 窗口末端 SOC 下界 = soc_min + fraction*(soc_max-soc_min)。
    0.0 表示不加终端约束（默认，向后兼容）。
    """
    T = range(horizon)
    m = LpProblem('bess_mpc_window', LpMaximize)

    # ---------------- 决策变量 ----------------
    # 充放电功率（连续）与充放电状态（0/1 互斥）
    p_ch = {t: LpVariable(f'p_ch_{t}', lowBound=0, upBound=p_ch_max) for t in T}
    p_dis = {t: LpVariable(f'p_dis_{t}', lowBound=0, upBound=p_dis_max) for t in T}
    soc = {t: LpVariable(f'soc_{t}', lowBound=soc_min, upBound=soc_max) for t in T}
    u_ch = {t: LpVariable(f'u_ch_{t}', cat=LpBinary) for t in T}
    u_dis = {t: LpVariable(f'u_dis_{t}', cat=LpBinary) for t in T}

    # ---------------- 约束 ----------------
    for t in T:
        # 同一时段充电与放电互斥，且功率受对应状态变量约束
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

    # ---------------- 目标：窗口内套利收益 - 退化成本 ----------------
    m += lpSum(
        prices_window[t] * (p_dis[t] - p_ch[t]) * dt - deg_cost * (p_ch[t] + p_dis[t]) * dt
        for t in T
    )

    m.solve(PULP_CBC_CMD(msg=False))
    check_pulp_status(m, "bess mpc window")

    return {
        'p_ch': value(p_ch[0]),                # 只取第 1 时段决策用于执行
        'p_dis': value(p_dis[0]),
        'soc_next': value(soc[0]),             # 执行后的 SOC，作为下一窗口初值
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
    """运行储能滚动优化，并输出逐步执行结果。

    每一步以当前 SOC 和未来 horizon 窗口价格求解子问题，
    只执行第 1 时段决策后向前滚动；序列尾部不足一个窗口时
    用最后一个价格重复填充，保证窗口长度一致。
    """
    records = []
    soc_now = initial_soc

    for step in range(len(prices)):
        # 取未来 horizon 窗口的价格预测；尾部不足时重复最后价格补齐
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
        # 滚动：以执行后的 SOC 作为下一步的初始状态
        soc_now = float(result['soc_next'])
    return pd.DataFrame(records)
