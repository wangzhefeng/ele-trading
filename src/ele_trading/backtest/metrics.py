from __future__ import annotations

import math

import numpy as np
import pandas as pd
import rainflow


def price_capture_ratio(
    strategy_revenue: float,
    oracle_revenue: float,
) -> float:
    """价格捕获率（v4 §8.3）：实际收益 / 完美预见上限收益。

    oracle_revenue 非正（无参考上限）时返回 0.0。
    """
    if oracle_revenue <= 0.0:
        return 0.0
    return float(strategy_revenue / oracle_revenue)


def deviation_penalty_share(
    deviation_penalty: float,
    total_cost: float,
) -> float:
    """偏差考核成本占比（v4 §8.3）：偏差考核 / 总成本。"""
    if total_cost <= 0.0:
        return 0.0
    return float(deviation_penalty / total_cost)


def quantile_calibration_error(
    actual,
    quantile_forecast,
    *,
    quantile: float,
) -> float:
    """分位校准误差（v4 §8.3）：|P(y ≤ q_τ) − τ|。"""
    if not 0.0 < quantile < 1.0:
        raise ValueError("quantile must be within (0, 1)")
    actual_values = np.asarray(list(actual), dtype=float)
    forecast_values = np.asarray(list(quantile_forecast), dtype=float)
    if (
        len(actual_values) != len(forecast_values)
        or len(actual_values) == 0
    ):
        raise ValueError(
            "calibration arrays must have the same non-zero length"
        )
    actual_coverage = float(np.mean(actual_values <= forecast_values))
    return abs(actual_coverage - quantile)


def summarize_bess_metrics(result_df: pd.DataFrame) -> dict[str, float]:
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


def compute_rainflow_degradation(
    soc_series: np.ndarray | list[float],
    e_cap: float,
    deg_cost_per_cycle: float = 0.0,
) -> dict[str, float]:
    """基于雨流计数法的离线退化核算。

    对完整 SOC 序列执行雨流计数，统计等效循环次数和退化成本。
    与线性吞吐量退化模型并列使用，可对比两种退化口径。

    参数
    ----
    soc_series        : SOC 时序（0~1 归一化或实际 MWh 均可，仅影响 DoD 绝对值）
    e_cap             : 储能系统额定容量（MWh）
    deg_cost_per_cycle: 单次完整循环退化成本（CNY/cycle），默认 0.0 仅返回循环次数

    返回字段
    --------
    rainflow_efc      : 雨流等效完整循环次数（Equivalent Full Cycles）
    total_throughput  : 总吞吐量（MWh），= Σ(count × range × e_cap)
    degradation_cost  : 退化成本（CNY），= rainflow_efc × deg_cost_per_cycle
    cycle_count       : 雨流识别的半/全循环总数
    """
    soc = np.asarray(soc_series, dtype=float)

    # rainflow.extract_cycles 返回 (range, mean, count, i_start, i_end)
    cycles = list(rainflow.extract_cycles(soc))

    # 等效完整循环：每个循环的等效次数 = count × range / 2
    # range 是 SOC 摆幅（如 0.8 表示 80% DoD），count 是 1.0（全循环）或 0.5（半循环）
    total_efc = sum(c[2] * c[0] / 2.0 for c in cycles)
    total_throughput = sum(c[2] * c[0] * e_cap for c in cycles)
    deg_cost = total_efc * deg_cost_per_cycle

    return {
        'rainflow_efc': round(total_efc, 6),
        'total_throughput': round(total_throughput, 6),
        'degradation_cost': round(deg_cost, 4),
        'cycle_count': len(cycles),
    }
