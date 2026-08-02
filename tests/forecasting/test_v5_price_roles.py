"""v5 V5-1：价格角色与多价格预测束契约。"""

from __future__ import annotations

import pandas as pd

from ele_trading.domain.contracts import MarketForecastBundle
from ele_trading.forecasting.contracts import ForecastRequest
from ele_trading.forecasting.price_forecast import PriceForecastModel
from ele_trading.forecasting.seasonal_naive_provider import (
    SeasonalNaiveTradingForecastProvider,
)
from ele_trading.markets.price_roles import PriceRole, normalize_price_role


ISSUE_TIME = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")


def test_price_role_normalizes_legacy_real_time_scope():
    assert normalize_price_role("real_time_reference") is PriceRole.REAL_TIME_SETTLEMENT
    assert normalize_price_role(PriceRole.SPREAD_DA_RT) is PriceRole.SPREAD_DA_RT


def test_forecast_request_normalizes_explicit_price_role_in_data():
    request = ForecastRequest(
        target="price",
        scope_type="market",
        scope_id="mengxi",
        horizon=2,
        frequency="15min",
        issue_time=ISSUE_TIME,
        data={"price_role": "real_time_reference"},
    )

    assert request.data["price_role"] == PriceRole.REAL_TIME_SETTLEMENT.value


def test_price_model_selects_history_by_canonical_price_role():
    index = pd.date_range(
        "2026-06-30 00:15",
        periods=96,
        freq="15min",
        tz="Asia/Shanghai",
    )
    model = PriceForecastModel(
        history_by_scope={
            PriceRole.DAY_AHEAD_REFERENCE.value: pd.Series(300.0, index=index),
            PriceRole.REAL_TIME_SETTLEMENT.value: pd.Series(450.0, index=index),
        }
    )
    request = ForecastRequest(
        target="price",
        scope_type="market",
        scope_id="mengxi",
        horizon=2,
        frequency="15min",
        issue_time=ISSUE_TIME,
        data={"price_role": PriceRole.REAL_TIME_SETTLEMENT.value},
    )

    result = model.forecast(request)

    assert result.point.tolist() == [450.0, 450.0]
    assert "price_role:real_time_settlement" in result.quality_flags


def test_market_forecast_bundle_preserves_primary_alias_and_role_mapping():
    primary = object()
    day_ahead = object()
    bundle = MarketForecastBundle(
        issue_time=ISSUE_TIME,
        price_forecast=primary,
        load_forecast=object(),
        wind_forecast=object(),
        pv_forecast=object(),
        price_forecasts={
            PriceRole.DAY_AHEAD_REFERENCE.value: day_ahead,
            PriceRole.REAL_TIME_SETTLEMENT.value: primary,
        },
    )

    assert bundle.price_forecast is primary
    assert bundle.get_price_forecast(PriceRole.DAY_AHEAD_REFERENCE.value) is day_ahead
    assert bundle.get_price_forecast(PriceRole.REAL_TIME_SETTLEMENT.value) is primary


def test_seasonal_naive_reads_price_column_by_role():
    history_index = pd.date_range(
        "2026-06-30 23:30",
        periods=2,
        freq="15min",
        tz="Asia/Shanghai",
    )
    history = pd.DataFrame(
        {
            "p_dayah": [100.0, 120.0],
            "p_real": [300.0, 360.0],
            "Q_real_load": [3.0, 4.0],
        },
        index=history_index,
    )
    provider = SeasonalNaiveTradingForecastProvider(
        history,
        feature_as_of=history_index[-1],
    )

    def forecast(role: PriceRole):
        return provider.forecast(
            ForecastRequest(
                target="price",
                scope_type="market",
                scope_id="single_settlement",
                horizon=2,
                frequency="15min",
                issue_time=ISSUE_TIME,
                quantiles=(0.1, 0.9),
                data={"price_role": role.value},
            )
        )

    day_ahead = forecast(PriceRole.DAY_AHEAD_REFERENCE)
    realtime = forecast(PriceRole.REAL_TIME_SETTLEMENT)
    spread = forecast(PriceRole.SPREAD_DA_RT)

    assert day_ahead.point.tolist() == [100.0, 120.0]
    assert realtime.point.tolist() == [300.0, 360.0]
    assert spread.point.tolist() == [200.0, 240.0]
    assert "price_role:spread_da_rt" in spread.quality_flags
