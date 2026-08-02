"""CVaR 辅助变量与风险调整目标 helper。

实现 Rockafellar-Uryasev 上尾 CVaR 的线性化：
    excess_s >= loss_s - VaR,  excess_s >= 0
    CVaR = VaR + 1 / (1 - alpha) * sum_s p_s * excess_s
另提供独立于 LP 的离散加权 VaR/CVaR 评估，可与优化结果交叉验证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
from pulp import LpAffineExpression, LpProblem, LpVariable, lpSum
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp

@dataclass(frozen=True, slots=True)
class CVaRAuxiliaries:
    """Rockafellar-Uryasev 线性化引入的 VaR 阈值、超额损失与 CVaR 表达式。"""

    var: LpVariable                       # VaR 阈值变量（自由变量）
    excess: dict[str, LpVariable]         # 各场景超额损失变量（>= 0）
    expression: LpAffineExpression        # CVaR 线性表达式，可直接加入目标


@dataclass(frozen=True, slots=True)
class WorstCaseAuxiliaries:
    """最坏场景损失的 epigraph 线性化。"""

    expression: LpVariable


@dataclass(frozen=True, slots=True)
class ChanceConstraintAuxiliaries:
    """离散场景机会约束引入的违约指示变量。"""

    violated: dict[str, LpVariable]
    probability_expression: LpAffineExpression


def _validate_probabilities(
    losses: Mapping[str, object],
    probabilities: Mapping[str, float],
) -> dict[str, float]:
    """校验场景概率：场景集合一致、取值有限且为正、总和为 1。"""
    if not losses:
        raise ValueError("losses must not be empty")
    if set(losses) != set(probabilities):
        raise ValueError("probabilities must match loss scenario IDs")
    normalized = {
        scenario_id: float(probability)
        for scenario_id, probability in probabilities.items()
    }
    if any(
        not np.isfinite(probability) or probability <= 0.0
        for probability in normalized.values()
    ):
        raise ValueError(
            "scenario probabilities must be finite and positive"
        )
    if not np.isclose(
        sum(normalized.values()),
        1.0,
        rtol=0.0,
        atol=1e-9,
    ):
        raise ValueError("scenario probabilities must sum to 1")
    return normalized


def add_cvar_auxiliaries(
    model: LpProblem,
    losses: Mapping[str, object],
    probabilities: Mapping[str, float],
    *,
    alpha: float,
    prefix: str = "cvar",
) -> CVaRAuxiliaries:
    """为最小化模型加入加权上尾 CVaR 的线性化辅助变量与约束。

    参数：
        model: 目标 PuLP 模型（最小化问题）。
        losses: 各场景的损失表达式（LpAffineExpression），键为场景 ID。
        probabilities: 各场景概率，必须与 losses 键一致且总和为 1。
        alpha: 置信水平，取值 (0, 1)，常用 0.95。
        prefix: 变量名前缀，避免与其他约束块重名。
    """
    if not isinstance(model, LpProblem):
        raise ValueError("model must be a PuLP LpProblem")
    if not np.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be within (0, 1)")
    probabilities = _validate_probabilities(losses, probabilities)
    # VaR 阈值：自由变量，可正可负
    var = LpVariable(f"{prefix}_var", lowBound=None, upBound=None)
    # 各场景超额损失变量；变量名用序号而非场景 ID，避免 ID 中非法字符
    excess = {
        scenario_id: LpVariable(
            f"{prefix}_excess_{position}",
            lowBound=0.0,
        )
        for position, scenario_id in enumerate(losses)
    }
    # 核心线性化约束：excess_s >= loss_s - VaR
    for position, (scenario_id, loss) in enumerate(losses.items()):
        model += (
            excess[scenario_id] >= loss - var,
            f"{prefix}_tail_{position}",
        )
    # CVaR = VaR + 1/(1-alpha) * E[(loss - VaR)+]
    expression = var + (
        1.0 / (1.0 - alpha)
    ) * lpSum(
        probabilities[scenario_id] * excess[scenario_id]
        for scenario_id in losses
    )
    return CVaRAuxiliaries(
        var=var,
        excess=excess,
        expression=expression,
    )


def risk_adjusted_objective(
    expected_cost,
    cvar,
    *,
    risk_weight: float,
):
    """组合期望成本与 CVaR 为风险调整目标，不改变两者量纲。

    目标 = 期望成本 + risk_weight * CVaR；
    risk_weight = 0 退化为纯期望成本最小化。
    """
    if not np.isfinite(risk_weight) or risk_weight < 0.0:
        raise ValueError("risk_weight must be finite and non-negative")
    return expected_cost + float(risk_weight) * cvar


def weighted_var_cvar(
    losses: Mapping[str, float],
    probabilities: Mapping[str, float],
    *,
    alpha: float,
) -> tuple[float, float]:
    """离散加权 VaR/CVaR 的直接评估，独立于 LP 辅助变量。

    按损失升序累积概率，首个累积概率达到 alpha 的损失即 VaR；
    CVaR = VaR + E[(loss - VaR)+] / (1 - alpha)。
    用于对优化求解结果做事后交叉验证。
    """
    if not np.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be within (0, 1)")
    probabilities = _validate_probabilities(losses, probabilities)
    numeric_losses = {
        scenario_id: float(loss)
        for scenario_id, loss in losses.items()
    }
    if any(
        not np.isfinite(loss)
        for loss in numeric_losses.values()
    ):
        raise ValueError("losses must be finite")
    # 按损失升序排列场景
    ordered = sorted(
        numeric_losses,
        key=lambda scenario_id: numeric_losses[scenario_id],
    )
    # 累积概率首次达到 alpha 处的损失即 VaR；若概率舍入未触发则取最大损失
    cumulative = 0.0
    var = numeric_losses[ordered[-1]]
    for scenario_id in ordered:
        cumulative += probabilities[scenario_id]
        if cumulative >= alpha - 1e-12:
            var = numeric_losses[scenario_id]
            break
    cvar = var + sum(
        probabilities[scenario_id]
        * max(numeric_losses[scenario_id] - var, 0.0)
        for scenario_id in numeric_losses
    ) / (1.0 - alpha)
    return float(var), float(cvar)


def weighted_worst_case(losses: Mapping[str, float]) -> float:
    """返回有限离散损失的最坏值。"""
    if not losses:
        raise ValueError("losses must not be empty")
    values = np.asarray([float(loss) for loss in losses.values()], dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("losses must be finite")
    return float(values.max())


def weighted_top_tail_mean(
    losses: Mapping[str, float],
    probabilities: Mapping[str, float],
    *,
    tail_mass: float,
) -> float:
    """计算概率质量恰为 ``tail_mass`` 的最坏上尾均值。"""
    if not np.isfinite(tail_mass) or not 0.0 < tail_mass <= 1.0:
        raise ValueError("tail_mass must be within (0, 1]")
    probabilities = _validate_probabilities(losses, probabilities)
    numeric_losses = {
        scenario_id: float(loss) for scenario_id, loss in losses.items()
    }
    if any(not np.isfinite(loss) for loss in numeric_losses.values()):
        raise ValueError("losses must be finite")
    remaining = float(tail_mass)
    weighted_sum = 0.0
    for scenario_id in sorted(
        numeric_losses,
        key=lambda item: numeric_losses[item],
        reverse=True,
    ):
        consumed = min(remaining, probabilities[scenario_id])
        weighted_sum += consumed * numeric_losses[scenario_id]
        remaining -= consumed
        if remaining <= 1e-12:
            break
    return float(weighted_sum / tail_mass)


def entropic_value_at_risk(
    losses: Mapping[str, float],
    probabilities: Mapping[str, float],
    *,
    alpha: float,
) -> float:
    """通过一维凸外层最小化评估离散上尾 EVaR。

    该函数只做数值评估；若要把 EVaR 放入优化目标，需要指数锥求解器
    或经验证的外层近似，不能直接塞入当前 PuLP/CBC 线性模型。
    """
    if not np.isfinite(alpha) or not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be within (0, 1)")
    probabilities = _validate_probabilities(losses, probabilities)
    values = np.asarray([float(losses[item]) for item in losses], dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("losses must be finite")
    log_probabilities = np.log(
        np.asarray([probabilities[item] for item in losses], dtype=float)
    )
    log_tail_probability = np.log(1.0 - alpha)

    def objective(log_theta: float) -> float:
        theta = float(np.exp(log_theta))
        return float(
            (
                logsumexp(log_probabilities + theta * values)
                - log_tail_probability
            )
            / theta
        )

    result = minimize_scalar(
        objective,
        bounds=(-12.0, 8.0),
        method="bounded",
        options={"xatol": 1e-10},
    )
    if not result.success or not np.isfinite(result.fun):
        raise RuntimeError("EVaR outer minimization failed")
    return float(min(result.fun, values.max()))


def chance_violation_probability(
    violations: Mapping[str, float],
    probabilities: Mapping[str, float],
    *,
    tolerance: float = 0.0,
) -> float:
    """返回 ``violation > tolerance`` 的离散概率质量。"""
    if not np.isfinite(tolerance):
        raise ValueError("tolerance must be finite")
    probabilities = _validate_probabilities(violations, probabilities)
    numeric = {
        scenario_id: float(value)
        for scenario_id, value in violations.items()
    }
    if any(not np.isfinite(value) for value in numeric.values()):
        raise ValueError("violations must be finite")
    return float(
        sum(
            probabilities[scenario_id]
            for scenario_id, value in numeric.items()
            if value > tolerance
        )
    )


def add_worst_case_auxiliary(
    model: LpProblem,
    losses: Mapping[str, object],
    *,
    prefix: str = "worst_case",
) -> WorstCaseAuxiliaries:
    """加入 ``worst >= loss_s`` epigraph 约束。"""
    if not isinstance(model, LpProblem):
        raise ValueError("model must be a PuLP LpProblem")
    if not losses:
        raise ValueError("losses must not be empty")
    worst = LpVariable(f"{prefix}_loss", lowBound=None, upBound=None)
    for position, loss in enumerate(losses.values()):
        model += worst >= loss, f"{prefix}_bound_{position}"
    return WorstCaseAuxiliaries(expression=worst)


def add_chance_constraint(
    model: LpProblem,
    violations: Mapping[str, object],
    probabilities: Mapping[str, float],
    *,
    max_violation_probability: float,
    big_m: float,
    prefix: str = "chance",
) -> ChanceConstraintAuxiliaries:
    """用场景二元变量加入离散机会约束。

    调用方必须从物理边界推导 ``big_m``；该函数拒绝无限或非正值，
    但无法替调用方证明 M 的紧致性。
    """
    if not isinstance(model, LpProblem):
        raise ValueError("model must be a PuLP LpProblem")
    if (
        not np.isfinite(max_violation_probability)
        or not 0.0 <= max_violation_probability <= 1.0
    ):
        raise ValueError("max_violation_probability must be within [0, 1]")
    if not np.isfinite(big_m) or big_m <= 0.0:
        raise ValueError("big_m must be finite and positive")
    probabilities = _validate_probabilities(violations, probabilities)
    violated = {
        scenario_id: LpVariable(
            f"{prefix}_violated_{position}",
            cat="Binary",
        )
        for position, scenario_id in enumerate(violations)
    }
    for position, (scenario_id, expression) in enumerate(violations.items()):
        model += (
            expression <= float(big_m) * violated[scenario_id],
            f"{prefix}_link_{position}",
        )
    probability_expression = lpSum(
        probabilities[scenario_id] * violated[scenario_id]
        for scenario_id in violations
    )
    model += (
        probability_expression <= float(max_violation_probability),
        f"{prefix}_probability",
    )
    return ChanceConstraintAuxiliaries(
        violated=violated,
        probability_expression=probability_expression,
    )
