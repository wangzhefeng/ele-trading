"""确定性单市场储能套利模型。

把储能视为独立市场资产，在已知电价序列下最大化：
    放电卖电收入 - 充电买电成本 - 线性退化成本
不使用负荷预测，适合作独立储能套利基准和收益上限评估。
"""

from __future__ import annotations

from pulp import LpMaximize, LpProblem

from .bess_model import BESSConfig, add_bess_constraints
from .contracts import BESSArbitrageResult
from .extraction import extract_bess_values
from .objectives import arbitrage_net_revenue
from .solver import SolveStatus, solve_pulp_model


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

    参数：
        prices: 各时段电价序列。
        soc0 / soc_min / soc_max: 初始 SOC 与 SOC 上下限。
        p_ch_max / p_dis_max: 充放电功率上限。
        eta_ch / eta_dis: 充放电效率。
        deg_cost: 单位吞吐量线性退化成本。
        dt: 时段时长（小时）；15 分钟颗粒度取 0.25。
        enforce_terminal_soc: 是否强制末端 SOC 回到初值
            （避免规划期末把电量放空造成的虚高收益）。
    """
    T = range(len(prices))
    m = LpProblem('bess_arbitrage', LpMaximize)
    # 复用共享储能约束内核：SOC 动态、效率、功率上限、充放电互斥
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

    # 目标：sum_t [ 电价 * (放电 - 充电) * dt - 退化成本 * (充电 + 放电) * dt ]
    m += arbitrage_net_revenue(
        bess,
        T,
        prices,
        deg_cost_per_mwh=deg_cost,
        dt=dt,
    )

    # 统一求解出口（v3 M3）：非最优显式失败，不返回伪造结果
    result = solve_pulp_model(m)
    if result.status is not SolveStatus.OPTIMAL:
        raise RuntimeError(f"bess arbitrage failed: {result.message}")

    values = extract_bess_values(bess, T)
    return {
        'objective': result.objective_value,
        'p_ch': values["p_charge"],
        'p_dis': values["p_discharge"],
        'soc': values["soc"],
    }


def solve_bess_arbitrage_typed(**kwargs) -> BESSArbitrageResult:
    """typed 包装：与 solve_bess_arbitrage 同参，返回 BESSArbitrageResult。"""
    result = solve_bess_arbitrage(**kwargs)
    return BESSArbitrageResult(**result)
