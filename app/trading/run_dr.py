"""需求响应入口（v1.3 §9）：参与决策 + 机会成本实算 + 履约锁定演示。"""

from __future__ import annotations

from _bootstrap import MENGXI_YAML, SAMPLE_BESS, load_daily_samples

import numpy as np

from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.day_ahead_coupled import solve_day_ahead_coupled
from ele_trading.trading.dr_allocator import evaluate_dr_participation
from ele_trading.trading.noisy_backcast import generate_noisy_forecast
from ele_trading.utils.log_util import logger

DR_COMPENSATION = 2000.0  # 元/MWh，邀约型削峰补偿上限量级（v1.3 §9 调研值）
DR_WINDOW = (72, 80)  # 晚高峰 18:00–20:00


def main() -> None:
    config = load_market_config(MENGXI_YAML)
    calendar = load_daily_samples()
    day = sorted(calendar.keys())[-1]
    daily = calendar[day]
    logger.info(f"=== 需求响应评估: {day:%Y-%m-%d}, 窗口={DR_WINDOW}, 补偿={DR_COMPENSATION} 元/MWh ===")

    # 日前计划（实算机会成本的输入）
    p_dayah_pre = generate_noisy_forecast(daily["p_dayah"].values, config.sca_price, seed=1)
    p_real_pre = generate_noisy_forecast(daily["p_real"].values, config.sca_price, seed=2)
    q_load_pre = generate_noisy_forecast(daily["Q_real_load"].values, config.sca_power, seed=3)
    plan_da = solve_day_ahead_coupled(q_load_pre, p_dayah_pre, p_real_pre, SAMPLE_BESS, config)

    # 可调容量：放电方向剩余可调 = p_bdmax - 计划放电（向上响应=多放电）
    adjustable = np.clip(SAMPLE_BESS["p_bdmax"] - plan_da.p_bd, 0.0, None)

    decision = evaluate_dr_participation(
        adjustable, DR_COMPENSATION, DR_WINDOW, config,
        margin=0.0, p_b_plan=plan_da.p_b, p_real_pre=p_real_pre,
    )

    logger.info(f"参与: {decision.participate}")
    logger.info(f"建议响应电量 {decision.response_qty:.2f} MWh, 窗口 {decision.window}")
    logger.info(f"预计补偿 {decision.expected_compensation:,.0f} 元, 套利机会成本 {decision.arbitrage_opportunity_cost:,.0f} 元")
    if decision.participate:
        logger.info(f"履约风险: {decision.fulfill_risk}；中标后响应时段 p_b 锁定（DR_FULFILL），其余时段继续滚动套利")
    else:
        logger.info(f"不参与原因: {decision.reject_reason}")


if __name__ == "__main__":
    main()
