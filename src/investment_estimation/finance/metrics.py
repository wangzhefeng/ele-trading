"""通用财务指标计算。

从 `ele_trading.evaluation.metrics` 迁移而来的最小子集,供本包自包含使用,
避免反向依赖 ele_trading 主包。当前仅含 `compute_irr`(capacity_planning
迁移代码 `todo/irr_finance.py` 依赖)。
"""

from __future__ import annotations


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
