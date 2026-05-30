from __future__ import annotations

from ele_trading.optimization.mpc_bess import run_bess_mpc


def run_bess_rolling_dispatch(prices: list[float], horizon: int, initial_soc: float, **bess_kwargs):
    """滚动调度包装器。

    当前直接复用 MPC 模块，让控制层与底层求解细节解耦。
    """
    return run_bess_mpc(prices=prices, horizon=horizon, initial_soc=initial_soc, **bess_kwargs)
