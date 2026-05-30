"""Plot helpers for PV + energy-storage strategy results."""

from pathlib import Path

import pandas as pd


PRICE_TYPE_BACKGROUND_STYLES = {
    "低": {"facecolor": "#D9F3F7", "alpha": 0.95},
    "谷": {"facecolor": "#D9F3F7", "alpha": 0.95},
    "平": {"facecolor": "#FFF7BF", "alpha": 0.75},
    "高": {"facecolor": "#FFD9D4", "alpha": 0.75},
    "峰": {"facecolor": "#FFD9D4", "alpha": 0.75},
    "尖": {"facecolor": "#F7D6FF", "alpha": 0.9},
}
DEFAULT_PRICE_TYPE_BACKGROUND_STYLE = {"facecolor": "#E5E7EB", "alpha": 0.55}
PRICE_TYPE_BACKGROUND_LABELS = {
    "低": "price_type_low",
    "谷": "price_type_valley",
    "平": "price_type_flat",
    "高": "price_type_high",
    "峰": "price_type_peak",
    "尖": "price_type_sharp",
}


def build_price_type_spans(
    ele_price_df: pd.DataFrame,
    default_freq_minutes: int = 15,
) -> list[dict]:
    """Build contiguous background spans from a price-type time series."""
    if ele_price_df.empty or "type" not in ele_price_df.columns:
        return []

    price_df = _ensure_datetime_index(ele_price_df)
    price_types = price_df["type"].astype(str).str.strip()
    index = price_df.index
    if len(index) > 1:
        step = index.to_series().diff().dropna().median()
    else:
        step = pd.Timedelta(minutes=default_freq_minutes)
    if pd.isna(step) or step <= pd.Timedelta(0):
        step = pd.Timedelta(minutes=default_freq_minutes)

    spans = []
    current_type = price_types.iloc[0]
    start_time = index[0]
    for idx in range(1, len(price_df)):
        if price_types.iloc[idx] == current_type:
            continue
        spans.append({"start": start_time, "end": index[idx], "type": current_type})
        current_type = price_types.iloc[idx]
        start_time = index[idx]
    spans.append({"start": start_time, "end": index[-1] + step, "type": current_type})
    return spans


def add_price_type_background(ax, ele_price_df: pd.DataFrame):
    """Add price-type background bands to a Matplotlib axis."""
    from matplotlib.patches import Patch

    spans = build_price_type_spans(ele_price_df)
    legend_handles = []
    plotted_types = set()
    for span in spans:
        price_type = span["type"]
        style = PRICE_TYPE_BACKGROUND_STYLES.get(
            price_type,
            DEFAULT_PRICE_TYPE_BACKGROUND_STYLE,
        )
        ax.axvspan(
            span["start"],
            span["end"],
            facecolor=style["facecolor"],
            alpha=style["alpha"],
            zorder=0,
        )
        if price_type in plotted_types:
            continue
        legend_handles.append(
            Patch(
                facecolor=style["facecolor"],
                edgecolor="none",
                alpha=style["alpha"],
                label=PRICE_TYPE_BACKGROUND_LABELS.get(price_type, "price_type_other"),
            )
        )
        plotted_types.add(price_type)
    return legend_handles


def configure_matplotlib_chinese_font():
    """Configure a local Chinese-capable font when one is available."""
    import matplotlib as mpl
    from matplotlib import font_manager as fm

    preferred_fonts = [
        "Arial Unicode MS",
        "PingFang SC",
        "Heiti TC",
        "Songti SC",
        "SimHei",
        "Noto Sans CJK SC",
    ]
    available_fonts = {font.name for font in fm.fontManager.ttflist}
    for font_name in preferred_fonts:
        if font_name in available_fonts:
            current_fonts = mpl.rcParams.get("font.sans-serif", [])
            mpl.rcParams["font.sans-serif"] = [font_name, *current_fonts]
            mpl.rcParams["axes.unicode_minus"] = False
            return font_name
    return None


