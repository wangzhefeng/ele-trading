"""日前储售联动入口（v1.3 §6）：日前储能计划 + 量价申报。

用 data/trading 最新一日样例跑日前耦合优化，计划曲线落 results/trading/plans/。
"""

from __future__ import annotations

import argparse

from _bootstrap import MENGXI_YAML, RESULTS_TRADING, SAMPLE_BESS, load_daily_samples

from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.day_ahead_coupled import solve_day_ahead_coupled
from ele_trading.trading.noisy_backcast import generate_noisy_forecast
from ele_trading.utils.log_util import logger


def main() -> None:
    parser = argparse.ArgumentParser(description="蒙西日前储售联动优化")
    parser.add_argument("--mode", default=None, choices=["A", "B", "C"], help="覆盖配置中的 dayahead.mode")
    args = parser.parse_args()

    config = load_market_config(MENGXI_YAML)
    mode = args.mode or config.dayahead_mode

    calendar = load_daily_samples()
    day = sorted(calendar.keys())[-1]
    daily = calendar[day]
    logger.info(f"=== 日前优化: {day:%Y-%m-%d}, mode={mode} ===")

    # 决策只用预测（对实际量价加噪，模拟日前出具的预测）
    p_dayah_pre = generate_noisy_forecast(daily["p_dayah"].values, config.sca_price, seed=1)
    p_real_pre = generate_noisy_forecast(daily["p_real"].values, config.sca_price, seed=2)
    q_load_pre = generate_noisy_forecast(daily["Q_real_load"].values, config.sca_power, seed=3)

    plan = solve_day_ahead_coupled(
        q_load_pre, p_dayah_pre, p_real_pre, SAMPLE_BESS, config,
        mode=mode, q_long=daily["Q_long"].values,
    )

    logger.info(f"expected_cost={plan.expected_cost:.1f}, expected_revenue={plan.expected_revenue:.1f}")
    logger.info(f"充电 {plan.p_bc.sum() * 0.25:.2f} MWh, 放电 {plan.p_bd.sum() * 0.25:.2f} MWh, "
                f"申报总量 {plan.q_dayah.sum():.1f} MWh")
    if plan.constraint_flags:
        for name, ts in plan.constraint_flags.items():
            logger.info(f"约束提示[{name}]: {len(ts)} 刻, 前 10 刻 {ts[:10]}")
    logger.info(f"申报价: {'报量不报价（bid_prices=None）' if plan.bid_prices is None else '分段报价已生成'}")

    out_dir = RESULTS_TRADING / "plans"
    out_dir.mkdir(parents=True, exist_ok=True)
    import numpy as np
    import pandas as pd

    df = pd.DataFrame(
        {
            "t": np.arange(len(plan.p_b)),
            "p_bc": plan.p_bc,
            "p_bd": plan.p_bd,
            "p_b": plan.p_b,
            "soc": plan.soc[:-1],
            "q_dayah": plan.q_dayah,
        }
    )
    out = out_dir / f"day_ahead_plan_{day:%Y-%m-%d}_mode{mode}.csv"
    df.to_csv(out, index=False)
    logger.info(f"计划曲线已落盘: {out}")


if __name__ == "__main__":
    main()
