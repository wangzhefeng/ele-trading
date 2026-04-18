from __future__ import annotations

import numpy as np
import pandas as pd


def compute_dispatch_revenue(dispatch_df: pd.DataFrame, deg_cost: float, dt: float = 1.0) -> pd.DataFrame:
    """根据逐时充放电结果计算收益分解（原有接口，向后兼容）。"""
    df = dispatch_df.copy()
    # 套利收益 = 放电收入 - 充电成本。
    df['energy_arbitrage_revenue'] = (df['p_dis'] - df['p_ch']) * df['price'] * dt
    # 退化成本按充放电总能量乘单位退化成本近似。
    df['degradation_cost'] = (df['p_ch'] + df['p_dis']) * deg_cost * dt
    df['net_revenue'] = df['energy_arbitrage_revenue'] - df['degradation_cost']
    return df


def compute_deviation_penalty(
    dispatch_df: pd.DataFrame,
    bid_series: pd.Series,
    pi_da: pd.Series,
    dt: float = 1.0,
    dead_band_pct: float = 0.02,
    tier1_threshold_pct: float = 0.05,
    tier1_kappa: float = 0.25,
    tier2_kappa: float = 0.50,
) -> pd.DataFrame:
    """计算广东式偏差考核罚款（分层模型）。

    参数
    ----
    dispatch_df         : 含 p_dis, p_ch 列的 DataFrame（MW）
    bid_series          : 日前申报量序列（MW），与 dispatch_df 等长
    pi_da               : 日前电价序列（CNY/MWh），与 dispatch_df 等长
    dt                  : 时间步长（小时）
    dead_band_pct       : 偏差率死区（默认 2%，广东标准）
    tier1_threshold_pct : Tier1 上限偏差率（默认 5%）
    tier1_kappa         : Tier1 罚款系数（默认 0.25）
    tier2_kappa         : Tier2 罚款系数（默认 0.50）

    返回列
    ------
    dev_mwh   : 偏差电量（MWh，带符号）
    dev_rate  : 偏差率（绝对值，相对于申报量）
    penalty   : 总罚款（CNY，非负）
    """
    net_output = (dispatch_df['p_dis'] - dispatch_df['p_ch']).to_numpy(dtype=float)
    bid = bid_series.to_numpy(dtype=float)
    price = pi_da.to_numpy(dtype=float)

    dev_mwh = (net_output - bid) * dt
    abs_dev_mwh = np.abs(dev_mwh)

    with np.errstate(divide='ignore', invalid='ignore'):
        dev_rate = np.where(np.abs(bid) > 1e-9, abs_dev_mwh / (np.abs(bid) * dt), np.inf)

    dead_band_mwh = dead_band_pct * np.abs(bid) * dt
    tier1_cap_mwh = tier1_threshold_pct * np.abs(bid) * dt

    # 超出死区部分
    above_dead = np.maximum(abs_dev_mwh - dead_band_mwh, 0.0)
    # Tier1 区间：从死区上沿到 tier1_threshold 之间
    tier1_mwh = np.minimum(above_dead, np.maximum(tier1_cap_mwh - dead_band_mwh, 0.0))
    # Tier2 区间：超出 tier1_threshold 的部分
    tier2_mwh = np.maximum(abs_dev_mwh - tier1_cap_mwh, 0.0)

    penalty = (tier1_mwh * tier1_kappa + tier2_mwh * tier2_kappa) * price

    result = dispatch_df[['p_dis', 'p_ch']].copy()
    result['dev_mwh'] = dev_mwh
    result['dev_rate'] = dev_rate
    result['penalty'] = penalty
    return result
