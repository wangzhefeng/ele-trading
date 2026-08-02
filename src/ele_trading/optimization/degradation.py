"""退化模型组件（v4 P0 / §6.1.2）。

- Level 0（默认）：线性退化，``objectives.throughput_degradation_cost``
  （deg_cost × 吞吐量），不区分日历与循环机理。
- Level 1（本模块）：日历 + 循环分离，LP 可线性化：

  .. math::

      C_{calendar} = \\sum_t k_{cal} \\times \\frac{soc_t}{soc_{max}} \\times dt

      C_{cycle} = k_{cyc} \\times \\sum_t |\\Delta soc_t|

  日历项随平均 SOC 与时间增长（高 SOC 静置加速退化）；循环项随
  SOC 摆幅增长，|Δsoc| 用辅助变量线性化（退化成本为正，最小化
  目标下辅助变量自动贴紧）。

Level 2（温度耦合）仅设计不实现（v4 P2，见设计文档 §6.1.2）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pulp import LpAffineExpression, LpProblem, LpVariable, lpSum

from .bess_model import BESSVariables


@dataclass(frozen=True, slots=True)
class Level1Degradation:
    """Level 1 退化成本表达式及其两个可解释分量。"""

    expression: LpAffineExpression
    calendar_expression: LpAffineExpression
    cycle_expression: LpAffineExpression
    swing: dict[int, LpVariable]


def add_level1_degradation(
    model: LpProblem,
    variables: BESSVariables,
    steps,
    *,
    calendar_cost_per_hour: float,
    cycle_cost_per_mwh: float,
    soc0: float,
    soc_max: float,
    dt: float,
    prefix: str = "deg",
) -> Level1Degradation:
    """向模型追加 Level 1 退化成本（日历 + 循环分离）。

    参数：
        calendar_cost_per_hour: k_cal，满 SOC 静置每小时退化成本（¥/h）。
        cycle_cost_per_mwh: k_cyc，单位 SOC 摆幅退化成本（¥/MWh）。
        soc0 / soc_max: 初始 SOC 与 SOC 上限（与物理核一致）。
        dt: 时段时长（小时）。
    """
    if calendar_cost_per_hour < 0.0 or cycle_cost_per_mwh < 0.0:
        raise ValueError("degradation costs must be non-negative")
    if not np.isfinite(soc_max) or soc_max <= 0.0:
        raise ValueError("soc_max must be positive")
    if dt <= 0.0:
        raise ValueError("dt must be positive")

    step_list = list(steps)

    # 日历退化：k_cal × (soc_t / soc_max) × dt（纯线性，无需辅助变量）
    calendar_expression = lpSum(
        calendar_cost_per_hour * (variables.soc[step] / soc_max) * dt
        for step in step_list
    )

    # 循环退化：|Δsoc_t| 辅助变量线性化
    swing: dict[int, LpVariable] = {}
    previous = soc0
    for step in step_list:
        aux = LpVariable(f"{prefix}_swing_{step}", lowBound=0.0)
        model += aux >= variables.soc[step] - previous
        model += aux >= previous - variables.soc[step]
        swing[step] = aux
        previous = variables.soc[step]
    cycle_expression = lpSum(
        cycle_cost_per_mwh * swing[step] for step in step_list
    )

    return Level1Degradation(
        expression=calendar_expression + cycle_expression,
        calendar_expression=calendar_expression,
        cycle_expression=cycle_expression,
        swing=swing,
    )
