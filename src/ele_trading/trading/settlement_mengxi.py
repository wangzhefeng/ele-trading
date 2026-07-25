"""Mengxi band-style settlement (§10.1).

Implements the quantity-price settlement C, difference settlement C2,
day-ahead deviation penalty Cpen_dayah, and mid-long-term recovery Cpen_long.
"""

from __future__ import annotations

import numpy as np


def compute_settlement_C(
    q_long: np.ndarray,
    p_long: np.ndarray,
    q_dayah: np.ndarray,
    p_dayah: np.ndarray,
    q_real: np.ndarray,
    p_real: np.ndarray,
) -> np.ndarray:
    """Quantity-price settlement (main caliber).

    C[t] = Q_long[t]*p_long[t]
         + (Q_dayah[t]-Q_long[t])*p_dayah[t]
         + (Q_real[t]-Q_dayah[t])*p_real[t]
    """
    return (
        q_long * p_long
        + (q_dayah - q_long) * p_dayah
        + (q_real - q_dayah) * p_real
    )


def compute_settlement_C2(
    q_long: np.ndarray,
    p_long: np.ndarray,
    q_dayah: np.ndarray,
    p_dayah: np.ndarray,
    q_real: np.ndarray,
    p_real: np.ndarray,
) -> np.ndarray:
    """Difference settlement (validation caliber).

    C2[t] = (Q_long[t]-Q_real[t])*(p_long[t]-p_dayah[t])
          + (Q_dayah[t]-Q_real[t])*(p_dayah[t]-p_real[t])
          + Q_real[t]*p_long[t]
    """
    return (
        (q_long - q_real) * (p_long - p_dayah)
        + (q_dayah - q_real) * (p_dayah - p_real)
        + q_real * p_long
    )


def compute_cpen_dayah(
    q_dayah: np.ndarray,
    p_dayah: np.ndarray,
    q_real: np.ndarray,
    p_real: np.ndarray,
    lam_l: float,
    lam_u: float,
) -> np.ndarray:
    """Day-ahead over/under-recovery (band caliber).

    If Q_dayah > lam_u*Q_real and p_dayah < p_real:
        Cpen = (Q_dayah - lam_u*Q_real) * (p_real - p_dayah)
    If Q_dayah < lam_l*Q_real and p_dayah > p_real:
        Cpen = (lam_l*Q_real - Q_dayah) * (p_dayah - p_real)
    Else 0.
    """
    cpen = np.zeros_like(q_dayah, dtype=float)

    # Over-declaration band breach with favorable price spread
    mask_over = (q_dayah > lam_u * q_real) & (p_dayah < p_real)
    cpen[mask_over] = (q_dayah[mask_over] - lam_u * q_real[mask_over]) * (
        p_real[mask_over] - p_dayah[mask_over]
    )

    # Under-declaration band breach with unfavorable price spread
    mask_under = (q_dayah < lam_l * q_real) & (p_dayah > p_real)
    cpen[mask_under] = (lam_l * q_real[mask_under] - q_dayah[mask_under]) * (
        p_dayah[mask_under] - p_real[mask_under]
    )

    return cpen


def compute_cpen_long(
    q_long_month: float,
    p_long_month: float,
    q_real_month: float,
    p_spot_month: float,
    lam_l_long: float,
    lam_u_long: float,
    m_long: float,
) -> float:
    """Mid-long-term monthly recovery.

    sign_ratio = Q_long_month / Q_real_month
    If sign_ratio > lam_u_long and p_long_month < p_spot_month:
        Cpen = m_long * (Q_long_month - lam_u_long*Q_real_month) * (p_spot_month - p_long_month)
    If sign_ratio < lam_l_long and p_long_month > p_spot_month:
        Cpen = m_long * (lam_l_long*Q_real_month - Q_long_month) * (p_long_month - p_spot_month)
    Else 0.
    """
    if q_real_month <= 0:
        return 0.0

    sign_ratio = q_long_month / q_real_month

    if sign_ratio > lam_u_long and p_long_month < p_spot_month:
        return m_long * (q_long_month - lam_u_long * q_real_month) * (
            p_spot_month - p_long_month
        )
    if sign_ratio < lam_l_long and p_long_month > p_spot_month:
        return m_long * (lam_l_long * q_real_month - q_long_month) * (
            p_long_month - p_spot_month
        )
    return 0.0
