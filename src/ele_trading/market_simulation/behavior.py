"""报价行为模型：logit 选择、经验加成校准与 ABM 出清（v5 §10.2/§10.3）。

- ``LogitMarkupPolicy``：在离散加成网格上做 softmax 选择；
  temperature → 0 收敛到 argmax，→ ∞ 收敛到均匀分布。
- ``empirical_markup_distribution``：从历史加成样本估计网格频率，
  作为行为校准的最小事实层（不虚构行为参数）。
- ``AgentBasedMarket``：每轮各策略 agent 在加成网格上选价 →
  报价替换机组边际成本 → 真实 SCED 出清 → 按 LMP 结算利润 →
  Q 值指数更新。环境状态只用当时可得信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from .bidding import OfferStack
from .grid.contracts import Generator, GridSnapshot
from .sced import SCEDResult, solve_sced


class LogitMarkupPolicy:
    """离散加成网格上的 logit（softmax）策略。"""

    def __init__(
        self,
        markup_grid: Sequence[float],
        *,
        temperature: float,
    ) -> None:
        grid = np.asarray(markup_grid, dtype=float)
        if grid.ndim != 1 or not len(grid) or not np.isfinite(grid).all():
            raise ValueError("markup_grid must be a finite 1-D vector")
        if (grid <= 0.0).any():
            raise ValueError("markups must be positive")
        if not np.isfinite(temperature) or temperature < 0.0:
            raise ValueError("temperature must be finite and non-negative")
        self.markup_grid = grid
        self.temperature = float(temperature)

    def probabilities(self, utilities: Sequence[float]) -> np.ndarray:
        values = np.asarray(utilities, dtype=float)
        if values.shape != self.markup_grid.shape or not np.isfinite(values).all():
            raise ValueError("utilities must align with markup_grid")
        if self.temperature <= 1e-12:
            probabilities = np.zeros_like(values)
            probabilities[int(np.argmax(values))] = 1.0
            return probabilities
        shifted = (values - values.max()) / self.temperature
        exp_values = np.exp(shifted)
        return exp_values / exp_values.sum()

    def choose(
        self,
        utilities: Sequence[float],
        rng: np.random.Generator,
    ) -> float:
        probabilities = self.probabilities(utilities)
        return float(rng.choice(self.markup_grid, p=probabilities))


def empirical_markup_distribution(
    samples: Sequence[float],
    markup_grid: Sequence[float],
) -> np.ndarray:
    """把历史加成样本归入最近网格点并归一化为频率分布。"""
    grid = np.asarray(markup_grid, dtype=float)
    values = np.asarray(samples, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("samples must be a finite 1-D vector")
    if grid.ndim != 1 or not len(grid) or not np.isfinite(grid).all():
        raise ValueError("markup_grid must be a finite 1-D vector")
    indices = np.argmin(np.abs(values[:, None] - grid[None, :]), axis=1)
    counts = np.bincount(indices, minlength=len(grid)).astype(float)
    return counts / counts.sum()


@dataclass(frozen=True, slots=True)
class ABMRoundResult:
    """单轮 ABM 出清记录。"""

    period: int
    markups: Mapping[str, float]
    offers: Mapping[str, OfferStack]
    clearing: SCEDResult
    profits: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class ABMResult:
    """多轮 ABM 运行结果。"""

    rounds: tuple[ABMRoundResult, ...]
    seed: int

    @property
    def lmp_series(self) -> Mapping[str, list[float]]:
        series: dict[str, list[float]] = {}
        for item in self.rounds:
            for bus_id, price in item.clearing.lmp.items():
                series.setdefault(bus_id, []).append(price)
        return series

    @property
    def markup_history(self) -> Mapping[str, list[float]]:
        history: dict[str, list[float]] = {}
        for item in self.rounds:
            for generator_id, markup in item.markups.items():
                history.setdefault(generator_id, []).append(markup)
        return history


class AgentBasedMarket:
    """策略 agent + 真实 SCED 出清的多轮市场仿真。

    状态只含 agent 当时可得信息（自身 Q 值、公开出清结果）；
    不允许访问其他参与者私有成本或未来出清结果。
    """

    def __init__(
        self,
        grid: GridSnapshot,
        *,
        markup_grid: Sequence[float],
        temperature: float,
        learning_rate: float = 0.3,
        price_cap: float | None = None,
        strategic_generator_ids: Sequence[str] | None = None,
    ) -> None:
        if not isinstance(grid, GridSnapshot):
            raise ValueError("grid must be a GridSnapshot")
        if not 0.0 < learning_rate <= 1.0:
            raise ValueError("learning_rate must be within (0, 1]")
        if price_cap is not None and (
            not np.isfinite(price_cap) or price_cap <= 0.0
        ):
            raise ValueError("price_cap must be finite and positive")
        self.grid = grid
        self.policy = LogitMarkupPolicy(markup_grid, temperature=temperature)
        self.learning_rate = float(learning_rate)
        self.price_cap = price_cap
        strategic = (
            tuple(strategic_generator_ids)
            if strategic_generator_ids is not None
            else tuple(grid.generator_ids)
        )
        unknown = set(strategic) - set(grid.generator_ids)
        if unknown:
            raise ValueError(
                "strategic_generator_ids references unknown generators: "
                + ", ".join(sorted(unknown))
            )
        self.strategic_generator_ids = strategic

    def _initial_utilities(self) -> dict[str, np.ndarray]:
        return {
            generator_id: np.zeros(len(self.policy.markup_grid))
            for generator_id in self.strategic_generator_ids
        }

    def run(
        self,
        load_mw: Mapping[str, float] | Sequence[Mapping[str, float]],
        *,
        periods: int,
        seed: int,
        solver=None,
    ) -> ABMResult:
        """运行多轮报价-出清-更新循环。"""
        if not isinstance(periods, int) or periods <= 0:
            raise ValueError("periods must be a positive integer")
        if not isinstance(seed, (int, np.integer)):
            raise ValueError("seed must be an integer")
        if isinstance(load_mw, Mapping):
            loads = [dict(load_mw)] * periods
        else:
            loads = [dict(item) for item in load_mw]
            if len(loads) != periods:
                raise ValueError("load sequence length must match periods")

        rng = np.random.default_rng(int(seed))
        utilities = self._initial_utilities()
        rounds: list[ABMRoundResult] = []
        for period in range(periods):
            markups: dict[str, float] = {}
            offers: dict[str, OfferStack] = {}
            offer_generators: list[Generator] = []
            for generator in self.grid.generators:
                gid = generator.generator_id
                if gid in utilities:
                    markup = self.policy.choose(utilities[gid], rng)
                else:
                    markup = 1.0
                markups[gid] = markup
                offer_price = generator.marginal_cost * markup
                if self.price_cap is not None:
                    offer_price = min(offer_price, self.price_cap)
                offers[gid] = OfferStack.flat(
                    gid,
                    capacity_mw=generator.p_max_mw,
                    price=offer_price,
                    rule_version=f"abm-seed-{seed}",
                )
                offer_generators.append(
                    Generator(
                        generator_id=gid,
                        bus_id=generator.bus_id,
                        p_min_mw=generator.p_min_mw,
                        p_max_mw=generator.p_max_mw,
                        ramp_up_mw=generator.ramp_up_mw,
                        ramp_down_mw=generator.ramp_down_mw,
                        marginal_cost=offer_price,
                    )
                )
            offer_grid = GridSnapshot(
                as_of=self.grid.as_of,
                version=f"{self.grid.version}:abm-{period}",
                buses=self.grid.buses,
                branches=self.grid.branches,
                generators=tuple(offer_generators),
                reserve_requirement_mw=self.grid.reserve_requirement_mw,
            )
            clearing = solve_sced(offer_grid, loads[period], solver=solver)
            profits = {
                generator.generator_id: (
                    clearing.lmp[generator.bus_id] - generator.marginal_cost
                )
                * clearing.dispatch_mw[generator.generator_id]
                for generator in self.grid.generators
            }
            # Q 值指数更新：只使用本轮公开出清与自身报价/利润
            for gid in self.strategic_generator_ids:
                index = int(
                    np.argmin(
                        np.abs(self.policy.markup_grid - markups[gid])
                    )
                )
                utilities[gid][index] += self.learning_rate * (
                    profits[gid] - utilities[gid][index]
                )
            rounds.append(
                ABMRoundResult(
                    period=period,
                    markups=markups,
                    offers=offers,
                    clearing=clearing,
                    profits=profits,
                )
            )
        return ABMResult(rounds=tuple(rounds), seed=int(seed))
