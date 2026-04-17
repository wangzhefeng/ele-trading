from __future__ import annotations

import pandas as pd


def summarize_storage_metrics(result_df: pd.DataFrame) -> dict[str, float]:
    """汇总储能回测中的关键指标。"""
    return {
        'Total Revenue': float(result_df['net_revenue'].sum()),
        'Energy Arbitrage Revenue': float(result_df['energy_arbitrage_revenue'].sum()),
        'Degradation Cost': float(result_df['degradation_cost'].sum()),
        'Average SOC': float(result_df['soc_next'].mean()),
    }
