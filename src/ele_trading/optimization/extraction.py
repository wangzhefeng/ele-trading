"""求解结果统一提取（v3 M3）。

把共享物理核变量集合提取为数值序列的唯一实现，
供日前、Two-stage、MPC 与套利模型复用；只在求解状态为
最优/可行后调用（状态判断归 ``solver.solve_pulp_model``）。
"""

from __future__ import annotations

from typing import cast

from pulp import value

from .bess_model import BESSVariables


def _solved_value(variable: object) -> float:
    """读取已求解变量的取值；未求解（None）时显式报错。"""
    result = value(variable)
    if result is None:
        raise RuntimeError(
            "extract_bess_values requires an optimally solved model"
        )
    return float(cast(float, result))


def extract_bess_values(
    variables: BESSVariables,
    steps,
) -> dict[str, list[float]]:
    """提取 BESSVariables 的数值序列：p_charge / p_discharge / soc。"""
    return {
        "p_charge": [_solved_value(variables.p_charge[step]) for step in steps],
        "p_discharge": [
            _solved_value(variables.p_discharge[step]) for step in steps
        ],
        "soc": [_solved_value(variables.soc[step]) for step in steps],
    }
