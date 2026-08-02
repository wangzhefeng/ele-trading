"""场景集质量诊断（v4 P0 / §5.3）。

对活动场景集执行五项诊断：

1. 权重守恒：Σ p_s = 1（契约已强制，此处显式复核）；
2. 边际一致性：场景加权均值/分位相对点预测的漂移有界；
3. 相关保持：场景内目标间相关性与历史参考一致（需历史参考）；
4. 极端覆盖：场景对历史极端水平的覆盖比例达标（需历史参考）；
5. 复现性：同 seed + 同输入版本应产生相同场景集（由
   ``assert_reproducible`` 以重跑构建器的方式验证）。

诊断不修改场景集，只报告；``passed=False`` 的语义是"该场景集不应
进入下游优化"（v3 不变量 6：失败显式）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from .contracts import ScenarioSet


@dataclass(frozen=True, slots=True)
class DiagnosticCheck:
    """单项诊断结果。"""

    name: str
    passed: bool
    value: float | None
    detail: str


@dataclass(frozen=True, slots=True)
class ScenarioDiagnostics:
    """场景集诊断报告。"""

    checks: tuple[DiagnosticCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[str, ...]:
        return tuple(
            check.name for check in self.checks if not check.passed
        )


def _weighted_mean(scenario_set: ScenarioSet, target: str) -> np.ndarray:
    matrix = np.column_stack(
        [
            scenario.trajectories[target].to_numpy(dtype=float)
            for scenario in scenario_set.scenarios
        ]
    )
    weights = np.array(
        [scenario.probability for scenario in scenario_set.scenarios]
    )
    return matrix @ weights


def _weighted_quantile(
    values: np.ndarray, weights: np.ndarray, tau: float
) -> float:
    order = np.argsort(values)
    sorted_values = values[order]
    cumulative = np.cumsum(weights[order])
    # 归一化到总权重 1（调用方传入的权重可含重复展开，总和不一定是 1）
    cumulative = cumulative / cumulative[-1]
    index = int(np.searchsorted(cumulative, tau, side="left"))
    return float(sorted_values[min(index, len(sorted_values) - 1)])


def diagnose_scenario_set(
    scenario_set: ScenarioSet,
    *,
    reference: Mapping[str, pd.Series],
    historical: pd.DataFrame | None = None,
    mean_tol_ratio: float = 0.05,
    quantile_tol_ratio: float = 0.10,
    quantiles: tuple[float, ...] = (0.1, 0.5, 0.9),
    corr_tol: float = 0.2,
    min_extreme_ratio: float = 0.005,
    extreme_quantile: float = 0.95,
) -> ScenarioDiagnostics:
    """对场景集执行五项诊断。

    reference：各目标的点预测（场景生成中心），用于边际一致性。
    historical：历史参考 DataFrame（列名与目标一致），用于相关保持
    与极端覆盖；缺省时这两项标记 skipped（不判失败但显式记录）。
    """
    checks: list[DiagnosticCheck] = []
    scenarios = scenario_set.scenarios
    weights = np.array([s.probability for s in scenarios])

    # ---- 1. 权重守恒 ----
    weight_sum = float(weights.sum())
    checks.append(
        DiagnosticCheck(
            name="weight_conservation",
            passed=bool(np.isclose(weight_sum, 1.0, rtol=0.0, atol=1e-6)),
            value=weight_sum,
            detail=f"Σ probability = {weight_sum:.12f}",
        )
    )

    # ---- 2. 边际一致性 ----
    worst_mean_ratio = 0.0
    worst_quantile_ratio = 0.0
    for target in scenario_set.units:
        if target not in reference:
            raise ValueError(
                f"reference must cover every scenario target, missing {target!r}"
            )
        ref = reference[target].to_numpy(dtype=float)
        if len(ref) != scenario_set.horizon:
            raise ValueError(
                f"reference[{target!r}] length must match scenario horizon"
            )
        mean_scenario = _weighted_mean(scenario_set, target)
        scale = float(np.mean(np.abs(ref)))
        if scale <= 0.0:
            continue
        worst_mean_ratio = max(
            worst_mean_ratio,
            float(np.max(np.abs(mean_scenario - ref))) / scale,
        )
        # 分位漂移：跨时段合并的场景加权分位 vs 点预测分位。
        # 归一化尺度用 mean|ref|（与均值检查一致；std 对近常数
        # 参考序列病态敏感）
        pooled = np.concatenate(
            [
                s.trajectories[target].to_numpy(dtype=float)
                for s in scenarios
            ]
        )
        pooled_weights = np.repeat(weights, scenario_set.horizon)
        for tau in quantiles:
            scenario_q = _weighted_quantile(pooled, pooled_weights, tau)
            ref_q = float(np.quantile(ref, tau))
            worst_quantile_ratio = max(
                worst_quantile_ratio,
                abs(scenario_q - ref_q) / scale,
            )
    checks.append(
        DiagnosticCheck(
            name="marginal_mean_consistency",
            passed=worst_mean_ratio <= mean_tol_ratio,
            value=worst_mean_ratio,
            detail=(
                f"max |mean(scenario)−point| / mean|point| "
                f"= {worst_mean_ratio:.4f} (tol {mean_tol_ratio})"
            ),
        )
    )
    checks.append(
        DiagnosticCheck(
            name="marginal_quantile_consistency",
            passed=worst_quantile_ratio <= quantile_tol_ratio,
            value=worst_quantile_ratio,
            detail=(
                f"max |quantile(scenario)−quantile(point)| / mean|point| "
                f"= {worst_quantile_ratio:.4f} (tol {quantile_tol_ratio})"
            ),
        )
    )

    # ---- 3/4. 相关保持与极端覆盖（需要历史参考） ----
    if historical is None:
        checks.append(
            DiagnosticCheck(
                name="correlation_preservation",
                passed=True,
                value=None,
                detail="skipped: no historical reference",
            )
        )
        checks.append(
            DiagnosticCheck(
                name="extreme_coverage",
                passed=True,
                value=None,
                detail="skipped: no historical reference",
            )
        )
    else:
        targets = [
            t for t in scenario_set.units if t in historical.columns
        ]
        worst_corr_gap = 0.0
        for i, left in enumerate(targets):
            for right in targets[i + 1:]:
                hist_corr = float(
                    historical[left].corr(historical[right])
                )
                # 逐场景相关（加权平均）：度量时段间协同结构，
                # 对场景间系统性水平偏移（如极端场景注入）不变
                scenario_corr = 0.0
                for scenario in scenarios:
                    pair_corr = float(
                        np.corrcoef(
                            scenario.trajectories[left].to_numpy(
                                dtype=float
                            ),
                            scenario.trajectories[right].to_numpy(
                                dtype=float
                            ),
                        )[0, 1]
                    )
                    if np.isfinite(pair_corr):
                        scenario_corr += scenario.probability * pair_corr
                if np.isfinite(hist_corr) and np.isfinite(scenario_corr):
                    worst_corr_gap = max(
                        worst_corr_gap, abs(scenario_corr - hist_corr)
                    )
        checks.append(
            DiagnosticCheck(
                name="correlation_preservation",
                passed=worst_corr_gap <= corr_tol,
                value=worst_corr_gap,
                detail=(
                    f"max |corr(scenario)−corr(historical)| "
                    f"= {worst_corr_gap:.4f} (tol {corr_tol})"
                ),
            )
        )
        # 极端覆盖：场景超过历史 q_extreme 水平的加权比例
        extreme_ratio = 0.0
        for target in targets:
            hist_values = historical[target].to_numpy(dtype=float)
            threshold = float(np.quantile(hist_values, extreme_quantile))
            exceed = np.concatenate(
                [
                    (
                        s.trajectories[target].to_numpy(dtype=float)
                        > threshold
                    ).astype(float)
                    * s.probability
                    for s in scenarios
                ]
            )
            extreme_ratio = max(extreme_ratio, float(np.mean(exceed)))
        checks.append(
            DiagnosticCheck(
                name="extreme_coverage",
                passed=extreme_ratio >= min_extreme_ratio,
                value=extreme_ratio,
                detail=(
                    f"P(scenario > historical q{extreme_quantile}) "
                    f"= {extreme_ratio:.4f} (min {min_extreme_ratio})"
                ),
            )
        )

    return ScenarioDiagnostics(checks=tuple(checks))


def assert_reproducible(
    builder: Callable[..., ScenarioSet],
    *args,
    **kwargs,
) -> None:
    """复现性诊断：同参数（含 seed）重跑构建器，场景集必须逐点一致。"""
    first = builder(*args, **kwargs)
    second = builder(*args, **kwargs)
    if len(first.scenarios) != len(second.scenarios):
        raise AssertionError(
            "scenario builder is not reproducible: scenario count differs"
        )
    for left, right in zip(first.scenarios, second.scenarios, strict=True):
        if left.scenario_id != right.scenario_id:
            raise AssertionError(
                "scenario builder is not reproducible: ids differ"
            )
        for target in left.trajectories:
            if not np.array_equal(
                left.trajectories[target].to_numpy(dtype=float),
                right.trajectories[target].to_numpy(dtype=float),
            ):
                raise AssertionError(
                    "scenario builder is not reproducible: "
                    f"trajectory {target!r} differs"
                )
