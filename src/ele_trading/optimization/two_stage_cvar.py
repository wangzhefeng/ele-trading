"""可运行的 PuLP 两阶段（Two-stage）储能优化器，带加权 CVaR 风险项。

模型结构（对应电力市场「日前申报 + 实时偏差结算」）：
- 第一阶段：日前申报量 bid_t（不随场景变化的 here-and-now 决策）。
- 第二阶段：各价格/负荷/风/光场景下的充放电、SOC、净上网与
  正/负偏差调节（wait-and-see recourse）。
- 目标：最小化 期望成本 + risk_weight * CVaR，其中场景成本 = -场景收益。

市场规则参数（正/负偏差考核系数）必须由上层显式传入，
本模块不提供市场默认值，保持「通用内核 vs 市场规则」边界。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from pulp import (
    LpAffineExpression,
    LpMinimize,
    LpProblem,
    LpVariable,
    lpSum,
    value,
)

from ele_trading.scenario.contracts import Scenario, ScenarioSet

from .bess_model import BESSConfig, BESSVariables, add_bess_constraints
from .extraction import extract_bess_values
from .objectives import throughput_degradation_cost
from .risk import (
    CVaRAuxiliaries,
    add_cvar_auxiliaries,
    risk_adjusted_objective,
    weighted_var_cvar,
)
from .solver import SolveStatus, SolverResult, solve_pulp_model


# ScenarioSet 中轨迹名称的别名表：允许不同数据源使用不同命名
_TARGET_ALIASES = {
    "price": ("price",),
    "load": ("load", "load_power"),
    "wind": ("wind", "wind_power"),
    "pv": ("pv", "pv_power", "solar", "solar_power"),
}


@dataclass(frozen=True, slots=True)
class ScenarioRecourse:
    """单个场景求解后的第二阶段储能调节与偏差结果。"""

    scenario_id: str                  # 场景 ID
    probability: float                # 场景概率
    p_charge: list[float]             # 各时段充电功率
    p_discharge: list[float]          # 各时段放电功率
    soc: list[float]                  # 各时段末 SOC
    net_export: list[float]           # 各时段净上网功率
    deviation_positive: list[float]   # 各时段正偏差（实际 > 申报）
    deviation_negative: list[float]   # 各时段负偏差（实际 < 申报）
    cost: float                       # 场景成本（= -场景收益）


@dataclass(frozen=True, slots=True)
class TwoStageCVaRResult:
    """typed 求解结果；求解失败时不包含伪造的计划数据。"""

    solve_status: SolveStatus                     # 求解状态
    solver_result: SolverResult                   # 求解器层原始结果
    first_stage_bid: list[float] | None           # 第一阶段日前申报量
    scenario_recourse: dict[str, ScenarioRecourse]  # 各场景第二阶段调节结果
    expected_cost: float | None                   # 期望成本
    var: float | None                             # 事后评估的 VaR
    cvar: float | None                            # 事后评估的 CVaR
    objective_value: float | None                 # 目标函数值
    trace_metadata: dict[str, object]             # 来源追溯元数据

    @property
    def first_stage_schedule(self) -> list[float] | None:
        """向后兼容别名：第一阶段计划即日前申报量。"""
        return self.first_stage_bid


@dataclass(slots=True)
class _ProblemContext:
    """_build_problem 的内部中间产物：模型与全部变量/表达式的句柄集合。"""

    model: LpProblem
    first_stage_bid: dict[int, LpVariable]                # 第一阶段申报量变量
    bess: dict[str, BESSVariables]                        # 各场景储能变量
    net_export: dict[str, dict[int, object]]              # 各场景净上网表达式
    deviation_positive: dict[str, dict[int, LpVariable]]  # 各场景正偏差变量
    deviation_negative: dict[str, dict[int, LpVariable]]  # 各场景负偏差变量
    losses: dict[str, LpAffineExpression]                 # 各场景成本表达式
    expected_cost: LpAffineExpression                     # 期望成本表达式
    cvar: CVaRAuxiliaries                                 # CVaR 辅助变量与表达式
    day_ahead_prices: tuple[float, ...]                   # 日前电价序列


def _revalidate_scenario_set(scenario_set: ScenarioSet) -> ScenarioSet:
    """防御性重建 ScenarioSet：深拷贝轨迹与元数据，隔离调用方后续修改。"""
    if not isinstance(scenario_set, ScenarioSet):
        raise ValueError("scenario_set must be a ScenarioSet")
    scenarios = tuple(
        Scenario(
            scenario_id=item.scenario_id,
            probability=item.probability,
            issue_time=item.issue_time,
            trajectories={
                target: trajectory.copy()
                for target, trajectory in item.trajectories.items()
            },
            seed=item.seed,
            source_versions=dict(item.source_versions),
        )
        for item in scenario_set.scenarios
    )
    return ScenarioSet(
        horizon=scenario_set.horizon,
        valid_time_index=scenario_set.valid_time_index,
        units=dict(scenario_set.units),
        scenarios=scenarios,
        metadata=dict(scenario_set.metadata),
    )


def _target_name(scenario_set: ScenarioSet, role: str) -> str:
    """按别名表在 ScenarioSet 中定位指定角色（价格/负荷/风/光）的轨迹名。"""
    for target in _TARGET_ALIASES[role]:
        if target in scenario_set.units:
            return target
    raise ValueError(
        f"ScenarioSet must contain a {role} trajectory"
    )


def _day_ahead_prices(
    scenario_set: ScenarioSet,
    price_target: str,
    supplied: Sequence[float] | pd.Series | None,
) -> tuple[float, ...]:
    """确定日前电价序列。

    未显式提供时用各场景价格的概率加权期望作为日前价格的代理；
    提供 pd.Series 时索引必须与 ScenarioSet 的 valid_time_index 一致。
    """
    if supplied is None:
        # 概率加权期望价格：sum_s p_s * price_s
        values = sum(
            (
                item.probability
                * item.trajectories[price_target].to_numpy(dtype=float)
                for item in scenario_set.scenarios
            ),
            np.zeros(scenario_set.horizon, dtype=float),
        )
    elif isinstance(supplied, pd.Series):
        if not supplied.index.equals(scenario_set.valid_time_index):
            raise ValueError(
                "day_ahead_prices index must match ScenarioSet valid times"
            )
        values = supplied.to_numpy(dtype=float)
    else:
        values = np.asarray(supplied, dtype=float)
    if values.shape != (scenario_set.horizon,):
        raise ValueError(
            "day_ahead_prices length must match ScenarioSet horizon"
        )
    if not np.isfinite(values).all():
        raise ValueError("day_ahead_prices must be finite")
    return tuple(float(item) for item in values)


def _build_problem(
    scenario_set: ScenarioSet,
    *,
    bess_config: BESSConfig,
    day_ahead_prices: Sequence[float] | pd.Series | None,
    alpha: float,
    risk_weight: float,
    degradation_cost: float,
    deviation_penalty_positive: float,
    deviation_penalty_negative: float,
) -> _ProblemContext:
    """构建两阶段 + CVaR 的 PuLP 模型（不求解），返回全部句柄。

    参数：
        scenario_set: 场景集合（价格/负荷/风/光轨迹 + 概率）。
        bess_config: 储能物理参数。
        day_ahead_prices: 日前电价；None 时用场景概率加权期望。
        alpha: CVaR 置信水平。
        risk_weight: CVaR 在目标中的权重。
        degradation_cost: 单位吞吐量线性退化成本。
        deviation_penalty_positive / deviation_penalty_negative:
            正/负偏差考核系数（市场参数，由上层显式传入）。
    """
    if not isinstance(scenario_set, ScenarioSet):
        raise ValueError("scenario_set must be a ScenarioSet")
    for name, amount in {
        "degradation_cost": degradation_cost,
        "deviation_penalty_positive": deviation_penalty_positive,
        "deviation_penalty_negative": deviation_penalty_negative,
    }.items():
        if not np.isfinite(amount) or amount < 0.0:
            raise ValueError(f"{name} must be finite and non-negative")

    # 按别名表定位四类轨迹
    price_target = _target_name(scenario_set, "price")
    load_target = _target_name(scenario_set, "load")
    wind_target = _target_name(scenario_set, "wind")
    pv_target = _target_name(scenario_set, "pv")
    day_ahead = _day_ahead_prices(
        scenario_set,
        price_target,
        day_ahead_prices,
    )
    steps = tuple(range(scenario_set.horizon))
    model = LpProblem("two_stage_bess_cvar", LpMinimize)

    # ---------------- 第一阶段：日前申报量 ----------------
    # 申报上限取所有场景下「新能源 - 负荷 + 放电上限」的最大值，
    # 保证任何场景的 recourse 都有能力兑现申报（收紧变量界，利于求解）
    max_export_by_step = {
        step: max(
            0.0,
            max(
                float(
                    item.trajectories[wind_target].iloc[step]
                    + item.trajectories[pv_target].iloc[step]
                    - item.trajectories[load_target].iloc[step]
                    + bess_config.p_dis_max
                )
                for item in scenario_set.scenarios
            ),
        )
        for step in steps
    }
    bid = {
        step: LpVariable(
            f"first_stage_bid_{step}",
            lowBound=0.0,
            upBound=max_export_by_step[step],
        )
        for step in steps
    }
    # ---------------- 第二阶段：逐场景 recourse ----------------
    bess_by_scenario: dict[str, BESSVariables] = {}
    net_export_by_scenario: dict[str, dict[int, object]] = {}
    positive_by_scenario: dict[str, dict[int, LpVariable]] = {}
    negative_by_scenario: dict[str, dict[int, LpVariable]] = {}
    loss_by_scenario: dict[str, LpAffineExpression] = {}

    for scenario_index, scenario in enumerate(scenario_set.scenarios):
        # 净负荷 = 负荷 - 风电 - 光伏
        net_load = {
            step: float(
                scenario.trajectories[load_target].iloc[step]
                - scenario.trajectories[wind_target].iloc[step]
                - scenario.trajectories[pv_target].iloc[step]
            )
            for step in steps
        }
        # 复用共享储能约束内核；no_export 时需要净负荷数据
        bess = add_bess_constraints(
            model,
            steps,
            bess_config,
            net_load=net_load if bess_config.no_export else None,
            prefix=f"recourse_{scenario_index}",
        )
        # 净上网 = -净负荷 + 放电 - 充电
        net_export = {
            step: (
                -net_load[step]
                + bess.p_discharge[step]
                - bess.p_charge[step]
            )
            for step in steps
        }
        # 正/负偏差变量（偏差拆分为两个非负变量）
        positive = {
            step: LpVariable(
                f"deviation_positive_{scenario_index}_{step}",
                lowBound=0.0,
            )
            for step in steps
        }
        negative = {
            step: LpVariable(
                f"deviation_negative_{scenario_index}_{step}",
                lowBound=0.0,
            )
            for step in steps
        }
        # 偏差平衡：实际净上网 - 申报 = 正偏差 - 负偏差
        for step in steps:
            model += (
                net_export[step] - bid[step]
                == positive[step] - negative[step],
                f"deviation_balance_{scenario_index}_{step}",
            )
        # 场景收益 = 日前申报收入 + 实时偏差结算 - 偏差考核 - 退化成本
        revenue = (
            lpSum(
                (
                    day_ahead[step] * bid[step]
                    + float(
                        scenario.trajectories[price_target].iloc[step]
                    )
                    * (net_export[step] - bid[step])
                    - deviation_penalty_positive * positive[step]
                    - deviation_penalty_negative * negative[step]
                )
                * bess_config.dt
                for step in steps
            )
            - throughput_degradation_cost(
                bess,
                steps,
                deg_cost_per_mwh=degradation_cost,
                dt=bess_config.dt,
            )
        )
        bess_by_scenario[scenario.scenario_id] = bess
        net_export_by_scenario[scenario.scenario_id] = net_export
        positive_by_scenario[scenario.scenario_id] = positive
        negative_by_scenario[scenario.scenario_id] = negative
        # 最小化问题以成本（负收益）为损失
        loss_by_scenario[scenario.scenario_id] = -revenue

    # ---------------- 目标：期望成本 + 风险权重 * CVaR ----------------
    probabilities = {
        item.scenario_id: item.probability
        for item in scenario_set.scenarios
    }
    expected_cost = lpSum(
        probabilities[scenario_id] * loss
        for scenario_id, loss in loss_by_scenario.items()
    )
    cvar = add_cvar_auxiliaries(
        model,
        loss_by_scenario,
        probabilities,
        alpha=alpha,
    )
    model += risk_adjusted_objective(
        expected_cost,
        cvar.expression,
        risk_weight=risk_weight,
    )
    return _ProblemContext(
        model=model,
        first_stage_bid=bid,
        bess=bess_by_scenario,
        net_export=net_export_by_scenario,
        deviation_positive=positive_by_scenario,
        deviation_negative=negative_by_scenario,
        losses=loss_by_scenario,
        expected_cost=expected_cost,
        cvar=cvar,
        day_ahead_prices=day_ahead,
    )


def solve_two_stage_cvar(
    scenario_set: ScenarioSet,
    *,
    bess_config: BESSConfig,
    deviation_penalty_positive: float,
    deviation_penalty_negative: float,
    day_ahead_prices: Sequence[float] | pd.Series | None = None,
    alpha: float = 0.95,
    risk_weight: float = 1.0,
    degradation_cost: float = 0.01,
    solver=None,
) -> TwoStageCVaRResult:
    """构建并通过 PuLP/CBC 求解两阶段 + CVaR 问题。

    校验失败或求解非最优时返回带 ERROR/对应状态的空结果，
    不返回伪造的零计划；偏差考核系数必须由上层显式传入。
    """
    # 输入校验失败：走 ERROR 结果通道，不抛异常
    try:
        scenario_set = _revalidate_scenario_set(scenario_set)
    except (TypeError, ValueError) as exc:
        solver_result = SolverResult(
            status=SolveStatus.ERROR,
            objective_value=None,
            raw_status=None,
            solver_name=(
                solver.__class__.__name__
                if solver is not None
                else "PULP_CBC_CMD"
            ),
            message=str(exc),
        )
        return TwoStageCVaRResult(
            solve_status=SolveStatus.ERROR,
            solver_result=solver_result,
            first_stage_bid=None,
            scenario_recourse={},
            expected_cost=None,
            var=None,
            cvar=None,
            objective_value=None,
            trace_metadata={"validation_error": str(exc)},
        )
    context = _build_problem(
        scenario_set,
        bess_config=bess_config,
        day_ahead_prices=day_ahead_prices,
        alpha=alpha,
        risk_weight=risk_weight,
        degradation_cost=degradation_cost,
        deviation_penalty_positive=deviation_penalty_positive,
        deviation_penalty_negative=deviation_penalty_negative,
    )
    solver_result = solve_pulp_model(
        context.model,
        solver=solver,
    )
    # 来源追溯元数据：场景来源、参数与求解器信息，便于结果审计
    trace_metadata = {
        "scenario_issue_time": scenario_set.issue_time.isoformat(),
        "scenario_ids": [
            item.scenario_id for item in scenario_set.scenarios
        ],
        "scenario_source_versions": scenario_set.source_versions,
        "scenario_metadata": dict(scenario_set.metadata),
        "day_ahead_prices": list(context.day_ahead_prices),
        "alpha": float(alpha),
        "risk_weight": float(risk_weight),
        "dt": bess_config.dt,
        "solver_name": solver_result.solver_name,
    }
    # 非最优：返回空计划 + 状态，不伪造数据
    if solver_result.status is not SolveStatus.OPTIMAL:
        return TwoStageCVaRResult(
            solve_status=solver_result.status,
            solver_result=solver_result,
            first_stage_bid=None,
            scenario_recourse={},
            expected_cost=None,
            var=None,
            cvar=None,
            objective_value=None,
            trace_metadata=trace_metadata,
        )

    # ---------------- 提取最优解 ----------------
    steps = tuple(range(scenario_set.horizon))
    scenario_lookup = {
        item.scenario_id: item
        for item in scenario_set.scenarios
    }
    recourse: dict[str, ScenarioRecourse] = {}
    for scenario_id, bess in context.bess.items():
        scenario = scenario_lookup[scenario_id]
        bess_values = extract_bess_values(bess, steps)
        recourse[scenario_id] = ScenarioRecourse(
            scenario_id=scenario_id,
            probability=scenario.probability,
            p_charge=bess_values["p_charge"],
            p_discharge=bess_values["p_discharge"],
            soc=bess_values["soc"],
            net_export=[
                float(value(context.net_export[scenario_id][step]))
                for step in steps
            ],
            deviation_positive=[
                float(
                    value(
                        context.deviation_positive[scenario_id][step]
                    )
                )
                for step in steps
            ],
            deviation_negative=[
                float(
                    value(
                        context.deviation_negative[scenario_id][step]
                    )
                )
                for step in steps
            ],
            cost=float(value(context.losses[scenario_id])),
        )
    # 用独立于 LP 的离散公式事后评估 VaR/CVaR，与优化结果交叉验证
    reported_var, reported_cvar = weighted_var_cvar(
        {
            scenario_id: item.cost
            for scenario_id, item in recourse.items()
        },
        {
            scenario_id: item.probability
            for scenario_id, item in recourse.items()
        },
        alpha=alpha,
    )
    return TwoStageCVaRResult(
        solve_status=solver_result.status,
        solver_result=solver_result,
        first_stage_bid=[
            float(value(context.first_stage_bid[step]))
            for step in steps
        ],
        scenario_recourse=recourse,
        expected_cost=float(value(context.expected_cost)),
        var=reported_var,
        cvar=reported_cvar,
        objective_value=solver_result.objective_value,
        trace_metadata=trace_metadata,
    )


def build_two_stage_cvar_model(
    T,
    OMEGA,
    p_omega: Mapping,
    pi_da: Mapping,
    pi_rt: Mapping,
    soc0: float,
    soc_min: float,
    soc_max: float,
    p_ch_max: float,
    p_dis_max: float,
    eta_ch: float,
    eta_dis: float,
    deg_cost: float,
    *,
    kappa_pos: float,
    kappa_neg: float,
    dt: float = 1.0,
    alpha: float = 0.95,
    lam: float = 1.0,
) -> LpProblem:
    """v1 旧示例入口的窄适配器：构造未求解的 PuLP 模型。

    不是 v2 主 API（主 API 为 solve_two_stage_cvar）；把旧式
    (T, OMEGA, p_omega, pi_da, pi_rt, kappa_pos, kappa_neg) 参数
    包装成 ScenarioSet 后复用 _build_problem。
    kappa_pos / kappa_neg（正/负偏差考核系数）同样必须显式传入。
    """
    time_steps = tuple(T)
    scenario_ids = tuple(OMEGA)
    if not time_steps or not scenario_ids:
        raise ValueError("T and OMEGA must not be empty")
    # 旧接口无真实时间轴：用固定起点构造占位 valid_time_index
    issue_time = pd.Timestamp("2000-01-01", tz="UTC")
    index = pd.date_range(
        issue_time + pd.Timedelta(hours=dt),
        periods=len(time_steps),
        freq=pd.Timedelta(hours=dt),
    )
    # 把旧式参数包装为 ScenarioSet：价格取实时价轨迹，负荷/风/光置零
    scenario_set = ScenarioSet(
        horizon=len(time_steps),
        valid_time_index=index,
        units={
            "price": "unknown",
            "load": "MW",
            "wind_power": "MW",
            "pv_power": "MW",
        },
        scenarios=tuple(
            Scenario(
                scenario_id=str(scenario_id),
                probability=float(p_omega[scenario_id]),
                issue_time=issue_time,
                trajectories={
                    "price": pd.Series(
                        [
                            pi_rt[(step, scenario_id)]
                            for step in time_steps
                        ],
                        index=index,
                        dtype=float,
                    ),
                    "load": pd.Series(0.0, index=index),
                    "wind_power": pd.Series(0.0, index=index),
                    "pv_power": pd.Series(0.0, index=index),
                },
                seed=0,
                source_versions={
                    "price": "legacy-pi-rt",
                    "load": "legacy-zero",
                    "wind_power": "legacy-zero",
                    "pv_power": "legacy-zero",
                },
            )
            for scenario_id in scenario_ids
        ),
        metadata={"compatibility": "build_two_stage_cvar_model"},
    )
    return _build_problem(
        scenario_set,
        bess_config=BESSConfig(
            soc0=soc0,
            soc_min=soc_min,
            soc_max=soc_max,
            p_ch_max=p_ch_max,
            p_dis_max=p_dis_max,
            eta_ch=eta_ch,
            eta_dis=eta_dis,
            dt=dt,
        ),
        day_ahead_prices=[pi_da[step] for step in time_steps],
        alpha=alpha,
        risk_weight=lam,
        degradation_cost=deg_cost,
        deviation_penalty_positive=kappa_pos,
        deviation_penalty_negative=kappa_neg,
    ).model
