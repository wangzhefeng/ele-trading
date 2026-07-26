"""Thin demo entrypoint for the Mengxi day-ahead operational plan."""

from __future__ import annotations

import argparse
import hashlib

import pandas as pd

from _bootstrap import DATA_TRADING, MENGXI_YAML, SAMPLE_BESS

from ele_trading.forecasting.seasonal_naive_provider import (
    SeasonalNaiveTradingForecastProvider,
)
from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.orchestrator import TradingOrchestrator
from ele_trading.trading.sample_data import SampleTradingDataProvider


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mengxi day-ahead operational plan demo"
    )
    parser.add_argument("--scenario-count", type=int, default=None)
    parser.add_argument("--intraday-start", type=int, default=48)
    args = parser.parse_args()

    data_provider = SampleTradingDataProvider(DATA_TRADING)
    days = data_provider.available_days
    if len(days) < 2:
        raise ValueError("day-ahead demo requires at least two sample days")
    day = days[-1]
    history_day = days[-2]
    decision_time = pd.Timestamp(day.date(), tz="Asia/Shanghai")
    config = load_market_config(MENGXI_YAML)
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
            MENGXI_YAML.read_bytes()
        ).hexdigest(),
    )
    actuals = data_provider.frame_for_day(day)
    result = orchestrator.run(
        decision_time=decision_time,
        actual_load=actuals["Q_real_load"].to_numpy(dtype=float),
        actual_price=actuals["p_real"].to_numpy(dtype=float),
        intraday_start=args.intraday_start,
    )
    plan = result.day_ahead_plan
    soc = plan.soc.to_numpy(dtype=float)
    schedule = plan.resource_schedule
    throughput = float(
        (schedule["p_charge"] + schedule["p_discharge"]).sum() * config.dt
    )
    trace = plan.decision_trace
    status = trace.solver_status if trace is not None else "unknown"
    components = (
        ", ".join(f"{k}={v:.2f}" for k, v in trace.objective_components.items())
        if trace is not None and trace.objective_components
        else "-"
    )
    print(
        "day-ahead operational plan "
        f"day={day.date()} status={status} "
        f"expected_cost={plan.expected_cost:.2f} "
        f"expected_risk={plan.expected_risk:.2f} "
        f"soc_min={soc.min():.2f} soc_max={soc.max():.2f} "
        f"throughput={throughput:.2f}"
    )
    print(f"objective_components: {components}")


if __name__ == "__main__":
    main()
