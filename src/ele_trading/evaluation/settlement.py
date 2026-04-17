from __future__ import annotations

import pandas as pd


def compute_dispatch_revenue(dispatch_df: pd.DataFrame, deg_cost: float, dt: float = 1.0) -> pd.DataFrame:
    """根据逐时充放电结果计算收益分解。"""
    df = dispatch_df.copy()
    # 套利收益 = 放电收入 - 充电成本。
    df['energy_arbitrage_revenue'] = (df['p_dis'] - df['p_ch']) * df['price'] * dt
    # 退化成本按充放电总能量乘单位退化成本近似。
    df['degradation_cost'] = (df['p_ch'] + df['p_dis']) * deg_cost * dt
    df['net_revenue'] = df['energy_arbitrage_revenue'] - df['degradation_cost']
    return df
