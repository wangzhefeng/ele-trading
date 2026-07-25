"""Demand response allocator (v1.3 §9)."""

from __future__ import annotations

import numpy as np

from ele_trading.trading.contracts import DRDecision, MarketConfig

DT = 0.25  # 15 min


def estimate_arbitrage_opportunity_cost(
    p_b_plan: np.ndarray,
    p_real_pre: np.ndarray,
    window: tuple[int, int],
) -> float:
    """由日前/滚动计划实算响应时段放弃的套利收益（v1.3 §9 步骤 2）。

    响应窗口内若被锁定为响应曲线，储能放弃的套利收益为
    ``Σ (p_b_plan[t]) * p_real_pre[t] * DT`` 中与响应方向相反的部分；
    简化为窗口内计划净放电收益（充电时段贡献为负成本）。
    """
    start, end = window
    seg = p_b_plan[start:end] * p_real_pre[start:end] * DT
    return float(np.sum(seg))


def evaluate_dr_participation(
    adjustable_capacity: np.ndarray,
    dr_compensation: float,
    window: tuple[int, int],
    config: MarketConfig,
    margin: float = 0.0,
    p_b_plan: np.ndarray | None = None,
    p_real_pre: np.ndarray | None = None,
) -> DRDecision:
    """Evaluate whether to participate in demand response (v1.3 §9).

    Compares DR compensation vs arbitrage opportunity cost.
    机会成本优先由 ``p_b_plan``（日前/滚动计划）实算；缺省退回固定单价估计。

    Args:
        adjustable_capacity: Available capacity for DR (MW) per period
        dr_compensation: DR compensation price (元/MWh)
        window: DR window (start, end) period indices
        config: MarketConfig
        margin: Participation margin threshold (元)
        p_b_plan: 日前/滚动净放电计划（MW），用于实算机会成本
        p_real_pre: 实时价预测（元/MWh），与 p_b_plan 配套

    Returns:
        DRDecision with participation recommendation
    """
    start, end = window
    window_capacity = adjustable_capacity[start:end]
    response_qty = float(np.sum(window_capacity) * DT)  # MWh (15min periods)

    # 机会成本：优先实算（v1.3 §9 ΔR_arb），无计划数据时退回固定单价估计
    if p_b_plan is not None and p_real_pre is not None:
        arbitrage_opportunity_cost = estimate_arbitrage_opportunity_cost(p_b_plan, p_real_pre, window)
        arbitrage_opportunity_cost = max(arbitrage_opportunity_cost, 0.0)
    else:
        arbitrage_opportunity_cost = response_qty * 50.0  # 占位估计 50 元/MWh

    expected_compensation = response_qty * dr_compensation

    # Participation rule: compensation > opportunity cost + margin
    participate = expected_compensation > (arbitrage_opportunity_cost + margin)

    if participate:
        fulfill_risk = "low"  # simplified
        reject_reason = None
    else:
        fulfill_risk = "n/a"
        reject_reason = (
            f"Compensation {expected_compensation:.0f} < opportunity cost "
            f"{arbitrage_opportunity_cost:.0f} + margin {margin:.0f}"
        )

    return DRDecision(
        participate=participate,
        response_qty=response_qty,
        window=window,
        expected_compensation=expected_compensation,
        arbitrage_opportunity_cost=arbitrage_opportunity_cost,
        fulfill_risk=fulfill_risk,
        reject_reason=reject_reason,
    )
