"""日内滚动优化入口（v1.3 §7）：模拟一日逐刻滚动重优化。"""

from __future__ import annotations

from _bootstrap import MENGXI_YAML, SAMPLE_BESS, load_daily_samples

import numpy as np

from ele_trading.trading.config_loader import load_market_config
from ele_trading.trading.day_ahead_coupled import solve_day_ahead_coupled
from ele_trading.trading.intraday_rolling import solve_intraday_rolling
from ele_trading.trading.noisy_backcast import generate_noisy_forecast
from ele_trading.utils.log_util import logger

DT = 0.25
ROLLING_STEP = 12  # 每 3 小时重优化一次


def main() -> None:
    config = load_market_config(MENGXI_YAML)
    calendar = load_daily_samples()
    day = sorted(calendar.keys())[-1]
    daily = calendar[day]
    horizon = len(daily)
    logger.info(f"=== 日内滚动: {day:%Y-%m-%d}, 步长={ROLLING_STEP} 刻 ===")

    # 日前计划（只用预测）
    p_dayah_pre = generate_noisy_forecast(daily["p_dayah"].values, config.sca_price, seed=1)
    p_real_pre = generate_noisy_forecast(daily["p_real"].values, config.sca_price, seed=2)
    q_load_pre = generate_noisy_forecast(daily["Q_real_load"].values, config.sca_power, seed=3)
    plan_da = solve_day_ahead_coupled(q_load_pre, p_dayah_pre, p_real_pre, SAMPLE_BESS, config)
    q_dayah = plan_da.q_dayah
    logger.info(f"日前申报总量 {q_dayah.sum():.1f} MWh")

    # 日内逐刻滚动
    soc = SAMPLE_BESS["socini"]
    prev_p_b = None
    executed = np.zeros(horizon)
    for t in range(horizon):
        if t % ROLLING_STEP == 0 or prev_p_b is None:
            rem = horizon - t
            q_load_roll = generate_noisy_forecast(daily["Q_real_load"].values[t:], config.sca_power, seed=100 + t)
            p_real_roll = generate_noisy_forecast(daily["p_real"].values[t:], config.sca_price, seed=200 + t)
            plan_id = solve_intraday_rolling(
                q_load_roll, p_real_roll, q_dayah[t:], daily["p_dayah"].values[t:],
                soc, SAMPLE_BESS, config,
                prev_p_b=prev_p_b[-rem:] if prev_p_b is not None and len(prev_p_b) >= rem else None,
            )
            prev_p_b = plan_id.schedule.p_b
            if t % (ROLLING_STEP * 4) == 0:
                delta = float(np.abs(plan_id.adjustment.delta_p_b).max()) if len(plan_id.adjustment.delta_p_b) else 0.0
                logger.info(
                    f"t={t:>2} 重优化: soc={soc:.2f}, Δp_b_max={delta:.3f}, "
                    f"reasons={plan_id.adjustment.reasons or ['-']}"
                )
        executed[t] = prev_p_b[0] if len(prev_p_b) else 0.0
        p_bc_t, p_bd_t = max(-executed[t], 0.0), max(executed[t], 0.0)
        soc = float(np.clip(
            soc + SAMPLE_BESS["p_bceff"] * p_bc_t * DT - p_bd_t * DT / SAMPLE_BESS["p_bdeff"],
            SAMPLE_BESS["socmin"], SAMPLE_BESS["socmax"],
        ))
        prev_p_b = prev_p_b[1:] if len(prev_p_b) > 1 else prev_p_b

    total_dis = sum(max(p, 0.0) for p in executed) * DT
    total_chg = sum(max(-p, 0.0) for p in executed) * DT
    logger.info(f"日内执行: 放电 {total_dis:.2f} MWh, 充电 {total_chg:.2f} MWh, 末态 SOC {soc:.2f}")


if __name__ == "__main__":
    main()
