from .metrics import compute_irr, summarize_bess_metrics, compute_extended_metrics
from .settlement import compute_dispatch_revenue

__all__ = [
    "run_simple_backtest",
    "compute_irr",
    "summarize_bess_metrics",
    "compute_extended_metrics",
    "compute_dispatch_revenue",
    "BESSSimulationModel",
]


def __getattr__(name: str):
    if name == "run_simple_backtest":
        from .backtest import run_simple_backtest

        return run_simple_backtest
    if name == "BESSSimulationModel":
        from .simulation import BESSSimulationModel

        return BESSSimulationModel
    raise AttributeError(name)
