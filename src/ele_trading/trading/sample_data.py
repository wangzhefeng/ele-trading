"""Provider boundary for repository trading demo data."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pandas as pd

from ele_trading.trading.contracts import PositionState


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
