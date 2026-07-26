from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ..quality import (
    compute_quality_score,
    ensure_datetime_column,
    ensure_unique_timestamps,
)


@dataclass(slots=True)
class LoadProfileBuildConfig:
    target_year: int
    freq: str
    date_col: str
    time_col: str
    power_col: str
    monthly_energy_targets: dict[int, float] | None
    history_source_year: int | None
    history_source_month_start: int | None
    smoothing_window: int
    fill_missing_points: bool
    fill_missing_days: bool


@dataclass(slots=True)
class LoadProfileResult:
    data: pd.DataFrame
    summary: dict[str, float | int | str]


def read_load_excel_folder(
    folder_path: str | Path,
    date_col: str = "数据日期",
    time_col: str = "时间",
    power_col: str = "功率(KW)",
    keep_source: bool = True,
) -> pd.DataFrame:
    folder = Path(folder_path)
    frames: list[pd.DataFrame] = []
    for file in sorted(folder.glob("*.xlsx")):
        df = pd.read_excel(file)
        out = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(
                    df[date_col].astype(str) + " " + df[time_col].astype(str),
                    errors="coerce",
                ),
                "load_kw": pd.to_numeric(df[power_col], errors="coerce"),
            }
        )
        out["source_file"] = file.name if keep_source else ""
        out = out.dropna(subset=["timestamp"]).reset_index(drop=True)
        out["is_original"] = out["load_kw"].notna()
        out["is_interpolated"] = False
        out["is_shifted_from_history"] = False
        out["is_filled_by_nearest_day"] = False
        frames.append(out)

    if not frames:
        raise ValueError(f"目录 {folder} 下未找到可读取的 Excel 文件")

    result = pd.concat(frames, ignore_index=True)
    result = ensure_datetime_column(result)
    ensure_unique_timestamps(result)
    result["quality_score"] = compute_quality_score(result)
    return result


def build_daily_energy_targets(target_year: int, monthly_energy_targets: dict[int, float]) -> dict[object, float]:
    daily_energy: dict[object, float] = {}
    for month, month_energy in sorted(monthly_energy_targets.items()):
        days = monthrange(target_year, month)[1]
        daily_value = month_energy / days
        for day in range(1, days + 1):
            daily_energy[pd.Timestamp(target_year, month, day).date()] = daily_value
    return daily_energy


def fill_missing_load_by_daily_energy(
    df: pd.DataFrame,
    target_year: int,
    daily_energy_targets: dict[object, float],
    freq: str = "15min",
) -> pd.DataFrame:
    result = ensure_datetime_column(df)
    ensure_unique_timestamps(result)
    dt_hours = pd.to_timedelta(freq).total_seconds() / 3600.0

    result["date"] = result["timestamp"].dt.date
    result["year"] = result["timestamp"].dt.year

    for date, target_energy in daily_energy_targets.items():
        day_mask = (result["date"] == date) & (result["year"] == target_year)
        day_df = result.loc[day_mask]
        if day_df.empty:
            continue

        day_indexed = day_df.set_index("timestamp")
        known_mask = day_indexed["load_kw"].notna()
        missing_mask = day_indexed["load_kw"].isna()
        if missing_mask.sum() == 0:
            continue

        known_energy = float((day_indexed.loc[known_mask, "load_kw"] * dt_hours).sum())
        missing_energy = target_energy - known_energy
        if missing_energy <= 0:
            fill_values = np.zeros(missing_mask.sum(), dtype=float)
        else:
            weight = (
                day_indexed["load_kw"]
                .interpolate(method="time")
                .ffill()
                .bfill()
                .clip(lower=0)
            )
            weights = weight.values[missing_mask.values]
            if np.allclose(weights.sum(), 0.0):
                weights = np.ones_like(weights, dtype=float)
            fill_values = weights / weights.sum() * (missing_energy / dt_hours)

        missing_idx = day_df.index[missing_mask.values]
        result.loc[missing_idx, "load_kw"] = fill_values
        result.loc[missing_idx, "is_interpolated"] = True

    result = result.drop(columns=["date", "year"])
    result["quality_score"] = compute_quality_score(result)
    return result


def smooth_history_shape(
    df: pd.DataFrame,
    source_year: int,
    month_start: int,
    window: int = 3,
) -> pd.DataFrame:
    result = ensure_datetime_column(df)
    history_mask = (result["timestamp"].dt.year == source_year) & (result["timestamp"].dt.month >= month_start)
    history = result.loc[history_mask]
    if history.empty:
        return result

    smoothed = (
        history.set_index("timestamp")["load_kw"]
        .interpolate(method="time")
        .rolling(window, center=True, min_periods=1)
        .mean()
    )
    result.loc[history.index, "load_kw"] = smoothed.values
    return result


