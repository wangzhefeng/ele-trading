from __future__ import annotations

import pandas as pd

from .config import DemandResult


def _setup_chinese_font() -> None:
    """配置 matplotlib 中文字体，避免乱码。"""
    import matplotlib
    from matplotlib.font_manager import fontManager

    # 按优先级尝试常见中文字体
    candidates = [
        "SimHei",           # Windows 黑体
        "Microsoft YaHei",  # Windows 微软雅黑
        "STHeiti",          # macOS 华文黑体
        "Heiti TC",         # macOS 黑体-繁
        "PingFang SC",      # macOS 苹方简
        "PingFang HK",      # macOS 苹方港
        "Songti SC",        # macOS 宋体
        "Arial Unicode MS", # macOS
        "Lantinghei SC",    # macOS 兰亭黑
        "WenQuanYi Micro Hei",  # Linux 文泉驿
        "Noto Sans CJK SC",     # Linux Noto
        "DejaVu Sans",          # fallback
    ]
    available = {f.name for f in fontManager.ttflist}
    for name in candidates:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name] + matplotlib.rcParams.get("font.sans-serif", [])
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def plot_load_with_demand(power: pd.Series, result: DemandResult) -> None:
    """绘制负荷曲线与最大需量线。

    Parameters
    ----------
    power : Series
        原始功率时间序列（index=DatetimeIndex）。
    result : DemandResult
        需量计算结果。
    """
    import matplotlib.pyplot as plt

    _setup_chinese_font()
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(power.index, power.values, linewidth=0.6, label="负荷功率")
    ax.axhline(
        result.max_demand,
        color="red",
        linestyle="--",
        linewidth=1.2,
        label=f"最大需量 {result.max_demand:.1f} kW",
    )
    ax.scatter(
        [result.peak_timestamp],
        [result.max_demand],
        color="red",
        zorder=5,
        s=60,
    )
    ax.annotate(
        f"{result.max_demand:.1f} kW\n{result.peak_timestamp:%Y-%m-%d %H:%M}",
        xy=(result.peak_timestamp, result.max_demand),
        xytext=(10, 15),
        textcoords="offset points",
        fontsize=9,
        arrowprops=dict(arrowstyle="->", color="red"),
    )
    ax.set_xlabel("时间")
    ax.set_ylabel("功率 (kW)")
    ax.set_title("负荷曲线与最大需量")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    plt.show()


def plot_monthly_demand(result: DemandResult) -> None:
    """绘制每月最大需量柱状图。

    Parameters
    ----------
    result : DemandResult
        需量计算结果。
    """
    import matplotlib.pyplot as plt

    _setup_chinese_font()
    fig, ax = plt.subplots(figsize=(10, 5))
    months = [str(p) for p in result.monthly_max.index]
    values = result.monthly_max.values
    bars = ax.bar(months, values, color="steelblue", width=0.6)
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(values) * 0.01,
            f"{val:.1f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_xlabel("月份")
    ax.set_ylabel("最大需量 (kW)")
    ax.set_title("每月最大需量")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    plt.show()
