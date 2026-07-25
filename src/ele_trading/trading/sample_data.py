"""Generate synthetic 96-point daily settlement sample for the Mengxi trading line.

Writes data/trading/daily_sample_2026-07-25.csv with the columns defined in
v1.3 Appendix A.1 (日清分列结构). For demo / regression / interface checks only —
not real market data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parents[3] / "data" / "trading" / "daily_sample_2026-07-25.csv"


def main(seed: int = 42) -> Path:
    rng = np.random.default_rng(seed)
    horizon = 96
    t = np.arange(horizon)
    # Diurnal shape: cheap at night, peak morning/evening
    shape = 0.5 * np.sin((t - 20) / 96 * 2 * np.pi) + 0.5 * np.sin((t - 60) / 96 * 4 * np.pi)
    base_price = 300.0 + 60.0 * shape + rng.normal(0, 8, horizon)
    p_dayah = np.clip(base_price, 50, 1500)
    p_real = np.clip(base_price + rng.normal(0, 15, horizon), 50, 1500)
    p_long = np.full(horizon, 310.0)
    load = 10.0 + 3.0 * shape + rng.normal(0, 0.5, horizon)
    q_real_load = np.clip(load, 0.5, None)
    # Mid-long covers ~80% of load
    q_long = 0.8 * q_real_load
    # Historical actual net load ≈ load (no storage in baseline history)
    q_real = q_real_load.copy()

    df = pd.DataFrame(
        {
            "p_long": p_long.round(2),
            "Q_long": q_long.round(4),
            "p_dayah": p_dayah.round(2),
            "p_real": p_real.round(2),
            "Q_real": q_real.round(4),
            "Q_real_load": q_real_load.round(4),
        }
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    return OUT


if __name__ == "__main__":
    path = main()
    print(f"wrote {path}")
