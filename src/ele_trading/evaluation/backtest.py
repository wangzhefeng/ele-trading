"""Forecast-aware backtest for Mengxi trading main line (§10.2).

Implements the calendar-loop backtest with DayAhead→Intraday two-phase flow,
baseline comparison, and counterfactual opportunity-loss analysis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ele_trading.control.rolling_dispatch import run_bess_rolling_dispatch
from ele_trading.data_provider.sample_data import load_default_intraday_prices, load_default_bess_config
from ele_trading.evaluation.metrics import summarize_bess_metrics
from ele_trading.evaluation.settlement import compute_dispatch_revenue
from ele_trading.trading.contracts import MarketConfig, SettlementReport
from ele_trading.trading.day_ahead_coupled import solve_day_ahead_coupled
from ele_trading.trading.intraday_rolling import solve_intraday_rolling
from ele_trading.trading.noisy_backcast import generate_noisy_forecast
from ele_trading.trading.settlement_mengxi import (
    compute_cpen_dayah,
    compute_cpen_long,
    compute_settlement_C,
)


def run_simple_backtest(horizon: int = 4) -> dict[str, float]:
    """运行最小回测闭环（保留原有简单回测）。"""
    price_series = load_default_intraday_prices()
    bess_config = load_default_bess_config()

    dispatch_df = run_bess_rolling_dispatch(
        prices=price_series.prices,
        horizon=horizon,
        initial_soc=bess_config.soc0,
        soc_min=bess_config.soc_min,
        soc_max=bess_config.soc_max,
        p_ch_max=bess_config.p_ch_max,
        p_dis_max=bess_config.p_dis_max,
        eta_ch=bess_config.eta_ch,
        eta_dis=bess_config.eta_dis,
        deg_cost=bess_config.deg_cost,
        dt=bess_config.dt,
    )
    result_df = compute_dispatch_revenue(dispatch_df, deg_cost=bess_config.deg_cost, dt=bess_config.dt)
    return summarize_bess_metrics(result_df)


def run_mengxi_backtest(
    daily_data: pd.DataFrame,
    bess: dict,
    config: MarketConfig,
    mode: str = "B",
    seed: int = 42,
) -> SettlementReport:
    """Run forecast-aware Mengxi backtest for one day.

    Args:
        daily_data: DataFrame with columns [p_long, Q_long, p_dayah, p_real, Q_real, Q_real_load]
        bess: BES object
        config: MarketConfig
        mode: Day-ahead optimization mode (A/B/C)
        seed: Random seed for noisy forecast

    Returns:
        SettlementReport with cost breakdown and opportunity loss
    """
    horizon = len(daily_data)
    dt = 0.25

    # Extract actual data
    p_long = daily_data["p_long"].values
    q_long = daily_data["Q_long"].values
    p_dayah = daily_data["p_dayah"].values
    p_real = daily_data["p_real"].values
    q_real = daily_data["Q_real"].values
    q_real_load = daily_data["Q_real_load"].values

    # Generate forecasts (noisy backcast)
    p_dayah_pre = generate_noisy_forecast(p_dayah, config.sca_price, seed=seed)
    p_real_pre = generate_noisy_forecast(p_real, config.sca_price, seed=seed + 1)
    q_load_pre = generate_noisy_forecast(q_real_load, config.sca_power, seed=seed + 2)

    # Phase 1: Day-ahead optimization (uses only forecasts)
    plan_da = solve_day_ahead_coupled(q_load_pre, p_dayah_pre, p_real_pre, bess, config, mode=mode)
    q_dayah_strategy = plan_da.q_dayah

    # Phase 2: Intraday rolling (uses forecasts + actual day-ahead clearing)
    # For simplicity, do one intraday pass with actual real price
    # In production, this would be a rolling loop
    plan_id = solve_intraday_rolling(
        q_real_load, p_real_pre, q_dayah_strategy, p_dayah, bess["socini"], bess, config
    )
    q_real_strategy = q_real_load - plan_id.schedule.p_b * dt

    # Settlement
    C_strategy = compute_settlement_C(q_long, p_long, q_dayah_strategy, p_dayah, q_real_strategy, p_real)
    cpen_dayah_strategy = compute_cpen_dayah(
        q_dayah_strategy, p_dayah, q_real_strategy, p_real, config.lam_l, config.lam_u
    )

    # Monthly aggregation for Cpen_long (simplified: use daily values)
    q_long_month = np.sum(q_long)
    q_real_month = np.sum(q_real_strategy)
    p_long_month = np.average(p_long, weights=q_long) if q_long_month > 0 else 0.0
    p_spot_month = np.average(p_real, weights=q_real_strategy) if q_real_month > 0 else 0.0
    cpen_long = compute_cpen_long(
        q_long_month, p_long_month, q_real_month, p_spot_month,
        config.lam_l_long, config.lam_u_long, config.m_long
    )

    c_daily = float(np.sum(C_strategy))
    cpen_dayah_total = float(np.sum(cpen_dayah_strategy))
    cost_daily = c_daily + cpen_dayah_total + cpen_long

    # Baseline: no storage, historical bid (use Q_long as proxy)
    q_dayah_baseline = q_long.copy()
    q_real_baseline = q_real_load.copy()
    C_baseline = compute_settlement_C(q_long, p_long, q_dayah_baseline, p_dayah, q_real_baseline, p_real)
    cpen_dayah_baseline = compute_cpen_dayah(
        q_dayah_baseline, p_dayah, q_real_baseline, p_real, config.lam_l, config.lam_u
    )
    cost_baseline = float(np.sum(C_baseline) + np.sum(cpen_dayah_baseline))

    delta_cost = cost_baseline - cost_daily

    # Opportunity loss: top-K periods with highest cost difference
    cost_diff = C_strategy - C_baseline
    top_k = min(10, horizon)
    top_indices = np.argsort(cost_diff)[-top_k:]
    opportunity_loss_topk = pd.DataFrame({
        "t": top_indices,
        "loss": cost_diff[top_indices],
        "cause": ["price_forecast_error"] * top_k,  # simplified
    })

    # Upside if oracle (perfect price forecast, same load forecast)
    plan_oracle = solve_day_ahead_coupled(q_load_pre, p_dayah, p_real, bess, config, mode="A")
    q_dayah_oracle = plan_oracle.q_dayah
    C_oracle = compute_settlement_C(q_long, p_long, q_dayah_oracle, p_dayah, q_real_strategy, p_real)
    cpen_oracle = compute_cpen_dayah(
        q_dayah_oracle, p_dayah, q_real_strategy, p_real, config.lam_l, config.lam_u
    )
    cost_oracle = float(np.sum(C_oracle) + np.sum(cpen_oracle))
    upside_if_oracle = cost_oracle - cost_daily  # negative = oracle is better

    return SettlementReport(
        c_daily=c_daily,
        cpen_dayah=cpen_dayah_total,
        cpen_long=cpen_long,
        cost_daily=cost_daily,
        cost_baseline=cost_baseline,
        delta_cost=delta_cost,
        opportunity_loss_topk=opportunity_loss_topk,
        upside_if_oracle=upside_if_oracle,
    )
