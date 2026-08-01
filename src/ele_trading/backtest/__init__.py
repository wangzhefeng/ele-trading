"""backtest — walk-forward 回测与交易/BESS 指标。

职责：无前瞻 walk-forward 回测（``backtest.py``：无储能/确定性/风险感知/
oracle 四基准，仅 oracle 可见未来）、交易与 BESS 指标及雨流退化核算
（``metrics.py``）。
"""

from .backtest import run_walk_forward_backtest
from .metrics import (
    compute_extended_metrics,
    compute_rainflow_degradation,
    summarize_bess_metrics,
)

__all__ = [
    "compute_extended_metrics",
    "compute_rainflow_degradation",
    "run_walk_forward_backtest",
    "summarize_bess_metrics",
]
