# -*- coding: utf-8 -*-

# ***************************************************
# * File        : utils.py
# * Author      : Zhefeng Wang
# * Email       : wangzhefengr@163.com
# * Date        : 2023-05-22
# * Version     : 0.1.052220
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-whitegrid')
# plt.rcParams['font.sans-serif']=['SimHei', 'Arial Unicode MS'] # 用来正常显示中文标签
# plt.rcParams['axes.unicode_minus'] = False # 用来显示负号
plt.rcParams['figure.autolayout'] = True # layout
plt.rcParams['axes.grid'] = True # grid
plt.rc(
    "figure",
    autolayout=True,
    figsize=(11, 4.5),
    titleweight="bold",
    titlesize=18,
)
plt.rc(
    "axes",
    labelweight="bold",
    labelsize="large",
    titleweight="bold",
    titlesize=16,
    titlepad=10,
)
train_scatter_plot_params = dict(
    color="0.75",
    style=".-",
    linewidth=2,
    markeredgecolor="0.25",
    markerfacecolor="0.25",
    legend=True,
    label="Train trues",
)
test_scatter_plot_params = dict(
    color="C2",
    style=".-",
    linewidth=2,
    markeredgecolor="0.25",
    markerfacecolor="0.25",
    legend=True,
    label="Test trues",
)
fit_line_plot_params = dict(
    color="C0",
    style=".-",
    linewidth=2,
    legend=True,
    label="Train preds",
)
pred_line_plot_params = dict(
    color="C1",
    style=".-",
    linewidth=2,
    legend=True,
    label="Test preds",
)
fore_line_plot_params = dict(
    color="C3",
    style=".-",
    linewidth=2,
    legend=True,
    label="Forecast",
)
# global variable
LOGGING_LABEL = Path(__file__).name[:-3]


def series_plot(df: pd.DataFrame, time_col, value_col):
    """
    单时序图

    Args:
        df (_type_): 时序数据
        time_col (_type_): 时间变量
        value_col (_type_): 待预测变量
    """
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False
    
    plt.figure(figsize=(25, 8))
    plt.plot(df[time_col], df[value_col], marker = ".", linestyle = "-.", color='C0', linewidth=1.0)
    plt.legend()
    plt.xlabel(time_col)
    plt.ylabel(value_col)
    plt.title(f"{value_col} 时序图")
    plt.tight_layout()
    plt.grid(True)
    plt.show();


def plot_ele_series(df, year: str, month: str):
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False
    
    plt.figure(figsize=(16, 6))
    plt.plot(df.index, df["Load_kW"], label="负荷 Load (kW)", linewidth=1.5)
    plt.plot(df.index, df["Wind_kW"], label="风电 Wind (kW)", linewidth=1.2, alpha=0.85)
    plt.title(f"{year} 年 {month} 月负荷与风电出力对比（15min）")
    plt.xlabel("时间")
    plt.ylabel("功率 (kW)")
    plt.legend(loc="upper right")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show();


def plot_load_pv_wind_netload(df):
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False
    
    plt.figure(figsize=(16, 6))
    # plt.plot(df["Time"], df["P_kw"], label="负荷 Load (kW)", linewidth=1.8)
    plt.plot(df["Time"], df["PV_kw"], label="光伏 PV (kW)", linewidth=1.5)
    plt.plot(df["Time"], df["Wind_kw"], label="风电 Wind (kW)", linewidth=1.5)
    plt.plot(df["Time"], df["NetLoad_kw"], label="Net Load (kW)", linewidth=1.6, color="black")
    # plt.axhline(0, linestyle="--", color="red", alpha=0.7)
    plt.xlabel("Time")
    plt.ylabel("Power (kW)")
    plt.title("Load / PV / Wind / NetLoad(Net Load = Load - PV - Wind) Power Time Series")
    plt.legend(loc="upper right")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()


