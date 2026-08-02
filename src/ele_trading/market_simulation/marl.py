"""MARL 报价环境、表格 Q 训练与 policy guard（v5 §10.4-§10.6）。

环境状态只包含参与者当时可获得的信息：自身资源状态与成本、公开
负荷、上一轮公开出清结果和规则边界；动作是离散加成网格索引；
奖励 = 利润 − 违规惩罚。真实未来出清结果、其他参与者私有成本
不进入任何 observation。

``run_policy_guard`` 在训练后执行安全闸：报价边界、零出力虚增
利润（记账错误 / simulator exploitation）、环境微扰敏感性、
训练/保留环境收益差。任何一项失败都不得晋级影子回测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .bidding import OfferStack
from .grid.contracts import Generator, GridSnapshot
from .sced import solve_sced


# ------------------------------------------------------------------ #
#  环境
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class MARLObservation:
    """单个 agent 的观察：只含自身信息与公开市场信息。"""

    own_marginal_cost: float
    own_capacity_mw: float
    system_load_mw: float
    last_public_lmp: float | None
    price_floor: float
    price_cap: float


class MARLBiddingEnv:
    """多 agent 同步报价环境（清算复用真实 SCED）。"""

    metadata = {"name": "marl-bidding-v1"}

    def __init__(
        self,
        grid: GridSnapshot,
        load_mw: Mapping[str, float],
        *,
        markup_grid: Sequence[float],
        price_floor: float = 0.0,
        price_cap: float = 2_000.0,
        violation_penalty: float = 1e6,
        solver=None,
    ) -> None:
        if not isinstance(grid, GridSnapshot):
            raise ValueError("grid must be a GridSnapshot")
        self.grid = grid
        self.load = {bus.bus_id: float(load_mw.get(bus.bus_id, 0.0)) for bus in grid.buses}
        if any(amount < 0.0 or not np.isfinite(amount) for amount in self.load.values()):
            raise ValueError("load_mw must be finite and non-negative")
        grid_arr = np.asarray(markup_grid, dtype=float)
        if grid_arr.ndim != 1 or not len(grid_arr) or not np.isfinite(grid_arr).all():
            raise ValueError("markup_grid must be a finite 1-D vector")
        if (grid_arr <= 0.0).any():
            raise ValueError("markups must be positive")
        self.markup_grid = grid_arr
        if not 0.0 <= price_floor <= price_cap or not np.isfinite(price_cap):
            raise ValueError("price bounds must satisfy 0 <= floor <= cap")
        self.price_floor = float(price_floor)
        self.price_cap = float(price_cap)
        if not np.isfinite(violation_penalty) or violation_penalty < 0.0:
            raise ValueError("violation_penalty must be finite and non-negative")
        self.violation_penalty = float(violation_penalty)
        self.solver = solver
        self.agent_ids = tuple(sorted(grid.generator_ids))
        self._last_lmp: dict[str, float] | None = None

    @property
    def action_dim(self) -> int:
        return len(self.markup_grid)

    def reset(self) -> dict[str, MARLObservation]:
        self._last_lmp = None
        return self._observations()

    def _observations(self) -> dict[str, MARLObservation]:
        total_load = float(sum(self.load.values()))
        observations: dict[str, MARLObservation] = {}
        for generator in self.grid.generators:
            gid = generator.generator_id
            observations[gid] = MARLObservation(
                own_marginal_cost=generator.marginal_cost,
                own_capacity_mw=generator.p_max_mw,
                system_load_mw=total_load,
                last_public_lmp=(
                    self._last_lmp.get(generator.bus_id)
                    if self._last_lmp is not None
                    else None
                ),
                price_floor=self.price_floor,
                price_cap=self.price_cap,
            )
        return observations

    def step(
        self,
        actions: Mapping[str, int],
    ) -> tuple[
        dict[str, MARLObservation],
        dict[str, float],
        bool,
        Mapping[str, object],
    ]:
        """同步执行一轮报价-出清。返回 (obs, reward, done=True, info)。"""
        if set(actions) != set(self.agent_ids):
            raise ValueError("actions must cover every agent exactly once")
        offer_generators: list[Generator] = []
        offers: dict[str, OfferStack] = {}
        violations: dict[str, float] = {}
        for generator in self.grid.generators:
            gid = generator.generator_id
            action = actions[gid]
            if not isinstance(action, (int, np.integer)) or not 0 <= int(action) < len(
                self.markup_grid
            ):
                raise ValueError(f"action for {gid!r} out of range")
            offered = generator.marginal_cost * float(self.markup_grid[int(action)])
            clipped = float(np.clip(offered, self.price_floor, self.price_cap))
            violations[gid] = abs(offered - clipped)
            offers[gid] = OfferStack.flat(
                gid,
                capacity_mw=generator.p_max_mw,
                price=clipped,
                rule_version="marl-v1",
            )
            offer_generators.append(
                Generator(
                    generator_id=gid,
                    bus_id=generator.bus_id,
                    p_min_mw=generator.p_min_mw,
                    p_max_mw=generator.p_max_mw,
                    ramp_up_mw=generator.ramp_up_mw,
                    ramp_down_mw=generator.ramp_down_mw,
                    marginal_cost=clipped,
                )
            )
        offer_grid = GridSnapshot(
            as_of=self.grid.as_of,
            version=f"{self.grid.version}:marl",
            buses=self.grid.buses,
            branches=self.grid.branches,
            generators=tuple(offer_generators),
            reserve_requirement_mw=self.grid.reserve_requirement_mw,
        )
        clearing = solve_sced(offer_grid, self.load, solver=self.solver)
        self._last_lmp = dict(clearing.lmp)

        rewards: dict[str, float] = {}
        for generator in self.grid.generators:
            gid = generator.generator_id
            profit = (
                clearing.lmp[generator.bus_id] - generator.marginal_cost
            ) * clearing.dispatch_mw[gid]
            rewards[gid] = profit - self.violation_penalty * violations[gid]

        info: dict[str, object] = {
            "lmp": dict(clearing.lmp),
            "dispatch_mw": dict(clearing.dispatch_mw),
            "offers": offers,
            "violations": violations,
        }
        return self._observations(), rewards, True, info


# ------------------------------------------------------------------ #
#  表格独立 Q 学习（自包含基线，不引入外部 RL 依赖）
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class TrainedPolicies:
    """训练产物：每 agent 的 Q 表与贪心动作。"""

    q_tables: Mapping[str, np.ndarray]
    greedy_actions: Mapping[str, int]
    episodes: int
    seed: int


def train_independent_q(
    env: MARLBiddingEnv,
    *,
    episodes: int,
    learning_rate: float,
    epsilon: float,
    seed: int,
) -> TrainedPolicies:
    """独立 Q-learning：状态聚合为（是否有上一轮公开 LMP）两态。"""
    if not isinstance(episodes, int) or episodes <= 0:
        raise ValueError("episodes must be a positive integer")
    if not 0.0 < learning_rate <= 1.0:
        raise ValueError("learning_rate must be within (0, 1]")
    if not 0.0 <= epsilon <= 1.0:
        raise ValueError("epsilon must be within [0, 1]")
    rng = np.random.default_rng(int(seed))
    q_tables = {
        agent_id: np.zeros((2, env.action_dim)) for agent_id in env.agent_ids
    }
    env.reset()
    for _ in range(episodes):
        actions: dict[str, int] = {}
        states: dict[str, int] = {}
        for agent_id in env.agent_ids:
            state = 0
            states[agent_id] = state
            if rng.random() < epsilon:
                actions[agent_id] = int(rng.integers(env.action_dim))
            else:
                actions[agent_id] = int(np.argmax(q_tables[agent_id][state]))
        _, rewards, _, _ = env.step(actions)
        for agent_id in env.agent_ids:
            state = states[agent_id]
            q_tables[agent_id][state, actions[agent_id]] += learning_rate * (
                rewards[agent_id]
                - q_tables[agent_id][state, actions[agent_id]]
            )
        env.reset()
    return TrainedPolicies(
        q_tables=q_tables,
        greedy_actions={
            agent_id: int(np.argmax(table[0]))
            for agent_id, table in q_tables.items()
        },
        episodes=episodes,
        seed=int(seed),
    )


# ------------------------------------------------------------------ #
#  Policy guard
# ------------------------------------------------------------------ #

@dataclass(frozen=True, slots=True)
class PolicyGuardReport:
    """安全闸结果：任何 violation 都阻止晋级。"""

    passed: bool
    violations: tuple[str, ...]


def run_policy_guard(
    env: MARLBiddingEnv,
    trained: TrainedPolicies,
    *,
    holdout_load_mw: Mapping[str, float] | None = None,
    max_generalization_gap: float = 0.5,
    perturbation: float = 0.01,
    solver=None,
) -> PolicyGuardReport:
    """v5 §10.6 安全闸（最小完备集）。

    检查：
    1. 报价合法性（环境内 step 已强制，违规动作直接抛错 → 这里验证
       贪心动作索引在网格内）；
    2. 零出力虚增利润：任何 agent 出清出力为 0 时利润必须为 0；
    3. 微扰敏感性：负荷微扰后利润符号/量级不得崩坏（收益对模拟器
       参数微扰仍稳健，而非钻环境漏洞）；
    4. 泛化差：训练负荷与保留负荷下的利润相对差不得超过阈值。
    """
    violations: list[str] = []
    for agent_id, action in trained.greedy_actions.items():
        if not 0 <= action < env.action_dim:
            violations.append(f"illegal action for {agent_id!r}")

    _, rewards, _, info = env.step(trained.greedy_actions)
    dispatch: Mapping[str, float] = info["dispatch_mw"]  # type: ignore[assignment]
    for agent_id in env.agent_ids:
        if abs(dispatch[agent_id]) <= 1e-9 and abs(rewards[agent_id]) > 1e-6:
            violations.append(
                f"zero-dispatch profit for {agent_id!r}: simulator exploitation"
            )
    train_profit = float(sum(rewards.values()))

    if perturbation > 0.0 and np.isfinite(perturbation):
        perturbed_load = {
            bus_id: amount * (1.0 + float(perturbation))
            for bus_id, amount in env.load.items()
        }
        perturbed_env = MARLBiddingEnv(
            env.grid,
            perturbed_load,
            markup_grid=list(env.markup_grid),
            price_floor=env.price_floor,
            price_cap=env.price_cap,
            violation_penalty=env.violation_penalty,
            solver=solver,
        )
        perturbed_env.reset()
        _, perturbed_rewards, _, _ = perturbed_env.step(trained.greedy_actions)
        perturbed_profit = float(sum(perturbed_rewards.values()))
        if train_profit > 0.0 and perturbed_profit <= 0.0:
            violations.append(
                "profit vanishes under load perturbation: simulator exploitation"
            )

    if holdout_load_mw is not None:
        holdout_env = MARLBiddingEnv(
            env.grid,
            holdout_load_mw,
            markup_grid=list(env.markup_grid),
            price_floor=env.price_floor,
            price_cap=env.price_cap,
            violation_penalty=env.violation_penalty,
            solver=solver,
        )
        holdout_env.reset()
        _, holdout_rewards, _, _ = holdout_env.step(trained.greedy_actions)
        holdout_profit = float(sum(holdout_rewards.values()))
        if train_profit > 1e-9:
            gap = (train_profit - holdout_profit) / abs(train_profit)
            if gap > max_generalization_gap:
                violations.append(
                    f"generalization gap {gap:.3f} exceeds {max_generalization_gap}"
                )

    return PolicyGuardReport(passed=not violations, violations=tuple(violations))
