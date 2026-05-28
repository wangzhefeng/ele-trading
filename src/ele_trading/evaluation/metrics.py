from __future__ import annotations

import math
import numpy as np
import pandas as pd


def compute_irr(
    cash_flows: list[float],
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """二分法计算内部收益率 (IRR)。

    Parameters
    ----------
    cash_flows : list[float]
        现金流序列，第 0 项为投资（负值），后续为每年净现金流。
    tol : float
        NPV 收敛容差。
    max_iter : int
        最大迭代次数。

    Returns
    -------
    float
        IRR（小数形式，0.2 = 20%）。无解时返回 0.0。
    """
    if len(cash_flows) < 2:
        return 0.0

    has_negative = any(cf < 0 for cf in cash_flows)
    has_positive = any(cf > 0 for cf in cash_flows)
    if not has_negative or not has_positive:
        return 0.0

    def npv(rate: float) -> float:
        return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cash_flows))

    low, high = -0.99, 1.0
    npv_low = npv(low)
    npv_high = npv(high)

    for _ in range(20):
        if npv_low * npv_high <= 0:
            break
        high *= 10
        npv_high = npv(high)
    else:
        return 0.0

    for _ in range(max_iter):
        mid = (low + high) / 2
        npv_mid = npv(mid)
        if abs(npv_mid) < tol:
            return mid
        if npv_mid * npv_low < 0:
            high, npv_high = mid, npv_mid
        else:
            low, npv_low = mid, npv_mid

    return (low + high) / 2


def summarize_storage_metrics(result_df: pd.DataFrame) -> dict[str, float]:
    """汇总储能回测关键指标（原有接口，向后兼容）。"""
    return {
        'Total Revenue': float(result_df['net_revenue'].sum()),
        'Energy Arbitrage Revenue': float(result_df['energy_arbitrage_revenue'].sum()),
        'Degradation Cost': float(result_df['degradation_cost'].sum()),
        'Average SOC': float(result_df['soc_next'].mean()),
    }


def compute_extended_metrics(
    dispatch_df: pd.DataFrame,
    e_cap: float,
    dt: float = 1.0,
    eta_ch: float = 0.95,
    eta_dis: float = 0.95,
    annualize_periods: int = 8760,
) -> dict[str, float]:
    """计算扩展绩效指标。

    参数
    ----
    dispatch_df       : 含 p_ch, p_dis, net_revenue, soc_next 列的 DataFrame
    e_cap             : 储能系统额定容量（MWh）
    dt                : 时间步长（小时）
    eta_ch / eta_dis  : 充放电效率，用于 RTE 计算
    annualize_periods : 年化用的时段数（小时颗粒度默认 8760）

    返回字段
    --------
    sharpe          : 年化 Sharpe 比率（基于净收益序列）
    max_drawdown    : 最大回撤（负值或零，相对于累计收益高点）
    efc_count       : 等效完整循环次数（Equivalent Full Cycles）
    revenue_per_efc : 单 EFC 净收益（CNY/MWh）
    rte             : 往返效率（Round-Trip Efficiency = eta_ch * eta_dis）
    utilization     : 利用率（实际充放吞吐 / 理论最大吞吐）
    """
    rev = dispatch_df['net_revenue'].to_numpy(dtype=float)
    p_ch = dispatch_df['p_ch'].to_numpy(dtype=float)
    p_dis = dispatch_df['p_dis'].to_numpy(dtype=float)
    n = len(rev)

    # --- Sharpe 比率（年化）---
    mean_r = float(np.mean(rev))
    std_r = float(np.std(rev, ddof=1)) if n > 1 else 0.0
    sharpe = (mean_r / std_r * math.sqrt(annualize_periods)) if std_r > 1e-12 else 0.0

    # --- 最大回撤（MDD）---
    cum_rev = np.cumsum(rev)
    roll_max = np.maximum.accumulate(cum_rev)
    denom = np.abs(roll_max)
    with np.errstate(divide='ignore', invalid='ignore'):
        drawdowns = np.where(denom > 1e-12, (cum_rev - roll_max) / denom, 0.0)
    max_drawdown = float(np.min(drawdowns))

    # --- EFC 和单 EFC 收益 ---
    total_discharge_mwh = float(np.sum(p_dis * dt))
    efc_count = total_discharge_mwh / e_cap if e_cap > 0 else 0.0
    total_net_revenue = float(np.sum(rev))
    revenue_per_efc = (total_net_revenue / efc_count) if efc_count > 1e-12 else 0.0

    # --- 往返效率 ---
    rte = eta_ch * eta_dis

    # --- 利用率 ---
    max_throughput = 2.0 * e_cap * n * dt
    actual_throughput = float(np.sum((p_ch + p_dis) * dt))
    utilization = (actual_throughput / max_throughput) if max_throughput > 0 else 0.0

    return {
        'sharpe': round(sharpe, 6),
        'max_drawdown': round(max_drawdown, 6),
        'efc_count': round(efc_count, 6),
        'revenue_per_efc': round(revenue_per_efc, 4),
        'rte': round(rte, 6),
        'utilization': round(utilization, 6),
    }
