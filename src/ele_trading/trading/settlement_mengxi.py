"""Active Mengxi single-settlement calculations."""

from __future__ import annotations

import numpy as np

from ele_trading.trading.contracts import (
    DecisionTrace,
    MarketConfig,
    SettlementReport,
)


def _aligned_arrays(*values: np.ndarray) -> tuple[np.ndarray, ...]:
    arrays = tuple(np.asarray(value, dtype=float) for value in values)
    if not arrays or any(array.shape != arrays[0].shape for array in arrays):
        raise ValueError("settlement inputs must use identical shapes")
    if any(not np.isfinite(array).all() for array in arrays):
        raise ValueError("settlement inputs must contain finite values")
    return arrays


def compute_energy_cost(
    q_real: np.ndarray,
    p_real: np.ndarray,
) -> np.ndarray:
    """Return period energy cost ``Q_real * p_real``."""
    q_real_arr, p_real_arr = _aligned_arrays(q_real, p_real)
    return q_real_arr * p_real_arr


def compute_contract_difference(
    q_long: np.ndarray,
    p_long: np.ndarray,
    *,
    p_ref: np.ndarray,
) -> np.ndarray:
    """Return period contract difference ``Q_long * (p_long - p_ref)``."""
    q_long_arr, p_long_arr, p_ref_arr = _aligned_arrays(
        q_long,
        p_long,
        p_ref,
    )
    return q_long_arr * (p_long_arr - p_ref_arr)


def compute_long_recovery(
    *,
    q_long_month: float,
    p_long_month: float,
    q_real_month: float,
    p_ref_month: float,
    config: MarketConfig,
) -> float:
    """Apply the configured monthly long-position shortage/excess rule."""
    values = np.asarray(
        [q_long_month, p_long_month, q_real_month, p_ref_month],
        dtype=float,
    )
    if not np.isfinite(values).all():
        raise ValueError("long-recovery inputs must be finite")
    if q_real_month <= 0.0:
        return 0.0

    ratio = q_long_month / q_real_month
    if (
        ratio > config.long_recovery_upper_ratio
        and p_long_month < p_ref_month
    ):
        return (
            config.long_recovery_multiplier
            * (
                q_long_month
                - config.long_recovery_upper_ratio * q_real_month
            )
            * (p_ref_month - p_long_month)
        )
    if (
        ratio < config.long_recovery_lower_ratio
        and p_long_month > p_ref_month
    ):
        return (
            config.long_recovery_multiplier
            * (
                config.long_recovery_lower_ratio * q_real_month
                - q_long_month
            )
            * (p_long_month - p_ref_month)
        )
    return 0.0


def build_settlement_report(
    *,
    q_real: np.ndarray,
    p_real: np.ndarray,
    q_long: np.ndarray,
    p_long: np.ndarray,
    p_ref: np.ndarray,
    long_recovery: float = 0.0,
    dr_adjustment: float = 0.0,
    degradation_cost: float = 0.0,
    execution_adjustment: float = 0.0,
    baseline_cost: float = 0.0,
    trace: DecisionTrace | None = None,
) -> SettlementReport:
    """Build an itemized report with each signed adjustment counted once."""
    scalar_items = np.asarray(
        [
            long_recovery,
            dr_adjustment,
            degradation_cost,
            execution_adjustment,
            baseline_cost,
        ],
        dtype=float,
    )
    if not np.isfinite(scalar_items).all():
        raise ValueError("settlement adjustments must be finite")

    energy_cost = float(np.sum(compute_energy_cost(q_real, p_real)))
    contract_difference = float(
        np.sum(
            compute_contract_difference(
                q_long,
                p_long,
                p_ref=p_ref,
            )
        )
    )
    total_cost = float(
        energy_cost
        + contract_difference
        + long_recovery
        + dr_adjustment
        + degradation_cost
        + execution_adjustment
    )
    return SettlementReport(
        energy_cost=energy_cost,
        contract_difference=contract_difference,
        long_recovery=float(long_recovery),
        dr_adjustment=float(dr_adjustment),
        degradation_cost=float(degradation_cost),
        execution_adjustment=float(execution_adjustment),
        total_cost=total_cost,
        baseline_cost=float(baseline_cost),
        delta_cost=float(baseline_cost - total_cost),
        trace=trace,
    )


def aggregate_to_settle_periods(
    quantity: np.ndarray,
    settle_periods: int,
) -> np.ndarray:
    """Aggregate interval energy while preserving the total quantity."""
    values = np.asarray(quantity, dtype=float)
    if (
        settle_periods <= 0
        or values.ndim != 1
        or len(values) % settle_periods != 0
    ):
        raise ValueError(
            "settle_periods must be a positive divisor of the horizon"
        )
    return values.reshape(settle_periods, -1).sum(axis=1)
