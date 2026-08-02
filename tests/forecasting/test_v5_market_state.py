"""v5 V5-1：可解释市场状态概率 provider。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.forecasting.market_state import (
    LogisticMarketStateProvider,
    MarketState,
    MarketStateFeatureSnapshot,
)


ISSUE_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")


def _training_features() -> pd.DataFrame:
    index = pd.date_range(
        "2026-06-29 00:00",
        periods=8,
        freq="15min",
        tz="Asia/Shanghai",
    )
    return pd.DataFrame(
        {
            "reserve_margin": [0.30, 0.25, 0.12, 0.10, 0.08, 0.06, 0.03, 0.01],
            "section_margin": [0.50, 0.45, 0.30, 0.25, 0.10, 0.08, 0.05, 0.01],
            "forced_outage_mw": [0.0, 0.0, 5.0, 6.0, 10.0, 12.0, 20.0, 25.0],
        },
        index=index,
    )


def _labels() -> pd.Series:
    return pd.Series(
        [
            "normal",
            "normal",
            "strained",
            "strained",
            "congested",
            "congested",
            "extreme",
            "extreme",
        ],
        index=_training_features().index,
    )


def test_logistic_state_provider_outputs_calibratable_probabilities():
    provider = LogisticMarketStateProvider(
        _training_features(),
        _labels(),
        feature_as_of=pd.Timestamp("2026-06-30 23:45", tz="Asia/Shanghai"),
        state_definition_version="physical-state-v1",
        model_kind="physical",
    )
    valid_times = pd.date_range(
        ISSUE_TIME + pd.Timedelta(minutes=15),
        periods=3,
        freq="15min",
    )
    snapshot = MarketStateFeatureSnapshot(
        as_of=ISSUE_TIME,
        version="physical-features-v1",
        frame=pd.DataFrame(
            {
                "reserve_margin": [0.28, 0.09, 0.02],
                "section_margin": [0.45, 0.12, 0.02],
                "forced_outage_mw": [0.0, 8.0, 22.0],
            },
            index=valid_times,
        ),
    )

    result = provider.forecast(ISSUE_TIME, snapshot)

    assert result.valid_time_index.equals(valid_times)
    assert tuple(result.probabilities.columns) == tuple(
        state.value for state in MarketState
    )
    assert result.probabilities.sum(axis=1).round(12).tolist() == [1.0, 1.0, 1.0]
    assert result.feature_as_of == ISSUE_TIME
    assert result.model_kind == "physical"


def test_physical_state_model_rejects_price_only_labels():
    features = pd.DataFrame(
        {"price": [100.0, 200.0, 300.0, 400.0]},
        index=pd.date_range(
            "2026-06-29",
            periods=4,
            freq="15min",
            tz="Asia/Shanghai",
        ),
    )
    labels = pd.Series(
        ["normal", "strained", "congested", "extreme"],
        index=features.index,
    )

    with pytest.raises(ValueError, match="price_regime_proxy"):
        LogisticMarketStateProvider(
            features,
            labels,
            feature_as_of=pd.Timestamp("2026-06-30", tz="Asia/Shanghai"),
            state_definition_version="bad-physical-v1",
            model_kind="physical",
        )


def test_state_forecast_rejects_future_feature_snapshot():
    provider = LogisticMarketStateProvider(
        _training_features(),
        _labels(),
        feature_as_of=pd.Timestamp("2026-06-30 23:45", tz="Asia/Shanghai"),
        state_definition_version="physical-state-v1",
        model_kind="physical",
    )
    snapshot = MarketStateFeatureSnapshot(
        as_of=ISSUE_TIME + pd.Timedelta(minutes=1),
        version="future-features",
        frame=_training_features().iloc[:2].copy(),
    )

    with pytest.raises(ValueError, match="later than issue_time"):
        provider.forecast(ISSUE_TIME, snapshot)
