"""目标函数组件库（v3 M3 目标策略层）。

各优化模型共享的目标组件：净负荷购电成本、线性退化成本、套利净收益。
组件只依赖物理变量与显式注入的参数，不感知市场规则语义
（市场规则参数由上层经配置注入，v3 不变量 3）。
"""

from __future__ import annotations

from pulp import lpSum

from .bess_model import BESSVariables


def net_load_energy_cost(
    variables: BESSVariables,
    steps,
    load,
    price,
    *,
    dt: float,
):
    """净负荷购电成本：Σ (load_t + (p_ch − p_dis)·dt) × price_t。"""
    return lpSum(
        (
            load[step]
            + (variables.p_charge[step] - variables.p_discharge[step]) * dt
        )
        * price[step]
        for step in steps
    )


def throughput_degradation_cost(
    variables: BESSVariables,
    steps,
    *,
    deg_cost_per_mwh: float,
    dt: float,
):
    """线性退化成本：deg_cost × Σ (p_ch + p_dis) × dt。"""
    return lpSum(
        deg_cost_per_mwh
        * (variables.p_charge[step] + variables.p_discharge[step])
        * dt
        for step in steps
    )


def arbitrage_net_revenue(
    variables: BESSVariables,
    steps,
    prices,
    *,
    deg_cost_per_mwh: float,
    dt: float,
):
    """套利净收益（最大化目标）：Σ [price×(p_dis−p_ch)×dt − 退化成本]。"""
    return lpSum(
        prices[step]
        * (variables.p_discharge[step] - variables.p_charge[step])
        * dt
        - deg_cost_per_mwh
        * (variables.p_charge[step] + variables.p_discharge[step])
        * dt
        for step in steps
    )