def plot_daily_pv_shape(pv_kw, date):
    """
    用于抽取某一天的光伏出力曲线并作图，主要服务于结果可视化和形状检查

    Args:
        pv_kw (_type_): _description_
        date (_type_): _description_
    """
    mask = pv_kw.index.date == pd.to_datetime(date).date()
    plt.figure(figsize=(8, 4))
    plt.plot(pv_kw.loc[mask])
    plt.title(f"PV output on {date}")
    plt.ylabel("kW")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.show()



# ============================================================
# 储能调度可视化
# ============================================================

def plot_bess_dispatch(
    charge_schedule,
    discharge_schedule,
    soc_schedule,
    time_index=None,
    title="储能充放电调度计划",
):
    """充放电功率 + SOC 三轴图。"""
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False

    T = len(charge_schedule)
    x = time_index if time_index is not None else range(T)

    fig, ax1 = plt.subplots(figsize=(16, 5))

    ax1.step(x, charge_schedule, where="mid", label="充电功率 (kW)", color="C0", alpha=0.8)
    ax1.step(x, [-d for d in discharge_schedule], where="mid", label="放电功率 (kW)", color="C1", alpha=0.8)
    ax1.set_ylabel("功率 (kW)")
    ax1.set_xlabel("时间")
    ax1.legend(loc="upper left")
    ax1.axhline(0, color="black", linewidth=0.5)

    ax2 = ax1.twinx()
    ax2.plot(x, soc_schedule, color="C3", linewidth=1.2, label="SOC (kWh)")
    ax2.set_ylabel("SOC (kWh)")
    ax2.legend(loc="upper right")

    plt.title(title)
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()


# ============================================================
# 电价-负荷匹配可视化
# ============================================================

def plot_price_load_scatter(
    df,
    price_col="Price",
    load_col="Load",
    spare_col=None,
    title="电价 vs 负荷散点图",
):
    """电价-负荷散点图，可选颜色映射变压器剩余容量。"""
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False

    plt.figure(figsize=(8, 6))
    if spare_col and spare_col in df.columns:
        sc = plt.scatter(
            df[price_col], df[load_col],
            c=df[spare_col], cmap="viridis", s=10, alpha=0.6,
        )
        cbar = plt.colorbar(sc)
        cbar.set_label(spare_col)
    else:
        plt.scatter(df[price_col], df[load_col], s=10, alpha=0.5)

    plt.xlabel("电价 (元/kWh)")
    plt.ylabel("负荷 (kW)")
    plt.title(title)
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()


def plot_matching_heatmaps(
    df,
    time_col="Time",
    price_col="Price",
    load_col="Load",
    spare_col="变压器剩余容量_kW",
):
    """小时维度热力图：电价、负荷、变压器剩余容量。"""
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False

    df = df.copy()
    df["hour"] = pd.to_datetime(df[time_col]).dt.hour

    fig, axes = plt.subplots(1, 3, figsize=(15, 4))

    pivot_p = df.pivot_table(index="hour", values=price_col, aggfunc="mean")
    pivot_l = df.pivot_table(index="hour", values=load_col, aggfunc="mean")

    import seaborn as sns
    sns.heatmap(pivot_p, cmap="coolwarm", ax=axes[0])
    axes[0].set_title("小时平均电价")

    sns.heatmap(pivot_l, cmap="YlGnBu", ax=axes[1])
    axes[1].set_title("小时平均负荷")

    if spare_col in df.columns:
        pivot_s = df.pivot_table(index="hour", values=spare_col, aggfunc="mean")
        sns.heatmap(pivot_s, cmap="Greens", ax=axes[2])
        axes[2].set_title("小时平均变压器剩余容量")

    plt.tight_layout()
    plt.show()


# ============================================================
# IRR 曲线可视化
# ============================================================

