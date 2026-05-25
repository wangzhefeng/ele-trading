"""PV-storage plotting utility tests."""

from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")

from utils.pv_es_plot import plot_strategy_power_detail


def test_plot_strategy_power_detail_writes_png(tmp_path):
    """Plot helper should save a non-empty PNG without hard-coded data paths."""
    index = pd.date_range("2025-01-01 00:00:00", periods=8, freq="15min")
    demand_load_df = pd.DataFrame({"value": [100.0] * len(index)}, index=index)
    pv_load_df = pd.DataFrame({"value": [0.0, 0.0, 10.0, 20.0, 30.0, 20.0, 10.0, 0.0]}, index=index)
    ele_price_df = pd.DataFrame(
        {"value": [0.3] * len(index), "type": ["低"] * len(index)},
        index=index,
    )
    strategy_df = pd.DataFrame(
        {
            "grid_import": [100.0] * len(index),
            "pv_to_load": pv_load_df["value"],
            "pv_to_battery": [0.0] * len(index),
            "pv_to_grid": [0.0] * len(index),
            "battery_charge": [0.0] * len(index),
            "battery_discharge": [0.0] * len(index),
            "soc": [0.0] * len(index),
        },
        index=index,
    )
    output_path = tmp_path / "strategy.png"

    result_path = plot_strategy_power_detail(
        demand_load_df=demand_load_df,
        pv_load_df=pv_load_df,
        ele_price_df=ele_price_df,
        strategy_df=strategy_df,
        es_scale=100,
        title="plot smoke",
        save_path=output_path,
    )

    assert result_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0
