"""Manual regressions for archived data-provider investment profiles."""

from pathlib import Path

import pandas as pd
import pytest


def test_build_daily_energy_targets_expands_monthly_values():
    from ele_trading.data_provider.todo.load_profile import (
        build_daily_energy_targets,
    )

    daily = build_daily_energy_targets(
        target_year=2025,
        monthly_energy_targets={1: 310.0, 2: 280.0},
    )

    assert daily[pd.Timestamp("2025-01-01").date()] == pytest.approx(10.0)
    assert daily[pd.Timestamp("2025-02-01").date()] == pytest.approx(10.0)
    assert len(daily) == 59


def test_fill_missing_load_by_daily_energy_returns_quality_flags():
    from ele_trading.data_provider.todo.load_profile import (
        fill_missing_load_by_daily_energy,
    )

    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2025-01-01 00:00:00",
                    "2025-01-01 00:15:00",
                    "2025-01-01 00:30:00",
                    "2025-01-01 00:45:00",
                ]
            ),
            "load_kw": [10.0, None, 30.0, None],
            "is_original": [True, False, True, False],
            "is_interpolated": [False, False, False, False],
            "is_shifted_from_history": [False, False, False, False],
            "is_filled_by_nearest_day": [False, False, False, False],
            "source_file": ["raw.xlsx"] * 4,
        }
    )

    result = fill_missing_load_by_daily_energy(
        df=frame,
        target_year=2025,
        daily_energy_targets={pd.Timestamp("2025-01-01").date(): 30.0},
        freq="15min",
    )

    assert result["load_kw"].notna().all()
    assert result["is_interpolated"].sum() == 2
    assert (result["quality_score"] <= 1.0).all()


def test_build_load_profile_from_raw_covers_target_year(tmp_path: Path):
    from ele_trading.data_provider.todo.load_profile import (
        LoadProfileBuildConfig,
        build_load_profile_from_raw,
    )

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    pd.DataFrame(
        {
            "数据日期": ["2024-09-01"] * 4,
            "时间": ["00:00:00", "00:15:00", "00:30:00", "00:45:00"],
            "功率(KW)": [10.0, 20.0, 30.0, 40.0],
        }
    ).to_excel(raw_dir / "sample.xlsx", index=False)

    result = build_load_profile_from_raw(
        raw_dir,
        LoadProfileBuildConfig(
            target_year=2025,
            freq="15min",
            date_col="数据日期",
            time_col="时间",
            power_col="功率(KW)",
            monthly_energy_targets=None,
            history_source_year=2024,
            history_source_month_start=9,
            smoothing_window=3,
            fill_missing_points=False,
            fill_missing_days=True,
        ),
    )

    assert result.data["timestamp"].dt.year.nunique() == 1
    assert result.data["timestamp"].dt.year.iloc[0] == 2025
    assert "filled_days_count" in result.summary


def test_build_investment_case_dataset_keeps_net_load():
    from ele_trading.data_provider.todo.case_dataset import (
        build_investment_case_dataset,
    )

    index = pd.date_range(
        "2025-01-01 00:00:00",
        periods=3,
        freq="h",
        tz="Asia/Shanghai",
    )
    load = pd.DataFrame(
        {
            "timestamp": index,
            "load_kw": [100.0, 120.0, 90.0],
            "quality_score": [1.0, 0.9, 1.0],
        }
    )
    pv = pd.Series([10.0, 20.0, 0.0], index=index)
    wind = pd.Series([5.0, 5.0, 5.0], index=index)
    prices = pd.DataFrame(
        {
            "timestamp": index,
            "buy_price": [0.5, 0.6, 0.4],
            "sell_price": [0.2, 0.2, 0.2],
        }
    )

    investment = build_investment_case_dataset(load, pv, wind, prices)

    assert "net_load_kw" in investment.frame.columns
