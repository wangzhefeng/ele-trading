"""V5-9 Task 13：结算后自动对账接线测试。"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest

from ele_trading.domain.contracts import BillingStatement, PositionState
from ele_trading.forecasting.contracts import ForecastRequest, ForecastResult
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.orchestrator import TradingOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_YAML = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
DECISION_TIME = cast(
    pd.Timestamp,
    pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai"),
)
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


class _DataProvider:
    def get_position_state(self, decision_time, valid_time_index):
        return PositionState(
            as_of=decision_time,
            q_long=pd.Series(1.0, index=valid_time_index),
            p_long=pd.Series(300.0, index=valid_time_index),
            source_version="position-v1",
        )


class _ForecastProvider:
    def forecast(self, request: ForecastRequest) -> ForecastResult:
        values = {
            "price": [200.0, 250.0, 500.0, 600.0],
            "load": [3.0, 3.0, 3.0, 3.0],
            "wind_power": [0.0, 0.0, 0.0, 0.0],
            "pv_power": [0.0, 0.0, 0.0, 0.0],
        }[request.target][: request.horizon]
        index = pd.date_range(
            request.issue_time + pd.Timedelta(minutes=15),
            periods=request.horizon,
            freq=request.frequency,
        )
        point = pd.Series(values, index=index)
        return ForecastResult(
            request=request,
            point=point,
            quantiles={0.1: point - 0.1, 0.9: point + 0.1},
            unit="CNY/MWh" if request.target == "price" else "MWh/period",
            model_version=f"{request.target}-v1",
            feature_as_of=request.issue_time,
        )


def _run(*, mode=None, billing_statement=None):
    config = SINGLE_SETTLEMENT_MODE.load_config(CONFIG_YAML)
    config.scenario.scenario_count = 2
    orchestrator = TradingOrchestrator(
        data_provider=_DataProvider(),
        forecast_provider=_ForecastProvider(),
        forecast_registry="registry-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=mode or SINGLE_SETTLEMENT_MODE,
        config=config,
        bess=BESS,
        config_version="config-v1",
    )
    return orchestrator.run(
        decision_time=DECISION_TIME,
        actual_load=np.full(4, 3.0),
        actual_price=np.array([210.0, 240.0, 490.0, 610.0]),
        intraday_start=2,
        billing_statement=billing_statement,
    )


def _statement_from_report(report, **overrides) -> BillingStatement:
    lines = {
        "energy_cost": report.energy_cost,
        "contract_difference": report.contract_difference,
        "long_recovery": report.long_recovery,
        "dr_adjustment": report.dr_adjustment,
        "degradation_cost": report.degradation_cost,
        "execution_adjustment": report.execution_adjustment,
    }
    values = {
        "statement_version": "stmt-2026-07-01",
        "lines": lines,
        "confirmed": True,
        "tolerance": 0.01,
    }
    values.update(overrides)
    return BillingStatement(**values)


def test_pipeline_without_billing_statement_has_no_reconciliation():
    result = _run()
    assert result.reconciliation_report is None


def test_matching_confirmed_statement_passes_reconciliation():
    report = _run().settlement_report
    result = _run(billing_statement=_statement_from_report(report))
    reconciliation = result.reconciliation_report
    assert reconciliation is not None
    assert reconciliation.passed
    assert reconciliation.confirmed
    assert reconciliation.differences == ()


def test_unconfirmed_statement_never_passes():
    report = _run().settlement_report
    result = _run(
        billing_statement=_statement_from_report(report, confirmed=False),
    )
    reconciliation = result.reconciliation_report
    assert reconciliation is not None
    assert not reconciliation.passed


def test_mismatched_billed_amount_is_reported_not_hidden():
    report = _run().settlement_report
    lines = {
        "energy_cost": report.energy_cost + 50.0,
        "contract_difference": report.contract_difference,
        "long_recovery": report.long_recovery,
        "dr_adjustment": report.dr_adjustment,
        "degradation_cost": report.degradation_cost,
        "execution_adjustment": report.execution_adjustment,
    }
    result = _run(
        billing_statement=_statement_from_report(report, lines=lines),
    )
    reconciliation = result.reconciliation_report
    assert reconciliation is not None
    assert not reconciliation.passed
    assert any(d.line_item == "energy_cost" for d in reconciliation.differences)


def test_mode_without_reconciliation_support_fails_explicitly():
    class _BareMode:
        name = "bare"
        settlement = SINGLE_SETTLEMENT_MODE.settlement
        price_roles = SINGLE_SETTLEMENT_MODE.price_roles
        day_ahead_price_role = SINGLE_SETTLEMENT_MODE.day_ahead_price_role
        intraday_price_role = SINGLE_SETTLEMENT_MODE.intraday_price_role

        def load_config(self, path):
            return SINGLE_SETTLEMENT_MODE.load_config(path)

    report = _run().settlement_report
    with pytest.raises(ValueError, match="reconciliation"):
        _run(
            mode=_BareMode(),
            billing_statement=_statement_from_report(report),
        )
