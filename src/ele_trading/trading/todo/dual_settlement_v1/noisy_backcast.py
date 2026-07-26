"""Noisy backcast: generate realistic forecasts from historical actuals (§15.5).

Uses sca_price/sca_power to inject noise into historical prices and loads,
producing *_pre series that mimic real forecast errors for backtesting.
"""

from __future__ import annotations

import numpy as np


def generate_noisy_forecast(
    actual: np.ndarray,
    sca: float,
    seed: int | None = None,
) -> np.ndarray:
    """Generate noisy forecast from actual series.

    Args:
        actual: Historical actual values (price or load).
        sca: Noise scale factor (e.g., 0.10 = 10% MAPE target).
        seed: Random seed for reproducibility.

    Returns:
        Noisy forecast with same shape as input.
    """
    if seed is not None:
        rng = np.random.default_rng(seed)
    else:
        rng = np.random.default_rng()

    # Multiplicative noise: forecast = actual * (1 + noise)
    noise = rng.normal(0, sca, size=actual.shape)
    forecast = actual * (1 + noise)

    # Clip negative prices to zero (electricity prices can be negative in some markets,
    # but for Mengxi default we keep non-negative)
    forecast = np.maximum(forecast, 0.0)

    return forecast
