"""月度交易入口（v1.3 §8.2/§8.3）：集中竞价阶梯 + 持仓缺口再平衡 + 降级量价走廊。"""

from __future__ import annotations

from _bootstrap import MARKET_CONFIG_YAML, load_daily_samples

import numpy as np

from ele_trading.markets.single_settlement.config_loader import load_market_config
from ele_trading.positions.monthly_trader import (
    build_bid_ladder,
    build_position_corridor,
    rebalance_position_gap,
)
from ele_trading.utils.log_util import logger


def main() -> None:
    config = load_market_config(MARKET_CONFIG_YAML)
    calendar = load_daily_samples()

    # 当月目标持仓带与价格带（demo：由样例量价推算）
    month_load = float(np.mean([df["Q_real_load"].sum() for df in calendar.values()]) * 30)
    alpha_long = 0.9
    q_target = month_load * alpha_long
    pos_tol = q_target * config.market.pos_tol_ratio
    p_spot = float(np.mean([df["p_real"].mean() for df in calendar.values()]))

    logger.info("=== 集中竞价阶梯申报 ===")
    ladder = build_bid_ladder(
        q_low=q_target - pos_tol, q_high=q_target + pos_tol,
        p_low=p_spot * 0.9, p_high=p_spot * 1.05,
        k=5, direction="buy", config=config, clear_prob_model="linear",
    )
    logger.info(f"方向={ladder.direction}, 预计成本 {ladder.expected_cost:,.0f} 元")
    for i, (q, p, prob) in enumerate(zip(ladder.bid_qty, ladder.bid_price, ladder.clear_prob), 1):
        logger.info(f"  段{i}: 累计 {q:,.0f} MWh @ {p:.1f} 元/MWh (边际成交概率 {prob:.2f})")

    logger.info("=== 持仓缺口再平衡 ===")
    q_need = np.full(30, month_load * alpha_long / 30)
    q_held = q_need * np.linspace(0.7, 1.1, 30)  # demo：前半月欠持、后半月渐回补
    gap = q_held - q_need
    result = rebalance_position_gap(gap, pos_tol=month_load * config.market.pos_tol_ratio / 30, config=config)
    logger.info(f"调整 {result['num_adjustments']}/30 日, 净补购 {result['total_buy']:.0f} MWh, 净减持 {result['total_sell']:.0f} MWh")
    for advice in result["advice"][:5]:
        logger.info(f"  t={advice['period']}: {advice['action']} (gap={advice['gap']:.0f}) — {advice['reason']}")

    # 降级输出：无对手盘数据时双边/挂牌/滚搓退化为量价走廊（v1.3 §12.2）
    logger.info("=== 降级量价走廊（无对手盘数据） ===")
    total_gap = float(gap.sum())
    if abs(total_gap) > pos_tol:
        corridor = build_position_corridor(
            position_gap=total_gap,
            tolerance=pos_tol,
            price_band=(p_spot * 0.9, p_spot * 1.05),
            config=config,
        )
        logger.info(
            f"{corridor.direction}: qty [{corridor.qty_range[0]:,.0f}, {corridor.qty_range[1]:,.0f}] MWh, "
            f"price [{corridor.price_range[0]:.1f}, {corridor.price_range[1]:.1f}] — {corridor.reason}"
        )
    else:
        logger.info(f"缺口 {total_gap:.1f} MWh 在容忍带 ±{pos_tol:.0f} 内，不调整")


if __name__ == "__main__":
    main()
