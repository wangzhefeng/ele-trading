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

Level 2（温度耦合，v5 §11.2）：温度序列为外生输入，因此
``k_cal(T_t)`` / ``k_cyc(T_t)`` 可在建模前逐时段解析求值，保持 LP
结构不变（温度调制的 Level 1）。系数在参考温度处与 Level 1 一致，
高温放大、低温衰减并以 0 为下界。缺少温度数据时由
``select_degradation_level`` 显式回退 Level 1。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pulp import LpAffineExpression, LpProblem, LpVariable, lpSum

from .bess_model import BESSVariables


@dataclass(frozen=True, slots=True)
class TemperatureDegradationParameters:
    """Level 2 温度退化核参数（线性温度响应，参考温度处对齐 Level 1）。

    ``k(T) = k_ref × max(0, 1 + coeff × (T - T_ref))``；
    系数由离线退化核与厂商/实测数据校准，不在优化内联拟合。
    """

    calendar_cost_per_hour_ref: float
    cycle_cost_per_mwh_ref: float
    reference_temperature_c: float
    calendar_temperature_coeff: float
    cycle_temperature_coeff: float

    def __post_init__(self) -> None:
        for name in (
            "calendar_cost_per_hour_ref",
            "cycle_cost_per_mwh_ref",
            "calendar_temperature_coeff",
            "cycle_temperature_coeff",
        ):
            value = float(getattr(self, name))
            if not np.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not np.isfinite(float(self.reference_temperature_c)):
            raise ValueError("reference_temperature_c must be finite")


def select_degradation_level(
    *,
    requested: str,
    temperature_available: bool,
) -> str:
    """按数据可用性选择退化层级；Level 2 缺温度数据显式回退 Level 1。"""
    if requested not in ("level0", "level1", "level2"):
        raise ValueError(f"requested degradation level unknown: {requested!r}")
    if requested == "level2" and not temperature_available:
        return "level1"
    return requested


def add_level2_degradation(
    model: LpProblem,
    variables: BESSVariables,
    steps,
    *,
    temperature_c,
    parameters: TemperatureDegradationParameters,
    soc0: float,
    soc_max: float,
    dt: float,
    prefix: str = "deg2",
) -> Level1Degradation:
    """向模型追加 Level 2 温度调制退化成本（LP 结构同 Level 1）。

    温度是外生预测序列，逐时段系数在 Python 侧解析求值：
    - 日历项：k_cal(T_t) × (soc_t / soc_max) × dt；
    - 循环项：k_cyc(T_t) × |Δsoc_t|（辅助变量线性化，同 Level 1）。
    """
    if not isinstance(parameters, TemperatureDegradationParameters):
        raise ValueError("parameters must be TemperatureDegradationParameters")
    step_list = list(steps)
    temperature = np.asarray(temperature_c, dtype=float)
    if temperature.shape != (len(step_list),) or not np.isfinite(temperature).all():
        raise ValueError(
            "temperature_c must be a finite vector aligned with steps"
        )
    if not np.isfinite(soc_max) or soc_max <= 0.0:
        raise ValueError("soc_max must be positive")
    if dt <= 0.0:
        raise ValueError("dt must be positive")

    delta = temperature - parameters.reference_temperature_c
    k_cal = parameters.calendar_cost_per_hour_ref * np.maximum(
        0.0, 1.0 + parameters.calendar_temperature_coeff * delta
    )
    k_cyc = parameters.cycle_cost_per_mwh_ref * np.maximum(
        0.0, 1.0 + parameters.cycle_temperature_coeff * delta
    )

    calendar_expression = lpSum(
        float(k_cal[position])
        * (variables.soc[step] / soc_max)
        * dt
        for position, step in enumerate(step_list)
    )

    swing: dict[int, LpVariable] = {}
    previous = soc0
    for position, step in enumerate(step_list):
        aux = LpVariable(f"{prefix}_swing_{step}", lowBound=0.0)
        model += aux >= variables.soc[step] - previous
        model += aux >= previous - variables.soc[step]
        swing[step] = aux
        previous = variables.soc[step]
    cycle_expression = lpSum(
        float(k_cyc[position]) * swing[step]
        for position, step in enumerate(step_list)
    )

    return Level1Degradation(
        expression=calendar_expression + cycle_expression,
        calendar_expression=calendar_expression,
        cycle_expression=cycle_expression,
        swing=swing,
    )


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
