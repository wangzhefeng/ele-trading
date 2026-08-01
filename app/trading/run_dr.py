"""Demand-response joint-optimization demo via the day-ahead operational solver.

Enables ``dr_enabled`` in the market config and shows the two-pass solve
result: baseline discharge → DR commitment → incremental discharge with
compensation.
"""

from __future__ import annotations

from _bootstrap import DATA_TRADING, MARKET_CONFIG_YAML, SAMPLE_BESS

import numpy as np
import pandas as pd

from ele_trading.trading.demo_fixtures import (
    SampleTradingDataProvider,
)
from ele_trading.forecasting.contracts import ForecastRequest
from ele_trading.forecasting.seasonal_naive_provider import (
    SeasonalNaiveTradingForecastProvider,
)
from ele_trading.markets.single_settlement.config_loader import load_market_config
from ele_trading.operations.day_ahead_coupled import (
    solve_day_ahead_operational,
)
from ele_trading.markets.single_settlement.settlement import compute_dr_settlement
from ele_trading.utils.log_util import logger


def main() -> None:
    config = load_market_config(MARKET_CONFIG_YAML)
    config.dr_enabled = True
    data_provider = SampleTradingDataProvider(DATA_TRADING)
    history_day, day = data_provider.available_days[-2:]
    decision_time = pd.Timestamp(day.date(), tz="Asia/Shanghai")
    forecast_provider = SeasonalNaiveTradingForecastProvider(
        data_provider.frame_for_day(history_day),
        feature_as_of=decision_time - pd.Timedelta(minutes=15),
    )
    price = forecast_provider.forecast(
        ForecastRequest(
            target="price",
            scope_type="market",
            scope_id=config.market_name,
            horizon=96,
            frequency="15min",
            issue_time=decision_time,
            quantiles=(0.1, 0.9),
        )
    ).point.to_numpy(dtype=float)
    load = forecast_provider.forecast(
        ForecastRequest(
            target="load",
            scope_type="market",
            scope_id=config.market_name,
            horizon=96,
            frequency="15min",
            issue_time=decision_time,
            quantiles=(0.1, 0.9),
        )
    ).point.to_numpy(dtype=float)

    plan = solve_day_ahead_operational(load, price, SAMPLE_BESS, config)
    commitment = plan.dr_commitment

    if commitment is None or not commitment.participate:
        logger.info("DR 不参与")
        if commitment and commitment.reject_reason:
            logger.info(f"原因: {commitment.reject_reason}")
        return

    # 模拟履约结算（假设全额履约）
    w_start, w_end = commitment.window
    window_discharge = float(
        plan.resource_schedule["p_discharge"]
        .iloc[w_start:w_end]
        .sum()
        * config.dt
    )
    dr_adj, compensation, penalty = compute_dr_settlement(
        committed_qty=commitment.committed_qty,
        executed_window_discharge_mwh=window_discharge,
        baseline_qty=commitment.baseline_qty,
        config=config,
    )

    logger.info(
        f"DR 参与: 申报={commitment.committed_qty:.3f} MWh; "
        f"基线 Q0={commitment.baseline_qty:.3f} MWh; "
        f"预期增量={commitment.expected_incremental:.3f} MWh; "
        f"补偿={compensation:.2f} 元; 罚金={penalty:.2f} 元"
    )


if __name__ == "__main__":
    main()
