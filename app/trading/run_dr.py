"""Config-driven demand-response demo on a feasible operational plan."""

from __future__ import annotations

from _bootstrap import DATA_TRADING, MENGXI_YAML, SAMPLE_BESS

import numpy as np
import pandas as pd

from ele_trading.trading.sample_data import (
    SampleTradingDataProvider,
)
from ele_trading.forecasting.contracts import ForecastRequest
from ele_trading.forecasting.seasonal_naive_provider import (
    SeasonalNaiveTradingForecastProvider,
)
from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.day_ahead_coupled import (
    solve_day_ahead_operational,
)
from ele_trading.trading.dr_allocator import evaluate_dr_participation
from ele_trading.utils.log_util import logger


def main() -> None:
    config = load_market_config(MENGXI_YAML)
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
    plan = solve_day_ahead_operational(
        load,
        price,
        SAMPLE_BESS,
        config,
    )
    schedule = plan.resource_schedule
    adjustable = np.clip(
        SAMPLE_BESS["p_bdmax"]
        - schedule["p_discharge"].to_numpy(dtype=float),
        0.0,
        None,
    )
    decision = evaluate_dr_participation(
        adjustable,
        config,
        p_net_plan=schedule["p_net"].to_numpy(dtype=float),
        realtime_price_forecast=price,
    )
    logger.info(
        f"参与: {decision.participate}; 响应={decision.response_qty:.2f} MWh; "
        f"净裕度={decision.net_margin:.2f} 元"
    )
    if decision.reject_reason:
        logger.info(f"不参与原因: {decision.reject_reason}")


if __name__ == "__main__":
    main()
