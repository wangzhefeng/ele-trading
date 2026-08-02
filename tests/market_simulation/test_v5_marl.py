"""v5 V5-6：MARL 环境、训练与 policy guard。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ele_trading.market_simulation.grid.contracts import (
    Bus,
    Generator,
    GridSnapshot,
)
from ele_trading.market_simulation.marl import (
    MARLBiddingEnv,
    TrainedPolicies,
    run_policy_guard,
    train_independent_q,
)

AS_OF = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")


def _monopoly_grid() -> GridSnapshot:
    return GridSnapshot(
        as_of=AS_OF,
        version="marl-monopoly",
        buses=(Bus("b1"),),
        branches=(),
        generators=(
            Generator(
                generator_id="monopolist",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=100.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
        ),
    )


def test_env_observation_excludes_opponent_private_information():
    grid = GridSnapshot(
        as_of=AS_OF,
        version="marl-duo",
        buses=(Bus("b1"),),
        branches=(),
        generators=(
            Generator(
                generator_id="a",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=50.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=100.0,
            ),
            Generator(
                generator_id="b",
                bus_id="b1",
                p_min_mw=0.0,
                p_max_mw=50.0,
                ramp_up_mw=0.0,
                ramp_down_mw=0.0,
                marginal_cost=200.0,
            ),
        ),
    )
    env = MARLBiddingEnv(grid, {"b1": 60.0}, markup_grid=[1.0, 2.0])
    observations = env.reset()

    assert observations["a"].own_marginal_cost == 100.0
    assert observations["a"].last_public_lmp is None
    # observation 数据类字段不含任何对手私有字段
    public_fields = set(type(observations["a"]).__dataclass_fields__)
    assert not any("opponent" in name or "other" in name for name in public_fields)

    _, rewards, done, info = env.step({"a": 0, "b": 0})
    assert done
    assert info["lmp"]["b1"] == pytest.approx(200.0, abs=1e-4)
    assert rewards["a"] == pytest.approx((200.0 - 100.0) * 50.0, abs=1e-4)
    assert rewards["b"] == pytest.approx(0.0, abs=1e-4)
    # 上一轮公开 LMP 进入下一步 observation
    next_observations = env._observations()
    assert next_observations["a"].last_public_lmp == pytest.approx(200.0)

    with pytest.raises(ValueError, match="out of range"):
        env.step({"a": 99, "b": 0})
    with pytest.raises(ValueError, match="cover every agent"):
        env.step({"a": 0})


def test_monopolist_q_learning_picks_high_markup_and_guard_passes():
    env = MARLBiddingEnv(
        _monopoly_grid(),
        {"b1": 50.0},
        markup_grid=[1.0, 1.5, 2.0, 3.0],
        price_cap=400.0,
    )
    trained = train_independent_q(
        env,
        episodes=60,
        learning_rate=0.5,
        epsilon=0.3,
        seed=5,
    )

    best_markup = env.markup_grid[trained.greedy_actions["monopolist"]]
    assert best_markup == pytest.approx(3.0)

    _, rewards, _, info = env.step(trained.greedy_actions)
    assert info["lmp"]["b1"] == pytest.approx(300.0, abs=1e-4)
    assert rewards["monopolist"] > 0.0

    report = run_policy_guard(
        env,
        trained,
        holdout_load_mw={"b1": 48.0},
    )
    assert report.passed, report.violations


def test_guard_flags_generalization_gap():
    env = MARLBiddingEnv(
        _monopoly_grid(),
        {"b1": 50.0},
        markup_grid=[1.0, 3.0],
        price_cap=400.0,
    )
    trained = train_independent_q(
        env, episodes=30, learning_rate=0.5, epsilon=0.2, seed=9
    )
    # 保留环境负荷过低：报价封顶 400 时利润结构变化，制造泛化差
    report = run_policy_guard(
        env,
        trained,
        holdout_load_mw={"b1": 1.0},
        max_generalization_gap=0.3,
    )
    assert not report.passed
    assert any("generalization gap" in item for item in report.violations)


def test_guard_flags_illegal_action_and_zero_dispatch_profit():
    env = MARLBiddingEnv(
        _monopoly_grid(),
        {"b1": 50.0},
        markup_grid=[1.0, 2.0],
    )
    illegal = TrainedPolicies(
        q_tables={"monopolist": np.zeros((2, 2))},
        greedy_actions={"monopolist": 7},
        episodes=1,
        seed=1,
    )
    with pytest.raises(ValueError, match="out of range"):
        run_policy_guard(env, illegal)

    # 合法动作下 guard 通过，无零出力虚增利润
    legal = TrainedPolicies(
        q_tables={"monopolist": np.zeros((2, 2))},
        greedy_actions={"monopolist": 0},
        episodes=1,
        seed=1,
    )
    report = run_policy_guard(env, legal)
    assert report.passed, report.violations


def test_training_reproducible_with_same_seed():
    env = MARLBiddingEnv(
        _monopoly_grid(),
        {"b1": 50.0},
        markup_grid=[1.0, 1.5, 2.0],
        price_cap=400.0,
    )
    first = train_independent_q(
        env, episodes=20, learning_rate=0.5, epsilon=0.5, seed=13
    )
    second = train_independent_q(
        env, episodes=20, learning_rate=0.5, epsilon=0.5, seed=13
    )
    assert first.greedy_actions == second.greedy_actions
    for agent_id in first.q_tables:
        assert np.array_equal(
            first.q_tables[agent_id], second.q_tables[agent_id]
        )
