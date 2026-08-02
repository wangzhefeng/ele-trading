"""v5 V5-3（§11.2）：Level 2 温度耦合电池退化。"""

from __future__ import annotations

import pytest
from pulp import LpMinimize, LpProblem, value

from ele_trading.optimization.bess_model import BESSConfig, add_bess_constraints
from ele_trading.optimization.degradation import (
    TemperatureDegradationParameters,
    add_level1_degradation,
    add_level2_degradation,
    select_degradation_level,
)
from ele_trading.optimization.objectives import arbitrage_gross_revenue
from ele_trading.optimization.solver import solve_pulp_model


PARAMS = TemperatureDegradationParameters(
    calendar_cost_per_hour_ref=1.0,
    cycle_cost_per_mwh_ref=10.0,
    reference_temperature_c=25.0,
    calendar_temperature_coeff=0.04,
    cycle_temperature_coeff=0.02,
)


def _build_dispatch_model(temperature_c: list[float], deg_kind: str):
    steps = tuple(range(len(temperature_c)))
    model = LpProblem("deg", LpMinimize)
    variables = add_bess_constraints(
        model,
        steps,
        BESSConfig(
            soc0=2.0,
            soc_min=1.0,
            soc_max=5.0,
            p_ch_max=2.0,
            p_dis_max=2.0,
            eta_ch=0.95,
            eta_dis=0.95,
            dt=1.0,
        ),
        net_load={step: 0.0 for step in steps},
        prefix="bess",
    )
    if deg_kind == "level1":
        degradation = add_level1_degradation(
            model,
            variables,
            steps,
            calendar_cost_per_hour=PARAMS.calendar_cost_per_hour_ref,
            cycle_cost_per_mwh=PARAMS.cycle_cost_per_mwh_ref,
            soc0=2.0,
            soc_max=5.0,
            dt=1.0,
        )
    else:
        degradation = add_level2_degradation(
            model,
            variables,
            steps,
            temperature_c=temperature_c,
            parameters=PARAMS,
            soc0=2.0,
            soc_max=5.0,
            dt=1.0,
        )
    return model, variables, degradation


def test_level2_matches_level1_at_reference_temperature():
    temperature = [25.0, 25.0, 25.0]
    _, variables1, level1 = _build_dispatch_model(temperature, "level1")
    _, variables2, level2 = _build_dispatch_model(temperature, "level2")

    # 参考温度下 Level 2 退化为 Level 1：同一 SOC 轨迹下两表达式值一致
    steps = tuple(range(len(temperature)))
    model1 = LpProblem("l1", LpMinimize)
    model1 += level1.expression
    for step in steps:
        model1 += variables1.soc[step] >= 4.0
    solve_pulp_model(model1)

    model2 = LpProblem("l2", LpMinimize)
    model2 += level2.expression
    for step in steps:
        model2 += variables2.soc[step] >= 4.0
    solve_pulp_model(model2)

    assert value(model2.objective) == pytest.approx(
        value(model1.objective), abs=1e-6
    )


def test_level2_hot_temperature_increases_marginal_degradation_cost():
    steps = tuple(range(2))
    _, variables_hot, deg_hot = _build_dispatch_model([45.0, 45.0], "level2")
    _, variables_cold, deg_cold = _build_dispatch_model([25.0, 25.0], "level2")

    # 高温下循环退化系数更高：固定同一 SOC 摆动，成本更高
    model_hot = LpProblem("hot", LpMinimize)
    model_hot += deg_hot.expression
    model_hot += variables_hot.soc[steps[0]] >= 4.0
    model_hot += variables_hot.soc[steps[1]] >= 4.0
    solve_pulp_model(model_hot)

    model_cold = LpProblem("cold", LpMinimize)
    model_cold += deg_cold.expression
    model_cold += variables_cold.soc[steps[0]] >= 4.0
    model_cold += variables_cold.soc[steps[1]] >= 4.0
    solve_pulp_model(model_cold)

    assert value(model_hot.objective) > value(model_cold.objective)


def test_level2_coefficients_never_negative_in_extreme_cold():
    steps = (0, 1)
    _, variables_cold, deg_cold = _build_dispatch_model([-40.0, -40.0], "level2")
    model_cold = LpProblem("cold-floor", LpMinimize)
    model_cold += deg_cold.expression
    for step in steps:
        model_cold += variables_cold.soc[step] >= 4.0
    solve_pulp_model(model_cold)
    cold_cost = value(model_cold.objective) or 0.0
    assert cold_cost == pytest.approx(0.0, abs=1e-9)
    assert cold_cost >= 0.0

    _, variables_ref, deg_ref = _build_dispatch_model([25.0, 25.0], "level2")
    model_ref = LpProblem("ref", LpMinimize)
    model_ref += deg_ref.expression
    for step in steps:
        model_ref += variables_ref.soc[step] >= 4.0
    solve_pulp_model(model_ref)
    assert (value(model_ref.objective) or 0.0) > 0.0


def test_level2_rejects_misaligned_temperature():
    model = LpProblem("mis", LpMinimize)
    variables = add_bess_constraints(
        model,
        (0, 1),
        BESSConfig(
            soc0=2.0, soc_min=1.0, soc_max=5.0,
            p_ch_max=2.0, p_dis_max=2.0,
            eta_ch=0.95, eta_dis=0.95, dt=1.0,
        ),
        net_load={0: 0.0, 1: 0.0},
        prefix="mis",
    )
    with pytest.raises(ValueError, match="temperature_c"):
        add_level2_degradation(
            model,
            variables,
            (0, 1),
            temperature_c=[25.0],
            parameters=PARAMS,
            soc0=2.0,
            soc_max=5.0,
            dt=1.0,
        )


def test_select_degradation_level_falls_back_without_temperature():
    assert select_degradation_level(
        requested="level2", temperature_available=True
    ) == "level2"
    assert select_degradation_level(
        requested="level2", temperature_available=False
    ) == "level1"
    assert select_degradation_level(
        requested="level1", temperature_available=True
    ) == "level1"
    with pytest.raises(ValueError, match="requested"):
        select_degradation_level(
            requested="level9", temperature_available=True
        )


def test_high_temperature_reduces_arbitrage_throughput():
    prices = [50.0] * 4 + [500.0] * 4
    horizon = len(prices)

    def solve_with_temperature(temperature_c: float) -> float:
        steps = tuple(range(horizon))
        model = LpProblem("arb", LpMinimize)
        variables = add_bess_constraints(
            model,
            steps,
            BESSConfig(
                soc0=3.0, soc_min=1.0, soc_max=5.0,
                p_ch_max=2.0, p_dis_max=2.0,
                eta_ch=0.95, eta_dis=0.95, dt=1.0,
                terminal_soc=3.0,
            ),
            net_load={step: 0.0 for step in steps},
            prefix="bess",
        )
        degradation = add_level2_degradation(
            model,
            variables,
            steps,
            temperature_c=[temperature_c] * horizon,
            parameters=PARAMS,
            soc0=3.0,
            soc_max=5.0,
            dt=1.0,
        )
        model += degradation.expression - arbitrage_gross_revenue(
            variables, steps, prices, dt=1.0
        )
        solve_pulp_model(model)
        return sum(
            value(variables.p_discharge[step]) + value(variables.p_charge[step])
            for step in steps
        )

    throughput_cold = solve_with_temperature(20.0)
    throughput_hot = solve_with_temperature(45.0)
    assert throughput_hot <= throughput_cold + 1e-6
