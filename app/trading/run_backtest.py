"""Walk-forward backtest driver for the active Mengxi single-settlement chain.

Demo/regression only (AGENTS.md data boundary): runs over the sample
``daily_sample_*.csv`` fixtures and writes a per-run report + manifest under
``results/trading/backtest/<run_id>/`` (v2 §8.3). Each decision day forecasts
from the prior observed day only, so no future information enters decisions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd

from _bootstrap import DATA_TRADING, MARKET_CONFIG_YAML, RESULTS_TRADING, SAMPLE_BESS

from ele_trading.scenario.joint_builder import build_joint_scenarios
from ele_trading.backtest.backtest import run_walk_forward_backtest
from ele_trading.markets.single_settlement.config_loader import load_market_config
from ele_trading.trading.orchestrator import TradingOrchestrator
from ele_trading.trading.demo_fixtures import (
    SampleTradingDataProvider,
    WalkForwardSeasonalNaiveProvider,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mengxi single-settlement walk-forward backtest"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="number of decision days to evaluate (default: all with a prior day)",
    )
    parser.add_argument("--intraday-start", type=int, default=48)
    parser.add_argument("--risk-weight", type=float, default=1.0)
    parser.add_argument("--scenario-count", type=int, default=None)
    parser.add_argument("--run-id", default="v2_baseline")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="output directory (default: results/trading/backtest/<run-id>)",
    )
    args = parser.parse_args()

    data_provider = SampleTradingDataProvider(DATA_TRADING)
    days = data_provider.available_days
    if len(days) < 2:
        raise ValueError("backtest requires at least two sample days")
    # Each decision day needs a strictly-prior observed day for its forecast.
    decision_days = list(days[1:])
    if args.days is not None:
        decision_days = decision_days[: args.days]
    if not decision_days:
        raise ValueError("no decision days with a prior day available")

    frames = {day: data_provider.frame_for_day(day) for day in days}
    calendar_data = {
        pd.Timestamp(day.date(), tz="Asia/Shanghai"): frames[day][
            ["Q_real_load", "p_real"]
        ].copy()
        for day in decision_days
    }

    config = load_market_config(MARKET_CONFIG_YAML)
    if args.scenario_count is not None:
        config.scenario_count = args.scenario_count
    config_version = hashlib.sha256(MARKET_CONFIG_YAML.read_bytes()).hexdigest()
    orchestrator = TradingOrchestrator(
        data_provider=data_provider,
        forecast_provider=WalkForwardSeasonalNaiveProvider(frames),
        forecast_registry="seasonal-naive-walkforward-v1",
        scenario_builder=build_joint_scenarios,
        config=config,
        bess=SAMPLE_BESS,
        config_version=config_version,
    )

    report = run_walk_forward_backtest(
        calendar_data,
        orchestrator=orchestrator,
        intraday_start=args.intraday_start,
        risk_aware_weight=args.risk_weight,
    )

    out_dir = args.out_dir or (RESULTS_TRADING / "backtest" / args.run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    report.to_csv(out_dir / "backtest_report.csv")
    shutil.copyfile(MARKET_CONFIG_YAML, out_dir / "single_settlement.yaml")
    manifest = {
        "run_id": args.run_id,
        "config_sha256": config_version,
        "n_decision_days": int(len(report)),
        "intraday_start": args.intraday_start,
        "risk_aware_weight": args.risk_weight,
        "scenario_count": config.scenario_count,
        "total_strategy_cost": float(report["strategy_cost"].sum()),
        "total_no_storage_cost": float(report["no_storage_cost"].sum()),
        "total_deterministic_cost": float(report["deterministic_cost"].sum()),
        "total_risk_aware_cost": float(report["risk_aware_cost"].sum()),
        "total_oracle_cost": float(report["oracle_cost"].sum()),
        "fallback_days": int(report["fallback_used"].sum()),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(
        "walk-forward backtest "
        f"days={len(report)} "
        f"strategy={manifest['total_strategy_cost']:.2f} "
        f"no_storage={manifest['total_no_storage_cost']:.2f} "
        f"deterministic={manifest['total_deterministic_cost']:.2f} "
        f"risk_aware={manifest['total_risk_aware_cost']:.2f} "
        f"oracle={manifest['total_oracle_cost']:.2f} "
        f"fallback_days={manifest['fallback_days']} "
        f"-> {out_dir}"
    )


if __name__ == "__main__":
    main()
