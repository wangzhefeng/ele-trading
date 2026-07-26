"""Forecast-aware backtest for Mengxi trading main line (v1.3 §10).

Implements the calendar-loop backtest with DayAhead→Intraday two-phase flow,
baseline comparison, and counterfactual opportunity-loss analysis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .contracts import MarketConfig, SettlementReport
from .day_ahead_coupled import solve_day_ahead_coupled
from .intraday_rolling import solve_intraday_rolling
from .noisy_backcast import generate_noisy_forecast
from .settlement_mengxi import (
    compute_cpen_dayah,
    compute_cpen_long,
    compute_settlement_C,
)

DT = 0.25  # 15 min 决策粒度（v1.3 §2.1）


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
    q_real_strategy = q_real_load - plan_id.schedule.p_b * DT

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

    # Upside if oracle：同一负荷预测下用实际价格跑模式 A 的对照（v1.3 §10.2）。
    # 正值 = 价格预测可改善空间。
    plan_oracle = solve_day_ahead_coupled(q_load_pre, p_dayah, p_real, bess, config, mode="A")
    q_dayah_oracle = plan_oracle.q_dayah
    C_oracle = compute_settlement_C(q_long, p_long, q_dayah_oracle, p_dayah, q_real_strategy, p_real)
    cpen_oracle = compute_cpen_dayah(
        q_dayah_oracle, p_dayah, q_real_strategy, p_real, config.lam_l, config.lam_u
    )
    cost_oracle = float(np.sum(C_oracle) + np.sum(cpen_oracle))
    upside_if_oracle = cost_daily - cost_oracle

    # 机会损失 Top-K：主因按价差预测误差 / 负荷预测误差 / SOC 触限 / 偏差带触线归因
    cost_diff = C_strategy - C_baseline
    top_k = min(10, horizon)
    top_indices = np.argsort(cost_diff)[-top_k:]
    causes = _attribute_loss_causes(
        top_indices, p_dayah, p_dayah_pre, p_real, p_real_pre,
        q_real_load, q_load_pre, plan_da.soc, bess,
        q_dayah_strategy, q_real_strategy, config,
    )
    opportunity_loss_topk = pd.DataFrame({
        "t": top_indices,
        "loss": cost_diff[top_indices],
        "cause": causes,
    })

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


def run_mengxi_backtest_calendar(
    calendar_data: dict[pd.Timestamp, pd.DataFrame],
    bess: dict,
    config: MarketConfig,
    mode: str = "B",
    seed: int = 42,
    rolling_step: int = 4,
) -> pd.DataFrame:
    """多日历 forecast-aware 回测（v1.3 §10.1）。

    逐日：日前（只用 ``*_pre`` 预测）→ 日内逐刻滚动（每 ``rolling_step`` 刻
    以当日实际量价重新加噪生成剩余窗口预测重优化）→ 蒙西带状结算。
    跨日：SOC 末端传递（§2.3 跨日 SOC 传递）；``Cpen_long`` 按自然月聚合
    后计入该月最后一天。

    Args:
        calendar_data: {日期: 当日 96 点 DataFrame}（列同 run_mengxi_backtest）
        bess: BES object
        config: MarketConfig
        mode: 日前优化模式
        seed: 加噪种子
        rolling_step: 日内重优化步长（刻）

    Returns:
        每日一行 [c_daily, cpen_dayah, cpen_long, cost_daily, cost_baseline, delta_cost]
    """
    days = sorted(calendar_data.keys())
    soc_current = float(bess["socini"])
    daily_rows: dict[pd.Timestamp, dict] = {}
    month_groups: dict[tuple[int, int], list[pd.Timestamp]] = {}

    for day in days:
        daily_rows[day] = _run_one_day(
            calendar_data[day], bess, config, mode, seed, soc_current, rolling_step
        )
        soc_current = daily_rows[day].pop("_soc_end")
        key = (day.year, day.month)
        month_groups.setdefault(key, []).append(day)

    # 月度中长期回收（v1.3 §5.3 月度口径），计入每月最后一天
    for (year, month), month_days in month_groups.items():
        cpen_long = _monthly_cpen_long(month_days, daily_rows, config)
        last_day = month_days[-1]
        row = daily_rows[last_day]
        row["cpen_long"] += cpen_long
        row["cost_daily"] += cpen_long
        row["delta_cost"] = row["cost_baseline"] - row["cost_daily"]

    return pd.DataFrame.from_dict(daily_rows, orient="index")


def _run_one_day(
    daily_data: pd.DataFrame,
    bess: dict,
    config: MarketConfig,
    mode: str,
    seed: int,
    soc0: float,
    rolling_step: int = 4,
) -> dict:
    """单日两阶段回测：日前 → 逐刻日内滚动 → 结算。"""
    horizon = len(daily_data)
    p_long = daily_data["p_long"].values
    q_long = daily_data["Q_long"].values
    p_dayah = daily_data["p_dayah"].values
    p_real = daily_data["p_real"].values
    q_real = daily_data["Q_real"].values
    q_real_load = daily_data["Q_real_load"].values

    # 日前：只用预测（以当日 00:00 为 issue 的加噪预测）
    p_dayah_pre = generate_noisy_forecast(p_dayah, config.sca_price, seed=seed)
    p_real_pre = generate_noisy_forecast(p_real, config.sca_price, seed=seed + 1)
    q_load_pre = generate_noisy_forecast(q_real_load, config.sca_power, seed=seed + 2)

    plan_da = solve_day_ahead_coupled(q_load_pre, p_dayah_pre, p_real_pre, bess, config, mode=mode)
    q_dayah_strategy = plan_da.q_dayah

    # 日内逐刻滚动：已执行段回放、剩余段重优化（v1.3 §7.2）
    p_b_exec = np.zeros(horizon)
    soc = soc0
    prev_p_b = None
    for t in range(horizon):
        if t % rolling_step == 0 or prev_p_b is None:
            rem = horizon - t
            # 以当前刻为 issue 重新加噪生成剩余窗口预测（无前瞻：种子随刻变化）
            q_load_roll = generate_noisy_forecast(q_real_load[t:], config.sca_power, seed=seed + 1000 + t)
            p_real_roll = generate_noisy_forecast(p_real[t:], config.sca_price, seed=seed + 2000 + t)
            bess_roll = {**bess, "socini": soc}
            plan_id = solve_intraday_rolling(
                q_load_roll,
                p_real_roll,
                q_dayah_strategy[t:],
                p_dayah[t:],
                soc,
                bess_roll,
                config,
                prev_p_b=prev_p_b[-rem:] if prev_p_b is not None and len(prev_p_b) >= rem else None,
            )
            prev_p_b = plan_id.schedule.p_b
        # 执行当前刻（取最新计划首点）
        p_b_exec[t] = prev_p_b[0] if prev_p_b is not None and len(prev_p_b) > 0 else 0.0
        p_bc_t = max(-p_b_exec[t], 0.0)
        p_bd_t = max(p_b_exec[t], 0.0)
        soc = soc + bess["p_bceff"] * p_bc_t * DT - (p_bd_t * DT) / bess["p_bdeff"]
        soc = float(np.clip(soc, bess["socmin"], bess["socmax"]))
        if prev_p_b is not None and len(prev_p_b) > 1:
            prev_p_b = prev_p_b[1:]

    q_real_strategy = q_real_load - p_b_exec * DT

    # 结算（v1.3 §5）
    C_strategy = compute_settlement_C(q_long, p_long, q_dayah_strategy, p_dayah, q_real_strategy, p_real)
    cpen_dayah_strategy = compute_cpen_dayah(
        q_dayah_strategy, p_dayah, q_real_strategy, p_real, config.lam_l, config.lam_u
    )
    c_daily = float(np.sum(C_strategy))
    cpen_dayah_total = float(np.sum(cpen_dayah_strategy))
    cost_daily = c_daily + cpen_dayah_total

    # 基准：无储能、以中长期持仓为申报（v1.3 §10.1 基准口径）
    C_baseline = compute_settlement_C(q_long, p_long, q_long, p_dayah, q_real_load, p_real)
    cpen_dayah_baseline = compute_cpen_dayah(
        q_long, p_dayah, q_real_load, p_real, config.lam_l, config.lam_u
    )
    cost_baseline = float(np.sum(C_baseline) + np.sum(cpen_dayah_baseline))

    return {
        "c_daily": c_daily,
        "cpen_dayah": cpen_dayah_total,
        "cpen_long": 0.0,  # 月度聚合后回填
        "cost_daily": cost_daily,
        "cost_baseline": cost_baseline,
        "delta_cost": cost_baseline - cost_daily,
        "_soc_end": soc,
        "_q_long_sum": float(np.sum(q_long)),
        "_q_real_sum": float(np.sum(q_real_strategy)),
        "_p_long_wavg": float(np.average(p_long, weights=q_long)) if q_long.sum() > 0 else 0.0,
        "_p_spot_wavg": float(np.average(p_real, weights=q_real_strategy)) if q_real_strategy.sum() > 0 else 0.0,
    }


def _monthly_cpen_long(
    month_days: list[pd.Timestamp],
    daily_rows: dict[pd.Timestamp, dict],
    config: MarketConfig,
) -> float:
    """按自然月聚合签约比例与加权价，计算一次月度中长期回收（v1.3 §5.3）。"""
    q_long_month = sum(daily_rows[d]["_q_long_sum"] for d in month_days)
    q_real_month = sum(daily_rows[d]["_q_real_sum"] for d in month_days)
    if q_long_month <= 0 or q_real_month <= 0:
        return 0.0
    # 电量加权月均价的再加权（按各日电量）
    p_long_m = (
        sum(daily_rows[d]["_p_long_wavg"] * daily_rows[d]["_q_long_sum"] for d in month_days) / q_long_month
    )
    p_spot_m = (
        sum(daily_rows[d]["_p_spot_wavg"] * daily_rows[d]["_q_real_sum"] for d in month_days) / q_real_month
    )
    return compute_cpen_long(
        q_long_month, p_long_m, q_real_month, p_spot_m,
        config.lam_l_long, config.lam_u_long, config.m_long,
    )


def _attribute_loss_causes(
    top_indices: np.ndarray,
    p_dayah: np.ndarray,
    p_dayah_pre: np.ndarray,
    p_real: np.ndarray,
    p_real_pre: np.ndarray,
    q_real_load: np.ndarray,
    q_load_pre: np.ndarray,
    soc_plan: np.ndarray,
    bess: dict,
    q_dayah: np.ndarray,
    q_real: np.ndarray,
    config: MarketConfig,
) -> list[str]:
    """机会损失时段主因归因（v1.3 §10.2）。

    优先级：SOC 触限 → 偏差带触线 → 价差预测误差 → 负荷预测误差。
    """
    causes = []
    price_err = np.abs(p_dayah - p_dayah_pre) + np.abs(p_real - p_real_pre)
    load_err = np.abs(q_real_load - q_load_pre)
    for t in top_indices:
        if soc_plan[min(t, len(soc_plan) - 1)] <= bess["socmin"] + 1e-3:
            causes.append("soc_limit")
        elif not (config.lam_l * q_real[t] <= q_dayah[t] <= config.lam_u * q_real[t]):
            causes.append("band_breach")
        elif price_err[t] >= load_err[t] * 100:  # 价差误差与电量误差不同量纲，按百元/MWh 折算比较
            causes.append("price_forecast_error")
        else:
            causes.append("load_forecast_error")
    return causes
