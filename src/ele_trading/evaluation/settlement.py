from __future__ import annotations

import pandas as pd

# NOTE: Guangdong-style tiered deviation penalty (compute_deviation_penalty) has been
# removed per v1 design document §5.4. The Mengxi band-style settlement is now the
# sole implementation; see `ele_trading.trading.settlement_mengxi` for C/C2/Cpen_dayah/
# Cpen_long formulas. Guangdong user-side dispatch code is preserved but no longer
# linked to settlement (minimal-change cleanup; full removal of Guangdong chain is
# out of scope for this phase).


def compute_dispatch_revenue(dispatch_df: pd.DataFrame, deg_cost: float, dt: float = 1.0) -> pd.DataFrame:
    """根据逐时充放电结果计算收益分解（原有接口，向后兼容）。"""
    df = dispatch_df.copy()
    # 套利收益 = 放电收入 - 充电成本。
    df['energy_arbitrage_revenue'] = (df['p_dis'] - df['p_ch']) * df['price'] * dt
    # 退化成本按充放电总能量乘单位退化成本近似。
    df['degradation_cost'] = (df['p_ch'] + df['p_dis']) * deg_cost * dt
    df['net_revenue'] = df['energy_arbitrage_revenue'] - df['degradation_cost']
    return df
