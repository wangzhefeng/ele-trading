"""Thin demo entrypoint for the active Mengxi single-settlement pipeline."""

from __future__ import annotations

import argparse
import hashlib

from _bootstrap import DATA_TRADING, MARKET_CONFIG_YAML, SAMPLE_BESS

from ele_trading.trading.demo_fixtures import (
    SampleTradingDataProvider,
)
from ele_trading.forecasting.seasonal_naive_provider import (
    SeasonalNaiveTradingForecastProvider,
)
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.markets.single_settlement.mode import SINGLE_SETTLEMENT_MODE
from ele_trading.trading.orchestrator import TradingOrchestrator

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mengxi single-settlement pipeline demo"
    )
    parser.add_argument("--scenario-count", type=int, default=None)
    parser.add_argument("--intraday-start", type=int, default=48)
    args = parser.parse_args()

    data_provider = SampleTradingDataProvider(DATA_TRADING)
    days = data_provider.available_days
    if len(days) < 2:
        raise ValueError("pipeline demo requires at least two sample days")
    day = days[-1]
    history_day = days[-2]
    decision_time = pd.Timestamp(
        day.date(),
        tz="Asia/Shanghai",
    )
    config = SINGLE_SETTLEMENT_MODE.load_config(MARKET_CONFIG_YAML)
    if args.scenario_count is not None:
        config.scenario.scenario_count = args.scenario_count
    forecast_provider = SeasonalNaiveTradingForecastProvider(
        data_provider.frame_for_day(history_day),
        feature_as_of=decision_time - pd.Timedelta(minutes=15),
    )
    orchestrator = TradingOrchestrator(
        data_provider=data_provider,
        forecast_provider=forecast_provider,
        forecast_registry="seasonal-naive-demo-v1",
        scenario_builder=build_joint_scenarios,
        market_mode=SINGLE_SETTLEMENT_MODE,
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
    report = result.settlement_report
    print(
        "single-settlement pipeline "
        f"day={day.date()} total={report.total_cost:.2f} "
        f"baseline={report.baseline_cost:.2f} "
        f"delta={report.delta_cost:.2f} "
        f"fallback={result.intraday_plan.fallback_used}"
    )


if __name__ == "__main__":
    main()
