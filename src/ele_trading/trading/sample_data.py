"""Generate synthetic 96-point daily settlement samples for the Mengxi trading line.

Writes data/trading/daily_sample_2026-07-DD.csv (default 30 days, 2026-07-01..30)
with the columns defined in v1.3 Appendix A.1 (日清分列结构).
For demo / regression / interface checks only — not real market data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "trading"

COLUMNS = ["p_long", "Q_long", "p_dayah", "p_real", "Q_real", "Q_real_load"]


def generate_day(rng: np.random.Generator, horizon: int = 96) -> pd.DataFrame:
    """生成单日 96 点日清分样例（v1.3 附录 A.1 列结构）。"""
    t = np.arange(horizon)
    # Diurnal shape: cheap at night, peak morning/evening
    shape = 0.5 * np.sin((t - 20) / horizon * 2 * np.pi) + 0.5 * np.sin((t - 60) / horizon * 4 * np.pi)
    base_price = 300.0 + 60.0 * shape + rng.normal(0, 8, horizon)
    p_dayah = np.clip(base_price, 50, 1500)
    p_real = np.clip(base_price + rng.normal(0, 15, horizon), 50, 1500)
    p_long = np.full(horizon, 310.0 + rng.normal(0, 3))
    load = 10.0 + 3.0 * shape + rng.normal(0, 0.5, horizon)
    q_real_load = np.clip(load, 0.5, None)
    # Mid-long covers ~97% of load（落在默认中长期考核带 [0.90, 1.05] 内，
    # 避免基线数据本身触发月度超额回收）
    q_long = 0.97 * q_real_load
    # Historical actual net load ≈ load (no storage in baseline history)
    q_real = q_real_load.copy()

    return pd.DataFrame(
        {
            "p_long": p_long.round(2),
            "Q_long": q_long.round(4),
            "p_dayah": p_dayah.round(2),
            "p_real": p_real.round(2),
            "Q_real": q_real.round(4),
            "Q_real_load": q_real_load.round(4),
        }
    )


def main(seed: int = 42, days: int = 30, start: str = "2026-07-01") -> list[Path]:
    """生成 ``days`` 天日清分样例，落 ``data/trading/daily_sample_YYYY-MM-DD.csv``。

    seed 固定保证可复现；单日主种子派生子种子使每日独立。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range(start, periods=days, freq="D")
    paths: list[Path] = []
    for i, day in enumerate(dates):
        rng = np.random.default_rng(seed + i)
        df = generate_day(rng)
        path = DATA_DIR / f"daily_sample_{day:%Y-%m-%d}.csv"
        df.to_csv(path, index=False)
        paths.append(path)
    return paths


if __name__ == "__main__":
    written = main()
    print(f"wrote {len(written)} files: {written[0].name} .. {written[-1].name}")
