"""储能 MPC（模型预测控制）滚动优化。

在电价序列上滚动执行：每一步用未来 horizon 窗口的价格预测求解一个
有限时域套利子问题，只执行窗口第 1 时段的充放电决策，然后以新的
SOC 为初值向前滚动。

v3 M1（D-004）：SOC 动态、效率、功率与充放互斥约束统一复用
``bess_model.add_bess_constraints`` 共享物理核，本模块只保留
MPC 特有的终端 SOC 下界与窗口目标；``dt`` 口径与共享核一致
（小时为单位的时段时长，15 分钟主链为 0.25）。
"""

from __future__ import annotations

import pandas as pd
from pulp import LpMaximize, LpProblem

from .bess_model import BESSConfig, add_bess_constraints
from .extraction import extract_bess_values
from .objectives import arbitrage_net_revenue
from .solver import SolveStatus, solve_pulp_model


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
    dt=0.25,
    terminal_soc_fraction: float = 0.0,
):
    """求解单个 MPC 预测窗口的储能套利子问题。

    dt: 时段时长（小时），与共享核口径一致；15 分钟颗粒度为 0.25。
    terminal_soc_fraction: 窗口末端 SOC 下界 = soc_min + fraction*(soc_max-soc_min)。
    0.0 表示不加终端约束（默认，向后兼容）。
    """
    T = range(horizon)
    m = LpProblem('bess_mpc_window', LpMaximize)

    # ---------------- 共享物理核：SOC 动态、效率、功率上限、充放互斥 ----------------
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
        ),
        prefix="mpc",
    )

    # ---------------- MPC 特有约束：窗口末端 SOC 下界 ----------------
    # 防止 MPC 在预测窗口末尾过度放电
    if terminal_soc_fraction > 0.0:
        terminal_lb = soc_min + terminal_soc_fraction * (soc_max - soc_min)
        m += (
            bess.soc[horizon - 1] >= terminal_lb,
            "mpc_terminal_soc_lower_bound",
        )

    # ---------------- 目标：窗口内套利收益 - 退化成本 ----------------
    m += arbitrage_net_revenue(
        bess,
        T,
        prices_window,
        deg_cost_per_mwh=deg_cost,
        dt=dt,
    )

    # 统一求解出口（v3 M3）：非最优显式失败，不返回伪造结果
    result = solve_pulp_model(m)
    if result.status is not SolveStatus.OPTIMAL:
        raise RuntimeError(f"bess mpc window failed: {result.message}")

    values = extract_bess_values(bess, T)
    return {
        'p_ch': values["p_charge"][0],           # 只取第 1 时段决策用于执行
        'p_dis': values["p_discharge"][0],
        'soc_next': values["soc"][0],            # 执行后的 SOC，作为下一窗口初值
        'soc_terminal': values["soc"][horizon - 1],
        'obj': result.objective_value,
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
    dt=0.25,
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
