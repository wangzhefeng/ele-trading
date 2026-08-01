"""Provider boundary for repository trading demo data.

与 ``ele_trading.data_provider.sample_data`` 区分：后者管理 prices/config/scenarios
最小样例的加载入口；本模块负责蒙西 30 天日清分样例（daily_sample_*.csv）的
provider 与 fixture 生成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from ele_trading.domain.contracts import PositionState


class SampleTradingDataProvider:
    """Load versioned daily demo fixtures without exposing paths to apps."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._frames = {
            pd.Timestamp(path.stem.removeprefix("daily_sample_")): (
                pd.read_csv(path)
            )
            for path in sorted(
                self.directory.glob("daily_sample_*.csv")
            )
        }
        if not self._frames:
            raise FileNotFoundError(
                f"no daily_sample_*.csv under {self.directory}"
            )

    @property
    def available_days(self) -> tuple[pd.Timestamp, ...]:
        return tuple(sorted(self._frames))

    def frame_for_day(self, day: pd.Timestamp) -> pd.DataFrame:
        key = pd.Timestamp(day).tz_localize(None).normalize()
        try:
            return self._frames[key].copy()
        except KeyError as exc:
            raise KeyError(f"no trading sample for {key.date()}") from exc

    def get_position_state(
        self,
        decision_time: pd.Timestamp,
        valid_time_index: pd.DatetimeIndex,
    ) -> PositionState:
        frame = self.frame_for_day(decision_time)
        if len(frame) != len(valid_time_index):
            raise ValueError(
                "sample position horizon must match valid_time_index"
            )
        day = pd.Timestamp(decision_time).date().isoformat()
        return PositionState(
            as_of=pd.Timestamp(decision_time),
            q_long=pd.Series(
                frame["Q_long"].to_numpy(dtype=float),
                index=valid_time_index,
            ),
            p_long=pd.Series(
                frame["p_long"].to_numpy(dtype=float),
                index=valid_time_index,
            ),
            monthly_positions={
                day[:7]: float(frame["Q_long"].sum())
            },
            source_version=f"daily_sample_{day}",
        )


class WalkForwardSeasonalNaiveProvider:
    """Demo walk-backtest forecast: each decision day forecasts from the prior observed day.

    Demo/backtest only (AGENTS.md data boundary — sample fixtures, not production data).
    For a request issued at decision-day midnight, the most recent sample day strictly
    before it is used as the seasonal-naive history, with a feature_as_of bounded by
    that prior day so no future information reaches the forecast.
    """

    def __init__(self, frames: Mapping[pd.Timestamp, pd.DataFrame]) -> None:
        self._frames = {
            pd.Timestamp(day).tz_localize(None): frame
            for day, frame in frames.items()
        }

    def forecast(self, request):
        from ele_trading.forecasting.seasonal_naive_provider import (
            SeasonalNaiveTradingForecastProvider,
        )

        issue = pd.Timestamp(request.issue_time)
        if issue.tzinfo is None:
            raise ValueError("issue_time must be timezone-aware")
        decision_date = issue.tz_localize(None).normalize()
        prior_days = [day for day in self._frames if day < decision_date]
        if not prior_days:
            raise ValueError(
                f"no prior sample day before {decision_date.date()} "
                "for walk-forward forecast"
            )
        history_day = max(prior_days)
        # Prior day fully observed by 23:45 local → available before next midnight.
        feature_as_of = (
            history_day + pd.Timedelta(days=1) - pd.Timedelta(minutes=15)
        ).tz_localize(issue.tzinfo)
        delegate = SeasonalNaiveTradingForecastProvider(
            self._frames[history_day],
            feature_as_of=feature_as_of,
        )
        return delegate.forecast(request)


# ---------------------------------------------------------------------------
# 样例 fixture 生成器：data/trading/daily_sample_*.csv 的权威来源
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "trading"

COLUMNS = ["p_long", "Q_long", "p_dayah", "p_real", "Q_real", "Q_real_load"]


def generate_day(rng: np.random.Generator, horizon: int = 96) -> pd.DataFrame:
    """生成单日 96 点日清分样例（列结构见 data/trading/README.md）。"""
    t = np.arange(horizon)
    # 日内形状：夜间低价、早晚双峰
    shape = 0.5 * np.sin((t - 20) / horizon * 2 * np.pi) + 0.5 * np.sin((t - 60) / horizon * 4 * np.pi)
    base_price = 300.0 + 60.0 * shape + rng.normal(0, 8, horizon)
    p_dayah = np.clip(base_price, 50, 1500)
    p_real = np.clip(base_price + rng.normal(0, 15, horizon), 50, 1500)
    p_long = np.full(horizon, 310.0 + rng.normal(0, 3))
    load = 10.0 + 3.0 * shape + rng.normal(0, 0.5, horizon)
    q_real_load = np.clip(load, 0.5, None)
    # 中长期覆盖约 97% 负荷（落在默认中长期考核带 [0.90, 1.05] 内，
    # 避免基线数据本身触发月度超额回收）
    q_long = 0.97 * q_real_load
    # 历史实际净负荷 ≈ 负荷（基线历史无储能）
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

    seed 固定保证可复现；单日主种子派生子种子（seed + i）使每日独立。
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