def build_monthly_demand_power_lines(
    demand_load_df: pd.DataFrame,
    pv_load_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build monthly max-demand reference lines for plotting."""
    demand_df = _ensure_datetime_index(demand_load_df)
    pv_df = _ensure_datetime_index(pv_load_df)
    strategy_df = _ensure_datetime_index(strategy_df)

    load_only_grid = demand_df["value"]
    pv_only_grid = (demand_df["value"] - pv_df["value"]).clip(lower=0)
    pv_bess_grid = strategy_df["grid_import"]

    return pd.DataFrame(
        {
            "load_only_monthly_max": load_only_grid.resample("ME").max(),
            "pv_only_monthly_max": pv_only_grid.resample("ME").max(),
            "pv_bess_monthly_max": pv_bess_grid.resample("ME").max(),
        }
    )


def plot_strategy_power_detail(
    demand_load_df: pd.DataFrame,
    pv_load_df: pd.DataFrame,
    ele_price_df: pd.DataFrame,
    strategy_df: pd.DataFrame,
    es_scale: float,
    title: str | None = None,
    save_path: str | Path | None = None,
    show: bool = False,
    start_time=None,
    end_time=None,
    date=None,
):
    """Plot load, PV, price type, dispatch flows, battery power, and SOC."""
    import matplotlib.pyplot as plt

    configure_matplotlib_chinese_font()

    demand_load_df = _ensure_datetime_index(demand_load_df)
    pv_load_df = _ensure_datetime_index(pv_load_df)
    ele_price_df = _ensure_datetime_index(ele_price_df)
    strategy_df = _ensure_datetime_index(strategy_df)

    monthly_demand_power_lines = build_monthly_demand_power_lines(
        demand_load_df,
        pv_load_df,
        strategy_df,
    )

    demand_load_df = _slice_frame(demand_load_df, date, start_time, end_time)
    pv_load_df = _slice_frame(pv_load_df, date, start_time, end_time)
    ele_price_df = _slice_frame(ele_price_df, date, start_time, end_time)
    strategy_df = _slice_frame(strategy_df, date, start_time, end_time)

    if strategy_df.empty:
        raise ValueError("No strategy data found for the selected time range or date")

    battery_power = strategy_df["battery_discharge"] - strategy_df["battery_charge"]

    fig, ax_power = plt.subplots(1, 1, figsize=(18, 8))
    fig.suptitle(title or f"PV + ES Strategy Detail - ES {es_scale:g} kW", fontsize=14)
    price_background_handles = add_price_type_background(ax_power, ele_price_df)

    demand_load_line = ax_power.plot(
        demand_load_df.index,
        demand_load_df["value"],
        label="demand_load(kW)",
        color="#111827",
        linewidth=2.0,
        alpha=0.98,
        zorder=3,
    )[0]
    pv_load_line = ax_power.plot(
        pv_load_df.index,
        pv_load_df["value"],
        label="pv_load(kW)",
        color="#F59E0B",
        linewidth=1.9,
        alpha=0.98,
        zorder=3,
    )[0]
    grid_import_line = ax_power.plot(
        strategy_df.index,
        strategy_df["grid_import"],
        label="grid_import(kW)",
        color="#0057B8",
        linewidth=2.0,
        alpha=0.98,
        zorder=3,
    )[0]
    pv_to_load_line = ax_power.plot(
        strategy_df.index,
        strategy_df["pv_to_load"],
        label="pv_to_load(kW)",
        color="#16A34A",
        linewidth=1.7,
        alpha=0.95,
        zorder=3,
    )[0]
    pv_to_battery_line = ax_power.plot(
        strategy_df.index,
        strategy_df["pv_to_battery"],
        label="pv_to_battery(kW)",
        color="#7C3AED",
        linewidth=1.8,
        linestyle="--",
        alpha=0.96,
        zorder=3,
    )[0]
    pv_to_grid_line = ax_power.plot(
        strategy_df.index,
        strategy_df["pv_to_grid"],
        label="pv_to_grid(kW)",
        color="#0891B2",
        linewidth=1.8,
        linestyle=":",
        alpha=0.96,
        zorder=3,
    )[0]
    battery_power_line = ax_power.plot(
        strategy_df.index,
        battery_power,
        label="battery_power(kW, +discharge/-charge)",
        color="#DC2626",
        linewidth=1.9,
        alpha=0.98,
        zorder=3,
    )[0]

    monthly_lines = _plot_monthly_reference_lines(
        ax_power,
        strategy_df,
        monthly_demand_power_lines,
    )
    ax_power.axhline(0, color="black", linewidth=0.8, alpha=0.5, zorder=2)
    ax_power.set_ylabel("Power(kW)")
    ax_power.grid(True, alpha=0.3)

    ax_soc = ax_power.twinx()
    soc_line = ax_soc.plot(
        strategy_df.index,
        strategy_df["soc"],
        label="soc(kWh)",
        color="#64748B",
        alpha=0.85,
        linewidth=1.5,
        zorder=3,
    )[0]
    ax_soc.set_ylabel("SOC(kWh)")

    _add_grouped_legends(
        fig,
        raw_data_handles=[demand_load_line, pv_load_line],
        demand_handles=[
            grid_import_line,
            *[line for line in monthly_lines if line.get_label() != "_nolegend_"],
        ],
        price_background_handles=price_background_handles,
        pv_handles=[pv_to_load_line, pv_to_battery_line, pv_to_grid_line],
        battery_handles=[battery_power_line, soc_line],
    )
    ax_power.set_xlabel("Time")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.18, 0.96, 0.95))

    if save_path is not None:
        output_path = Path(save_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=160)
    else:
        output_path = None

    if show:
        plt.show()
    elif output_path is not None:
        plt.close(fig)

    return output_path if output_path is not None else fig


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.index, pd.DatetimeIndex):
        return df.sort_index()
    if "time" in df.columns:
        result = df.copy()
        result["time"] = pd.to_datetime(result["time"])
        return result.set_index("time").sort_index()
    raise ValueError("DataFrame must use a DatetimeIndex or contain a 'time' column")


def _slice_frame(df: pd.DataFrame, date, start_time, end_time) -> pd.DataFrame:
    if date is not None:
        date_values = date if isinstance(date, (list, tuple, set)) else [date]
        mask = pd.Series(False, index=df.index)
        for date_i in date_values:
            day = pd.to_datetime(date_i).normalize()
            mask |= df.index.normalize() == day
        return df.loc[mask.to_numpy(dtype=bool)]

    start = pd.to_datetime(start_time) if start_time is not None else df.index.min()
    end = pd.to_datetime(end_time) if end_time is not None else df.index.max()
    return df[(df.index >= start) & (df.index <= end)]


def _plot_monthly_reference_lines(
    ax_power,
    strategy_df: pd.DataFrame,
    monthly_demand_power_lines: pd.DataFrame,
):
    monthly_line_styles = {
        "load_only_monthly_max": {
            "label": "load_only_monthly_max(kW)",
            "color": "#111827",
        },
        "pv_only_monthly_max": {
            "label": "pv_only_monthly_max(kW)",
            "color": "#9A3412",
        },
        "pv_bess_monthly_max": {
            "label": "pv_bess_monthly_max(kW)",
            "color": "#003B8E",
        },
    }
    monthly_lines = []
    plotted_monthly_labels = set()
    for month, month_df in strategy_df.groupby(strategy_df.index.to_period("M")):
        month_end = month.to_timestamp(how="end").normalize()
        if month_end not in monthly_demand_power_lines.index:
            continue
        x_start = month_df.index.min()
        x_end = month_df.index.max()
        for col, style in monthly_line_styles.items():
            label = style["label"] if col not in plotted_monthly_labels else "_nolegend_"
            monthly_lines.extend(
                ax_power.plot(
                    [x_start, x_end],
                    [
                        monthly_demand_power_lines.loc[month_end, col],
                        monthly_demand_power_lines.loc[month_end, col],
                    ],
                    label=label,
                    color=style["color"],
                    linestyle="--",
                    linewidth=1.5,
                    alpha=0.90,
                    zorder=2,
                )
            )
            plotted_monthly_labels.add(col)
    return monthly_lines


def _add_grouped_legends(
    fig,
    raw_data_handles,
    demand_handles,
    price_background_handles,
    pv_handles,
    battery_handles,
) -> None:
    legend_groups = [
        ("raw_data", raw_data_handles, (0.04, 0.02), 1),
        ("demand", demand_handles, (0.18, 0.02), 2),
        ("price_type", price_background_handles, (0.43, 0.02), 2),
        ("pv_allocation", pv_handles, (0.58, 0.02), 2),
        ("battery", battery_handles, (0.82, 0.02), 1),
    ]
    for legend_title, handles, anchor, ncol in legend_groups:
        if not handles:
            continue
        fig.legend(
            handles=handles,
            labels=[handle.get_label() for handle in handles],
            title=legend_title,
            loc="lower left",
            bbox_to_anchor=anchor,
            borderaxespad=0.0,
            frameon=True,
            ncol=ncol,
            fontsize=8,
            title_fontsize=9,
        )
