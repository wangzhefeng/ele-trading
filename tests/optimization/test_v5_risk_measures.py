"""v5 V5-2：扩展尾部风险度量与线性辅助约束。"""

from __future__ import annotations

import pytest
from pulp import LpMinimize, LpProblem, LpVariable, value

from ele_trading.optimization.risk import (
    add_chance_constraint,
    add_worst_case_auxiliary,
    chance_violation_probability,
    entropic_value_at_risk,
    weighted_top_tail_mean,
    weighted_worst_case,
)
from ele_trading.optimization.solver import solve_pulp_model


LOSSES = {"s1": 10.0, "s2": 20.0, "s3": 100.0}
PROBABILITIES = {"s1": 0.6, "s2": 0.3, "s3": 0.1}


def test_discrete_tail_risk_metrics_have_expected_order():
    top_tail = weighted_top_tail_mean(
        LOSSES,
        PROBABILITIES,
        tail_mass=0.2,
    )
    evar = entropic_value_at_risk(
        LOSSES,
        PROBABILITIES,
        alpha=0.8,
    )

    assert top_tail == pytest.approx(60.0)
    assert weighted_worst_case(LOSSES) == 100.0
    assert top_tail <= evar
    assert evar <= weighted_worst_case(LOSSES) + 1e-6
    assert chance_violation_probability(
        {"s1": -1.0, "s2": 0.5, "s3": 2.0},
        PROBABILITIES,
    ) == pytest.approx(0.4)


def test_worst_case_auxiliary_bounds_every_scenario_loss():
    model = LpProblem("worst_case", LpMinimize)
    x = LpVariable("x", lowBound=0.0)
    losses = {"s1": 2.0 * x + 1.0, "s2": -x + 8.0}
    worst = add_worst_case_auxiliary(model, losses, prefix="wc")
    model += worst.expression

    solve_pulp_model(model)

    assert value(x) == pytest.approx(7.0 / 3.0, abs=1e-6)
    assert value(worst.expression) == pytest.approx(17.0 / 3.0, abs=1e-6)


def test_chance_constraint_limits_probability_of_positive_violation():
    model = LpProblem("chance", LpMinimize)
    x = LpVariable("x", lowBound=0.0, upBound=10.0)
    violations = {
        "s1": x - 8.0,
        "s2": x - 5.0,
        "s3": x - 2.0,
    }
    auxiliaries = add_chance_constraint(
        model,
        violations,
        PROBABILITIES,
        max_violation_probability=0.1,
        big_m=20.0,
        prefix="soc",
    )
    model += -x

    solve_pulp_model(model)

    assert value(x) == pytest.approx(5.0, abs=1e-6)
    assert sum(
        PROBABILITIES[scenario_id] * value(variable)
        for scenario_id, variable in auxiliaries.violated.items()
    ) <= 0.1 + 1e-8
