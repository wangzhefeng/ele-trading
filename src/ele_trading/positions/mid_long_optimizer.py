"""中长期头寸约束优化策略（v4 P0 / §7.1.2）。

在保留启发式基线（``mid_long_planner.plan_mid_long_position``）的
同时，新增 CVaR 约束优化策略：

.. math::

    \\min \\quad E[Cost] + \\lambda_{cvar} \\times CVaR_\\alpha(Cost)
        + \\lambda_{turnover} \\times \\sum_m |q_{long}[m] - q_{prev}[m]|

    Cost[m] = q_{long}[m] \\cdot p_{long}[m]
        + (q_{load}[m] - q_{long}[m]) \\cdot p_{real}[m]

约束：覆盖上下限、预算（期望成本口径，与启发式一致）、可选年度
合约总量上限、换手惩罚。CVaR 复用 ``optimization/risk.py`` 的
线性化实现；求解经统一 adapter ``optimization.solver``。

默认策略保持启发式（v4 D 序列决策前不改变默认行为，§9.3）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, cast

import numpy as np
import pandas as pd
from pulp import LpMinimize, LpProblem, LpVariable, lpSum, value

from ele_trading.markets.sections import MarketConfig
from ele_trading.optimization.risk import add_cvar_auxiliaries
from ele_trading.optimization.solver import SolveStatus, solve_pulp_model
from ele_trading.positions.contracts import PositionPlan
from ele_trading.positions.mid_long_planner import plan_mid_long_position


# ------------------------------------------------------------------ #
#  独立策略配置（v4 §10：不并入 v3 六子对象）
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class MidLongOptimizationConfig:
    """中长期约束优化策略参数。"""

    cvar_alpha: float = 0.95
    cvar_weight: float = 0.5
    turnover_penalty: float = 0.0
    min_coverage: float = 0.0
    max_coverage: float = 1.0
    max_total_long_mwh: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 < self.cvar_alpha < 1.0:
            raise ValueError("cvar_alpha must be within (0, 1)")
        if self.cvar_weight < 0.0:
            raise ValueError("cvar_weight must be non-negative")
        if self.turnover_penalty < 0.0:
            raise ValueError("turnover_penalty must be non-negative")
        if not 0.0 <= self.min_coverage <= self.max_coverage <= 1.0:
            raise ValueError(
                "coverage bounds must satisfy 0 <= min <= max <= 1"
            )
        if (
            self.max_total_long_mwh is not None
            and self.max_total_long_mwh <= 0.0
        ):
            raise ValueError("max_total_long_mwh must be positive")


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """v4 独立策略配置：头寸策略选择与参数。"""

    mid_long_strategy: str = "heuristic"
    mid_long: MidLongOptimizationConfig = MidLongOptimizationConfig()

    def __post_init__(self) -> None:
        if self.mid_long_strategy not in ("heuristic", "cvar_optimization"):
            raise ValueError(
                "mid_long_strategy must be 'heuristic' or "
                "'cvar_optimization'"
            )


def _solved(expression) -> float:
    """读取已求解表达式取值；未求解（None）显式失败。"""
    result = value(expression)
    if result is None:
        raise RuntimeError(
            "mid-long optimization must be optimally solved before extraction"
        )
    return float(cast(float, result))


# ------------------------------------------------------------------ #
#  CVaR 约束优化
# ------------------------------------------------------------------ #

def plan_mid_long_position_cvar(
    q_load_forecast: pd.Series,
    p_long_forecast: pd.Series,
    spot_scenarios: Mapping[str, pd.Series],
    scenario_probabilities: Mapping[str, float],
    budget: float,
    config: MidLongOptimizationConfig,
    q_long_prev: pd.Series | None = None,
    solver=None,
) -> PositionPlan:
    """中长期头寸的 CVaR 约束优化（v4 P0）。

    参数：
        q_load_forecast: 月度负荷预测（MWh/月）。
        p_long_forecast: 月度中长期价格点预测（元/MWh）。
        spot_scenarios: 月度实时价格场景 {scenario_id: Series}。
        scenario_probabilities: 场景概率（和为 1）。
        budget: 期望成本预算（元，与启发式口径一致）。
        config: 优化参数。
        q_long_prev: 上月合约量（换手惩罚参照；None 表示不惩罚）。
    """
    months = list(range(len(q_load_forecast)))
    if not months:
        raise ValueError("monthly forecasts must not be empty")
    if not q_load_forecast.index.equals(p_long_forecast.index):
        raise ValueError("monthly forecasts must use the same index")
    if budget < 0.0 or not np.isfinite(budget):
        raise ValueError("budget must be finite and non-negative")
    if not spot_scenarios:
        raise ValueError("spot scenarios must not be empty")
    for scenario_id, series in spot_scenarios.items():
        if not series.index.equals(q_load_forecast.index):
            raise ValueError(
                f"spot scenario {scenario_id!r} must align with monthly index"
            )

    q_load = q_load_forecast.to_numpy(dtype=float)
    p_long = p_long_forecast.to_numpy(dtype=float)
    if (q_load < 0.0).any():
        raise ValueError("monthly load forecast must be non-negative")

    model = LpProblem("mid_long_position_cvar", LpMinimize)

    # 决策变量：各月中长期合约量（覆盖上下限 + 不可超购）
    q_long = {
        month: LpVariable(
            f"q_long_{month}",
            lowBound=config.min_coverage * q_load[month],
            upBound=config.max_coverage * q_load[month],
        )
        for month in months
    }

    # 换手惩罚线性化：|q_long − q_prev|
    turnover_expr = 0.0
    if config.turnover_penalty > 0.0 and q_long_prev is not None:
        if not q_long_prev.index.equals(q_load_forecast.index):
            raise ValueError("q_long_prev must align with monthly index")
        prev = q_long_prev.to_numpy(dtype=float)
        turnover_terms = []
        for month in months:
            aux = LpVariable(f"turnover_{month}", lowBound=0.0)
            model += aux >= q_long[month] - prev[month]
            model += aux >= prev[month] - q_long[month]
            turnover_terms.append(aux)
        turnover_expr = config.turnover_penalty * lpSum(turnover_terms)

    # 场景成本：Cost_s = Σ_m [q_long·p_long + (q_load − q_long)·p_real_s]
    losses = {}
    probabilities = {}
    for scenario_id, series in spot_scenarios.items():
        p_real = series.to_numpy(dtype=float)
        losses[scenario_id] = lpSum(
            q_long[month] * p_long[month]
            + (q_load[month] - q_long[month]) * p_real[month]
            for month in months
        )
        probabilities[scenario_id] = float(
            scenario_probabilities[scenario_id]
        )

    expected_cost = lpSum(
        probabilities[scenario_id] * losses[scenario_id]
        for scenario_id in losses
    )
    cvar = add_cvar_auxiliaries(
        model,
        losses,
        probabilities,
        alpha=config.cvar_alpha,
        prefix="midlong",
    )
    model += expected_cost + config.cvar_weight * cvar.expression + turnover_expr

    # 预算约束：期望成本 ≤ budget（budget=0 视为不约束，与启发式口径兼容）
    if budget > 0.0:
        model += expected_cost <= budget, "budget_cap"
    # 可选年度合约总量上限（v4 §7.1.2 公式中的 annual_budget）
    if config.max_total_long_mwh is not None:
        model += (
            lpSum(q_long[month] for month in months)
            <= config.max_total_long_mwh,
            "total_long_cap",
        )

    result = solve_pulp_model(model, solver=solver)
    if result.status is not SolveStatus.OPTIMAL:
        raise RuntimeError(f"mid-long position optimization failed: {result.message}")

    q_long_values = np.array(
        [_solved(q_long[month]) for month in months]
    )
    total_load = float(q_load.sum())
    alpha_long = (
        float(q_long_values.sum()) / total_load if total_load > 0.0 else 0.0
    )
    expected_value = _solved(expected_cost)

    return PositionPlan(
        alpha_long=alpha_long,
        alpha_real=1.0 - alpha_long,
        q_long_monthly=pd.Series(
            q_long_values, index=q_load_forecast.index
        ),
        price_band=(
            float(p_long_forecast.min()),
            float(p_long_forecast.max()),
        ),
        expected_cost=expected_value,
        # 优化策略的风险口径：CVaR_α 取值（区别于启发式的成本标准差）
        expected_risk=_solved(cvar.expression),
        budget_used=expected_value / budget if budget > 0.0 else 0.0,
        coverage=alpha_long,
    )


# ------------------------------------------------------------------ #
#  策略路由（配置切换，默认启发式）
# ------------------------------------------------------------------ #

def plan_mid_long(
    q_load_forecast: pd.Series,
    p_long_forecast: pd.Series,
    p_spot_forecast: pd.Series,
    budget: float,
    config: MarketConfig,
    *,
    strategy: StrategyConfig | None = None,
    spot_scenarios: Mapping[str, pd.Series] | None = None,
    scenario_probabilities: Mapping[str, float] | None = None,
    q_long_prev: pd.Series | None = None,
    solver=None,
) -> PositionPlan:
    """中长期头寸策略路由：默认启发式；cvar_optimization 需场景输入。"""
    strategy = strategy or StrategyConfig()
    if strategy.mid_long_strategy == "heuristic":
        return plan_mid_long_position(
            q_load_forecast,
            p_long_forecast,
            p_spot_forecast,
            budget,
            config,
        )
    if spot_scenarios is None or scenario_probabilities is None:
        raise ValueError(
            "cvar_optimization requires spot_scenarios and "
            "scenario_probabilities"
        )
    return plan_mid_long_position_cvar(
        q_load_forecast,
        p_long_forecast,
        spot_scenarios,
        scenario_probabilities,
        budget,
        strategy.mid_long,
        q_long_prev=q_long_prev,
        solver=solver,
    )
