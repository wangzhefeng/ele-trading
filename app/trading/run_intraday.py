"""Thin demo entrypoint for the Mengxi intraday rolling plan."""

from __future__ import annotations

import argparse
import hashlib

import pandas as pd

from _bootstrap import DATA_TRADING, MARKET_CONFIG_YAML, SAMPLE_BESS

from ele_trading.forecasting.seasonal_naive_provider import (
    SeasonalNaiveTradingForecastProvider,
)
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.markets.single_settlement.config_loader import load_market_config
from ele_trading.trading.orchestrator import TradingOrchestrator
from ele_trading.trading.demo_fixtures import SampleTradingDataProvider


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mengxi intraday rolling plan demo"
    )
    parser.add_argument("--scenario-count", type=int, default=None)
    parser.add_argument("--intraday-start", type=int, default=48)
    args = parser.parse_args()

    data_provider = SampleTradingDataProvider(DATA_TRADING)
    days = data_provider.available_days
    if len(days) < 2:
        raise ValueError("intraday demo requires at least two sample days")
    day = days[-1]
    history_day = days[-2]
    decision_time = pd.Timestamp(day.date(), tz="Asia/Shanghai")
    config = load_market_config(MARKET_CONFIG_YAML)
    if args.scenario_count is not None:
        config.scenario_count = args.scenario_count
    forecast_provider = SeasonalNaiveTradingForecastProvider(
        data_provider.frame_for_day(history_day),
        feature_as_of=decision_time - pd.Timedelta(minutes=15),
    )
    orchestrator = TradingOrchestrator(
        data_provider=data_provider,
        forecast_provider=forecast_provider,
        forecast_registry="seasonal-naive-demo-v1",
        scenario_builder=build_joint_scenarios,
        config=config,
        bess=SAMPLE_BESS,
        config_version=hashlib.sha256(
            MARKET_CONFIG_YAML.read_bytes()
        ).hexdigest(),
    )
    actuals = data_provider.frame_for_day(day)
    result = orchestrator.run(
        decision_time=decision_time,
        actual_load=actuals["Q_real_load"].to_numpy(dtype=float),
        actual_price=actuals["p_real"].to_numpy(dtype=float),
        intraday_start=args.intraday_start,
    )
    plan = result.intraday_plan
    executed = len(plan.executed_prefix)
    remaining = len(plan.schedule.resource_schedule)
    adjustment = plan.adjustment
    reasons = "; ".join(adjustment.reasons) if adjustment.reasons else "-"
    print(
        "intraday rolling plan "
        f"day={day.date()} fallback={plan.fallback_used} "
        f"executed_prefix={executed} remaining={remaining} "
        f"cost_delta={adjustment.expected_cost_delta:.2f}"
    )
    print(f"reasons: {reasons}")


if __name__ == "__main__":
    main()