def plot_irr_vs_capacity(
    scan_df,
    capacity_col="capacity_mwh",
    irr_col="irr_percent",
    group_col=None,
    title="储能容量 vs IRR",
):
    """IRR 随储能容量变化曲线。支持按 group_col 分组绘制。"""
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False

    plt.figure(figsize=(10, 6))

    if group_col and group_col in scan_df.columns:
        for key, sub in scan_df.groupby(group_col):
            sub = sub.sort_values(capacity_col)
            plt.plot(sub[capacity_col], sub[irr_col], marker="o", label=f"{group_col}={key}")
    else:
        df_sorted = scan_df.sort_values(capacity_col)
        plt.plot(df_sorted[capacity_col], df_sorted[irr_col], marker="o")

    plt.axhline(0, color="gray", linestyle="--")
    plt.xlabel("储能容量 (MWh)")
    plt.ylabel("IRR (%)")
    plt.title(title)
    plt.grid(alpha=0.3)
    if group_col:
        plt.legend()
    plt.tight_layout()
    plt.show()


def plot_pv_bess_irr_curves(
    result,
    title="光储 IRR 曲线（不同购电价）",
):
    """光储 IRR 扫描结果：x=储能容量, y=IRR, 曲线=不同购电价。"""
    if result.scan_df is None:
        return

    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False

    plt.figure(figsize=(10, 6))
    for bp, sub in result.scan_df.groupby("buy_price_per_kwh"):
        sub = sub.sort_values("bess_mwh")
        plt.plot(sub["bess_mwh"], sub["irr_percent"], marker="o", label=f"购电价 {bp:.2f}")

    plt.axhline(0, color="gray", linestyle="--")
    plt.xlabel("储能规模 (MWh)")
    plt.ylabel("IRR (%)")
    plt.title(title)
    plt.grid(alpha=0.3)
    plt.legend(loc="upper right")
    plt.gca().yaxis.set_major_formatter(lambda x, pos: f"{x:.1f}%")
    plt.tight_layout()
    plt.show()


def plot_delta_irr_curves(
    result,
    title="储能容量变化 50MWh 时的 IRR 变化规律",
):
    """ΔIRR 曲线：每增加 50MWh 的 IRR 变化率。"""
    if result.delta_df is None:
        return

    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False

    plt.figure(figsize=(10, 6))
    for bp, sub in result.delta_df.groupby("buy_price_per_kwh"):
        plt.plot(sub["bess_from_mwh"], sub["delta_irr_percent"], marker="o", label=f"购电价 {bp:.2f}")

    plt.axhline(0, color="gray", linestyle="--")
    plt.xlabel("储能规模 (MWh)")
    plt.ylabel("ΔIRR (%)")
    plt.title(title)
    plt.grid(alpha=0.3)
    plt.legend(loc="upper right")
    plt.tight_layout()
    plt.show()


# ============================================================
# 多节点对比可视化
# ============================================================

def plot_multi_node_comparison(
    summary_df,
    node_col="节点",
    capacity_col="capacity_mwh",
    irr_col="irr_percent",
    title="各节点最优容量 & IRR 对比",
):
    """柱状图（容量）+ 折线图（IRR）双轴对比。"""
    import matplotlib as mpl
    font_name = ['Arial Unicode MS', 'SimHei']
    mpl.rcParams['font.sans-serif'] = font_name[0]
    mpl.rcParams['axes.unicode_minus'] = False

    df = summary_df.sort_values(irr_col, ascending=False).reset_index(drop=True)
    nodes = df[node_col].tolist()
    x = np.arange(len(nodes))

    fig, ax1 = plt.subplots(figsize=(10, 6))

    ax1.bar(x, df[capacity_col], width=0.4, label="推荐容量 (MWh)")
    ax1.set_xlabel("节点")
    ax1.set_ylabel("推荐容量 (MWh)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(nodes, rotation=45, ha="right")

    ax2 = ax1.twinx()
    ax2.plot(x, df[irr_col], marker="o", linestyle="-", label="IRR (%)", color="tab:red")
    ax2.set_ylabel("IRR (%)")

    handles1, labels1 = ax1.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(handles1 + handles2, labels1 + labels2, loc="upper left")

    plt.title(title)
    plt.tight_layout()
    plt.show()


# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
