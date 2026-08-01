"""中长期仓位规划入口（v1.3 §8.1）：年度占比与分月分解。"""

from __future__ import annotations

from _bootstrap import MARKET_CONFIG_YAML, load_daily_samples

import numpy as np
import pandas as pd

from ele_trading.markets.single_settlement.config_loader import load_market_config
from ele_trading.positions.mid_long_planner import plan_mid_long_position
from ele_trading.utils.log_util import logger


def main() -> None:
    config = load_market_config(MARKET_CONFIG_YAML)

    # 由 30 天样例外推 12 个月负荷/价格水平（demo 口径，生产接入 ForecastProvider）
    calendar = load_daily_samples()
    month_load = np.mean([df["Q_real_load"].sum() for df in calendar.values()]) * 30
    p_spot = np.mean([df["p_real"].mean() for df in calendar.values()])
    p_long = np.mean([df["p_long"].mean() for df in calendar.values()])

    months = pd.period_range("2026-08", periods=12, freq="M")
    seasonal = 1.0 + 0.15 * np.sin(np.arange(12) / 12 * 2 * np.pi)  # 夏冬翘尾
    q_load_forecast = pd.Series(month_load * seasonal, index=months)
    p_long_forecast = pd.Series(np.full(12, p_long), index=months)
    p_spot_forecast = pd.Series(p_spot * (1.0 + 0.1 * np.sin(np.arange(12) / 12 * 2 * np.pi)), index=months)
    budget = float(np.sum(q_load_forecast * p_long_forecast) * 1.2)

    plan = plan_mid_long_position(q_load_forecast, p_long_forecast, p_spot_forecast, budget, config)

    logger.info("=== 中长期仓位规划 ===")
    logger.info(
        f"α_long={plan.alpha_long:.3f}, α_real={plan.alpha_real:.3f}"
    )
    logger.info(f"价格带 [{plan.price_band[0]:.1f}, {plan.price_band[1]:.1f}] 元/MWh")
    logger.info(f"预计成本 {plan.expected_cost:,.0f} 元, 预算占用 {plan.budget_used:.1%}, 需求满足度 {plan.coverage:.1%}")
    logger.info("分月中长期持仓 (MWh):")
    for month, qty in plan.q_long_monthly.items():
        logger.info(f"  {month}: {qty:,.0f}")


if __name__ == "__main__":
    main()
