"""V6-0 场景准入门：失败或缺少关键历史证据不得静默进入候选优化。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ele_trading.domain.contracts import PositionState
from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.scenario.diagnostics import DiagnosticCheck, ScenarioDiagnostics
from ele_trading.trading.orchestrator import TradingOrchestrator
from ele_trading.trading.scenario_admission import (
    ScenarioAdmissionPolicy,
    ScenarioAdmissionRejected,
    ScenarioEvidenceTier,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
DECISION_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")
BESS = {
    "p_bcmax": 2.0,
    "p_bdmax": 2.0,
    "p_bceff": 0.95,
    "p_bdeff": 0.95,
    "socmin": 1.0,
    "socmax": 5.0,
    "socini": 3.0,
    "cap": 4.0,
}


def _diagnostics(*, historical_skipped: bool) -> ScenarioDiagnostics:
    checks = [
        DiagnosticCheck(
            name="weight_conservation",
            passed=True,
            value=1.0,
            detail="Σ probability = 1.000000000000",
        ),
        DiagnosticCheck(
            name="marginal_mean_consistency",
            passed=True,
            value=0.0,
            detail="within tolerance",
        ),
        DiagnosticCheck(
            name="marginal_quantile_consistency",
            passed=True,
            value=0.0,
            detail="within tolerance",
        ),
    ]
    history_detail = (
        "skipped: no historical reference"
        if historical_skipped
        else "within tolerance"
    )
    checks.extend(
        (
            DiagnosticCheck(
                name="correlation_preservation",
                passed=True,
                value=None if historical_skipped else 0.0,
                detail=history_detail,
            ),
            DiagnosticCheck(
                name="extreme_coverage",
                passed=True,
                value=None if historical_skipped else 0.01,
                detail=history_detail,
            ),
        )
    )
    return ScenarioDiagnostics(checks=tuple(checks))


def test_candidate_policy_rejects_skipped_historical_checks():
    policy = ScenarioAdmissionPolicy(
        evidence_tier=ScenarioEvidenceTier.REAL_CANDIDATE,
    )

    decision = policy.evaluate(_diagnostics(historical_skipped=True))

    assert not decision.admitted
    assert decision.status == "rejected"
    assert decision.failed_checks == (
        "correlation_preservation",
        "extreme_coverage",
    )


def test_trading_package_exports_scenario_admission_contracts():
    import ele_trading.trading as trading

    assert trading.ScenarioAdmissionPolicy is ScenarioAdmissionPolicy
    assert trading.ScenarioEvidenceTier is ScenarioEvidenceTier


def test_research_policy_records_explicit_degradation_for_skipped_history():
    policy = ScenarioAdmissionPolicy(
        evidence_tier=ScenarioEvidenceTier.RESEARCH,
    )

    decision = policy.evaluate(_diagnostics(historical_skipped=True))

    assert decision.admitted
    assert decision.status == "degraded"
    assert decision.degraded_checks == (
        "correlation_preservation",
        "extreme_coverage",
    )


def test_research_policy_explicitly_degrades_failed_diagnostics():
    policy = ScenarioAdmissionPolicy(
        evidence_tier=ScenarioEvidenceTier.RESEARCH,
    )
    diagnostics = ScenarioDiagnostics(
        checks=(
            DiagnosticCheck(
                name="marginal_quantile_consistency",
                passed=False,
                value=0.2,
                detail="outside tolerance",
            ),
        ),
    )

    decision = policy.evaluate(diagnostics)

    assert decision.admitted
    assert decision.status == "degraded"
    assert decision.degraded_checks == ("marginal_quantile_consistency",)


class _PositionProvider:
    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            source_version="position-v1",
        )


class _ForecastProvider:
    def forecast(self, request: ForecastRequest) -> ForecastResult:
        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        values = {
            "price": 300.0,
            "load": 3.0,
            "wind_power": 0.0,
            "pv_power": 0.0,
        }
        point = pd.Series(values[request.target], index=index)
        spread = 10.0 if request.target == "price" else 0.1
        return ForecastResult(
            request=request,
            point=point,
            quantiles={0.1: point - spread, 0.9: point + spread},
            unit="CNY/MWh" if request.target == "price" else "MWh/period",
            model_version=f"{request.target}-v1",
            feature_as_of=request.issue_time,
        )


def _orchestrator(*, policy: ScenarioAdmissionPolicy) -> TradingOrchestrator:
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    return TradingOrchestrator(
        data_provider=_PositionProvider(),
        forecast_provider=_ForecastProvider(),
        forecast_registry="scenario-admission-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v6-0",
        scenario_admission_policy=policy,
    )


def test_orchestrator_rejects_candidate_before_day_ahead_solve_without_history():
    orchestrator = _orchestrator(
        policy=ScenarioAdmissionPolicy(
            evidence_tier=ScenarioEvidenceTier.REAL_CANDIDATE,
        ),
    )

    with pytest.raises(ScenarioAdmissionRejected, match="correlation_preservation"):
        orchestrator.run(
            decision_time=DECISION_TIME,
            actual_load=np.full(4, 3.0),
            actual_price=np.full(4, 300.0),
            intraday_start=2,
        )


def test_orchestrator_records_research_degradation_for_each_decision_vintage():
    orchestrator = _orchestrator(
        policy=ScenarioAdmissionPolicy(
            evidence_tier=ScenarioEvidenceTier.RESEARCH,
        ),
    )

    result = orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.full(4, 300.0),
        intraday_start=2,
    )

    assert [item.stage for item in result.scenario_admissions] == [
        "day_ahead",
        "intraday",
    ]
    assert all(item.status == "degraded" for item in result.scenario_admissions)
    assert result.day_ahead_plan.decision_trace is not None
    assert result.day_ahead_plan.decision_trace.diagnostics[
        "scenario_admission.day_ahead.status"
    ] == "degraded"
    assert result.intraday_plan.schedule.decision_trace is not None
    assert result.intraday_plan.schedule.decision_trace.diagnostics[
        "scenario_admission.intraday.status"
    ] == "degraded"