def shift_history_profile(
    df: pd.DataFrame,
    source_year: int,
    target_year: int,
    month_start: int,
) -> pd.DataFrame:
    history = df[
        (df["timestamp"].dt.year == source_year)
        & (df["timestamp"].dt.month >= month_start)
    ].copy()
    if history.empty:
        return history
    history["timestamp"] = history["timestamp"].apply(lambda ts: ts.replace(year=target_year))
    history["is_original"] = False
    history["is_shifted_from_history"] = True
    history["quality_score"] = compute_quality_score(history)
    return history


def fill_missing_days_by_reference(
    df_raw: pd.DataFrame,
    target_year: int,
    reference_strategy: str = "nearest",
) -> pd.DataFrame:
    df = ensure_datetime_column(df_raw)
    all_days = pd.date_range(f"{target_year}-01-01", f"{target_year}-12-31", freq="D").date
    existing_days = df["timestamp"].dt.date.unique()
    missing_days = sorted(set(all_days) - set(existing_days))

    if not missing_days:
        df["quality_score"] = compute_quality_score(df)
        return df

    def _pick_reference(day: object) -> object:
        candidates = list(existing_days)
        target = pd.Timestamp(day)
        if reference_strategy == "same_month":
            month_candidates = [d for d in candidates if pd.Timestamp(d).month == target.month]
            if month_candidates:
                candidates = month_candidates
        elif reference_strategy == "same_weekday":
            weekday_candidates = [d for d in candidates if pd.Timestamp(d).weekday() == target.weekday()]
            if weekday_candidates:
                candidates = weekday_candidates
        return min(candidates, key=lambda value: abs(pd.Timestamp(value) - target))

    for day in missing_days:
        ref_day = _pick_reference(day)
        day_curve = df[df["timestamp"].dt.date == ref_day].copy()
        day_curve["timestamp"] = day_curve["timestamp"].apply(
            lambda ts: ts.replace(year=day.year, month=day.month, day=day.day)
        )
        day_curve["is_original"] = False
        day_curve["is_filled_by_nearest_day"] = True
        df = pd.concat([df, day_curve], ignore_index=True)

    df = ensure_datetime_column(df)
    ensure_unique_timestamps(df)
    df["quality_score"] = compute_quality_score(df)
    return df


def build_load_profile(config: LoadProfileBuildConfig, raw_df: pd.DataFrame) -> LoadProfileResult:
    result_df = raw_df.copy()
    if config.fill_missing_points and config.monthly_energy_targets:
        targets = build_daily_energy_targets(config.target_year, config.monthly_energy_targets)
        result_df = fill_missing_load_by_daily_energy(
            result_df,
            target_year=config.target_year,
            daily_energy_targets=targets,
            freq=config.freq,
        )

    if config.history_source_year is not None and config.history_source_month_start is not None:
        result_df = smooth_history_shape(
            result_df,
            source_year=config.history_source_year,
            month_start=config.history_source_month_start,
            window=config.smoothing_window,
        )
        shifted = shift_history_profile(
            result_df,
            source_year=config.history_source_year,
            target_year=config.target_year,
            month_start=config.history_source_month_start,
        )
        if not shifted.empty:
            result_df = pd.concat([result_df, shifted], ignore_index=True)
            result_df = ensure_datetime_column(result_df)
            result_df = result_df.drop_duplicates(subset=["timestamp"], keep="first").reset_index(drop=True)

    result_df = result_df[result_df["timestamp"].dt.year == config.target_year].copy()
    if config.fill_missing_days:
        result_df = fill_missing_days_by_reference(result_df, target_year=config.target_year)
    result_df["quality_score"] = compute_quality_score(result_df)

    summary = {
        "target_year": config.target_year,
        "rows": len(result_df),
        "filled_days_count": int(result_df["is_filled_by_nearest_day"].sum()),
        "interpolated_points_count": int(result_df["is_interpolated"].sum()),
    }
    return LoadProfileResult(data=result_df.reset_index(drop=True), summary=summary)


def build_load_profile_from_raw(
    raw_data_dir: str | Path,
    config: LoadProfileBuildConfig,
) -> LoadProfileResult:
    raw_df = read_load_excel_folder(
        raw_data_dir,
        date_col=config.date_col,
        time_col=config.time_col,
        power_col=config.power_col,
    )
    return build_load_profile(config=config, raw_df=raw_df)


def save_load_profile(result: LoadProfileResult, output_path: str | Path) -> None:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.data.to_csv(output, index=False, encoding="utf-8")
