"""investment_estimation.utils.bess_charge_discharge_plot 功能测试。

覆盖全部公共函数：read_price / process_strategy_data / read_load /
get_month_colors / plot_data，以及 main() 端到端冒烟（依赖 demo 数据，
缺失时跳过）。绘图统一用 Agg 无界面后端，断言产物为有效 PNG。
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # 必须在导入 pyplot（即被测模块）之前设置无界面后端

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from investment_estimation.utils.bess_charge_discharge_plot import (
    get_month_colors,
    main,
    plot_data,
    process_strategy_data,
    read_load,
    read_price,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _is_valid_png(path: Path) -> bool:
    return path.is_file() and path.read_bytes()[:8] == PNG_SIGNATURE


# ---------------------------------------------------------------------------
# get_month_colors
# ---------------------------------------------------------------------------


def test_get_month_colors_returns_hex_per_month():
    colors = get_month_colors([7, 8, 9])
    assert len(colors) == 3
    assert all(isinstance(c, str) and c.startswith("#") and len(c) == 7 for c in colors)
    # 已知映射保持稳定
    assert colors[0] == get_month_colors([7])[0]


def test_get_month_colors_unknown_month_falls_back():
    colors = get_month_colors([13])  # 13 月不存在
    assert colors == ["#4C78A8"]  # 回退默认色


# ---------------------------------------------------------------------------
# process_strategy_data
# ---------------------------------------------------------------------------


def test_process_strategy_data_aggregates_by_year_month_hour(tmp_path):
    # 两天的同一小时取均值：hour=0 的 value 10 与 20 → 15
    rows = pd.DataFrame(
        {
            "time": [
                "2025-07-10 00:00:00",
                "2025-07-11 00:00:00",
                "2025-07-10 01:00:00",
                "2025-07-11 01:00:00",
            ],
            "value": [10.0, 20.0, -5.0, -15.0],
        }
    )
    rows.to_csv(tmp_path / "src.csv", index=False)

    process_strategy_data(tmp_path, "src", "dst")

    out = pd.read_csv(tmp_path / "dst.csv")
    agg = out.set_index(["year", "month", "hour"])["value"]
    assert agg.loc[(2025, 7, 0)] == pytest.approx(15.0)
    assert agg.loc[(2025, 7, 1)] == pytest.approx(-10.0)
    assert set(out.columns) >= {"year", "month", "hour", "value"}


# ---------------------------------------------------------------------------
# read_load
# ---------------------------------------------------------------------------


def test_read_load_pivots_hour_x_year_month(tmp_path):
    processed = pd.DataFrame(
        {
            "year": [2025, 2025, 2026, 2026],
            "month": [7, 7, 1, 1],
            "hour": [0, 1, 0, 1],
            "value": [100.0, 110.0, 50.0, 60.0],
        }
    )
    processed.to_csv(tmp_path / "load.csv", index=False)

    df = read_load(tmp_path, "load", month_list=[7, 1])

    assert df["hour"].tolist() == [f"{h:02d}:00" for h in range(24)]
    assert {"2025-7", "2026-1"} <= set(df.columns)
    assert df.loc[df["hour"] == "00:00", "2025-7"].iloc[0] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# read_price
# ---------------------------------------------------------------------------


def test_read_price_filters_to_target_day_hourly(tmp_path):
    price = pd.DataFrame(
        {
            "time": [
                "2025-07-09 23:00:00",  # 前一天，应被排除
                "2025-07-10 00:00:00",
                "2025-07-10 00:30:00",  # 半小时，应被排除（只留整点）
                "2025-07-10 01:00:00",
                "2025-07-11 00:00:00",  # 次日，应被排除
            ],
            "value": [0.1, 0.2, 0.3, 0.4, 0.5],
            "type": ["低", "低", "低", "谷", "平"],
        }
    )
    price.to_csv(tmp_path / "ele_price.csv", index=False)

    df = read_price(tmp_path, "ele_price", year=2025, month=7)

    assert df["hour"].tolist() == ["00:00", "01:00"]
    assert df["type"].tolist() == ["低", "谷"]


# ---------------------------------------------------------------------------
# plot_data
# ---------------------------------------------------------------------------


def _make_plot_df(columns: dict[str, list]) -> pd.DataFrame:
    hours = [f"{h:02d}:00" for h in range(24)]
    # 交替电价时段背景
    types = ["谷"] * 6 + ["平"] * 4 + ["峰"] * 4 + ["平"] * 4 + ["谷"] * 6
    data = {"hour": hours, "type": types}
    data.update(columns)
    return pd.DataFrame(data)


def test_plot_data_multi_month_renders_png(tmp_path):
    rng = np.random.default_rng(42)
    df = _make_plot_df(
        {
            "2025-7": rng.uniform(-5000, 20000, 24),
            "2025-8": rng.uniform(-5000, 20000, 24),
            "2025-9": rng.uniform(-5000, 20000, 24),
        }
    )
    out = tmp_path / "夏季"

    plot_data(
        df,
        title="夏季",
        img_dir=out.parent,
        year_list=[2025, 2025, 2025],
        month_list=[7, 8, 9],
        text_position=600,
    )

    assert _is_valid_png(tmp_path / "夏季.png")


def test_plot_data_two_month_branch_renders_png(tmp_path):
    rng = np.random.default_rng(7)
    df = _make_plot_df(
        {
            "2025-12": rng.uniform(-5000, 20000, 24),
            "2026-1": rng.uniform(-5000, 20000, 24),
        }
    )

    plot_data(
        df,
        title="冬季",
        img_dir=tmp_path,
        year_list=[2025, 2026],
        month_list=[12, 1],
        text_position=600,
    )

    assert _is_valid_png(tmp_path / "冬季.png")


# ---------------------------------------------------------------------------
# main() 端到端冒烟（依赖未跟踪的 demo 数据，缺失时跳过）
# ---------------------------------------------------------------------------


_DEMO_DATA = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "bess_charge_discharge"
    / "wuhu"
    / "ele_price.csv"
)


@pytest.mark.skipif(
    not _DEMO_DATA.exists(),
    reason="demo 数据 data/bess_charge_discharge/wuhu/ 未跟踪，fresh clone 下缺失",
)
def test_main_end_to_end_produces_seasonal_reports():
    main()

    out_dir = (
        Path(__file__).resolve().parents[2]
        / "results"
        / "bess_charge_discharge"
        / "wuhu"
    )
    # 夏/冬/其他三张典型日报表
    pngs = list(out_dir.glob("*.png"))
    assert len(pngs) >= 3
    assert all(_is_valid_png(p) for p in pngs)
