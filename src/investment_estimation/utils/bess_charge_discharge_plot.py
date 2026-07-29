# -*- coding: utf-8 -*-

# ***************************************************
# * File        : bess_charge_discharge_plot.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2025-12-01
# * Version     : 1.0.120116
# * Description : 储能充放电收益测算报告绘图(按夏/冬/其他月份分组绘制典型日充放电功率与电价时段背景)
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
from typing import List, Dict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib as mpl
# mpl.use('TkAgg')  # 强制使用 TkAgg 后端（最稳定）
import matplotlib.pyplot as plt
# plt.style.use('seaborn-v0_8-whitegrid')
# plt.rcParams['font.sans-serif']=['SimHei'] # 用来正常显示中文标签
# plt.rcParams['axes.unicode_minus'] = False # 用来显示负号
plt.rcParams['figure.autolayout'] = True # layout
plt.rcParams['axes.grid'] = True # grid
plt.rc(
    "figure",
    autolayout=True,
    figsize=(25, 8),
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
# train_scatter_plot_params = dict(
#     color="0.75",
#     style=".-",
#     linewidth=2,
#     markeredgecolor="0.25",
#     markerfacecolor="0.25",
#     legend=True,
#     label="Train trues",
# )
# test_scatter_plot_params = dict(
#     color="C2",
#     style=".-",
#     linewidth=2,
#     markeredgecolor="0.25",
#     markerfacecolor="0.25",
#     legend=True,
#     label="Test trues",
# )
# line1_plot_params = dict(
#     color="C0",
#     style=".-",
#     linewidth=2,
#     legend=True,
#     label="Train preds",
# )
# line2_plot_params = dict(
#     color="C1",
#     style=".-",
#     linewidth=2,
#     legend=True,
#     label="Test preds",
# )
# line3_plot_params = dict(
#     color="C3",
#     style=".-",
#     linewidth=2,
#     legend=True,
#     label="Forecast",
# )

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]


def read_price(data_dir: str, data_name: str, year: int, month: int):
    df = pd.read_csv(data_dir.joinpath(f"{data_name}.csv"), encoding="utf-8")
    df = df.loc[(df["time"] >= f"{year}-{month:02d}-10 00:00:00") & (df["time"] < f"{year}-{month:02d}-11 00:00:00"), :] 
    df["time"] = pd.to_datetime(df["time"])
    df["hour"] = df["time"].apply(lambda x: x.strftime("%H:%M"))
    df = df.loc[df["hour"].apply(lambda x: x[-2:] == "00"), ["hour", "type"]]
    
    return df

def process_strategy_data(data_dir, data_source, data_target):
    source_path = data_dir.joinpath(f"{data_source}.csv")
    target_path = data_dir.joinpath(f"{data_target}.csv")

    df = pd.read_csv(source_path, encoding="utf-8")
    df["time"] = pd.to_datetime(df["time"])
    df["year"] = df["time"].dt.year
    df["month"] = df["time"].dt.month
    df["hour"] = df["time"].dt.hour
    df = (
        df.groupby(["year", "month", "hour"], as_index=False)["value"]
        .mean()
        .loc[:, ["year", "month", "hour", "value"]]
    )
    df.to_csv(target_path, encoding="utf-8", index=True)

def read_load(data_dir: str, data_target_name: str, month_list: List):
    df = pd.read_csv(data_dir.joinpath(f"{data_target_name}.csv"), encoding="utf-8")
    df = df.loc[df["month"].apply(lambda x: x in month_list), :]
    df = df.sort_values(["month", "year", "hour"]).reset_index(drop=True)
    df["year_month"] = df.apply(lambda x: f"{int(x['year'])}-{int(x['month'])}", axis=1)
    df["hour"] = df["hour"].apply(lambda x: f"{x:02d}:{'00'}")
    column_order = (
        df[["year", "month", "year_month"]]
        .drop_duplicates()
        .sort_values(["month", "year"])["year_month"]
        .tolist()
    )
    df = df[["year_month", "hour", "value"]]
    
    df_pivot = df.pivot_table(
        index="hour",
        columns="year_month",
        values="value",
        aggfunc="first",
    )
    df_pivot = df_pivot.reindex(columns=column_order)
    # 确保24小时顺序正确
    all_hours = [f"{i:02d}:00" for i in range(24)]
    df_pivot = df_pivot.reindex(all_hours)
    
    df_plot = df_pivot.reset_index()
    
    return df_plot

def get_month_colors(month_list: List[int]):
    month_palette = {
        2: "#5B8FF9",
        3: "#61DDAA",
        4: "#7BC96F",
        5: "#FF6B6B",
        6: "#FF9F1C",
        7: "#2C7FB8",
        8: "#F6AA1C",
        9: "#D95D39",
        10: "#8E6CBB",
        11: "#A06C3F",
        12: "#4C78A8",
        1: "#72B7B2",
    }

    return [month_palette.get(month, "#4C78A8") for month in month_list]

def plot_data(df: pd.DataFrame, title: str="", img_dir: str=None, year_list: List=[], month_list: List=[], text_position=400):
    # ------------------------------
    # 1. setting
    # ------------------------------
    font_name = ["Arial Unicode MS", "SimHei"]
    mpl.rcParams["font.sans-serif"] = font_name[0]  # 用来正常显示中文标签
    mpl.rcParams["axes.unicode_minus"] = False  # 用来显示负号
    # ------------------------------
    # 2. 准备绘图数据
    # ------------------------------
    hours = df['hour'].tolist()
    price_types = df['type'].tolist()
    x = np.arange(len(hours))
    # ------------------------------
    # 3. 画布
    # ------------------------------
    fig, ax = plt.subplots(figsize=(14, 8))
    value_columns = [col for col in df.columns if col not in ["hour", "type"]]
    value_array = df[value_columns].to_numpy(dtype=float)
    data_max = max(0, np.nanmax(value_array))
    data_min = min(0, np.nanmin(value_array))
    data_range = data_max - data_min
    if data_range == 0:
        data_range = max(abs(data_max), 1.0)
    top_padding = data_range * 0.22
    bottom_padding = data_range * 0.06
    auto_text_position = data_max + data_range * 0.12
    text_y = max(text_position if text_position is not None else auto_text_position, auto_text_position)
    ax.set_ylim(data_min - bottom_padding, data_max + top_padding)
    # ------------------------------
    # 4. 绘制柱状图
    # ------------------------------
    df_columns = value_columns
    color_names = get_month_colors(month_list)
    # 2个月
    if len(df_columns) == 2:
        bar_width = 0.35
        ax.bar(
            x,
            df[f"{year_list[0]}-{month_list[0]}"].to_list(), 
            bar_width, 
            label=f'{month_list[0]}月', 
            color=color_names[0], alpha=0.85)
        ax.bar(
            x + bar_width, 
            df[f"{year_list[1]}-{month_list[1]}"].to_list(), 
            bar_width, 
            label=f'{month_list[1]}月', 
            color=color_names[1], alpha=0.85) 
    # 多个月份
    else:
        bar_width = 0.10
        for j, (col, color) in enumerate(zip(df_columns, color_names)):
            ax.bar(
                x + j * bar_width, 
                df[col].to_list(), 
                bar_width, 
                label=f'{col.split("-")[-1]}月', 
                color=color, alpha=0.8
            )
    # ------------------------------
    # 5. 添加背景区域 (根据电价时段)
    # ------------------------------
    # 定义背景颜色和透明度
    type_styles = {
        "低": {"facecolor": "#D9F3F7", "alpha": 0.95},
        "谷": {"facecolor": "#D9F3F7", "alpha": 0.95},
        "平": {"facecolor": "#FFF7BF", "alpha": 0.75},
        "高": {"facecolor": "#FFD9D4", "alpha": 0.75},
        "峰": {"facecolor": "#FFD9D4", "alpha": 0.75},
        "尖": {"facecolor": "#F7D6FF", "alpha": 0.9},
    }
    # 动态绘制背景区域和标签
    current_type = price_types[0]
    start_idx = 0
    for i in range(1, len(price_types)):
        if price_types[i] != current_type:
            end_idx = i
            # 绘制背景
            style = type_styles[current_type]
            ax.axvspan(start_idx - 0.5, end_idx - 0.5, facecolor=style["facecolor"], alpha=style["alpha"], zorder=0)
            # 添加文字标签
            mid_point = (start_idx - 0.5 + end_idx - 0.5) / 2
            ax.text(mid_point, text_y, current_type, ha='center', va='center', fontsize=18, fontweight='bold')
            # 更新状态
            current_type = price_types[i]
            start_idx = i
    # 绘制最后一个区域
    style = type_styles[current_type]
    ax.axvspan(start_idx - 0.5, len(price_types) - 0.2, facecolor=style["facecolor"], alpha=style["alpha"], zorder=0)
    mid_point = (start_idx - 0.5 + len(price_types) - 0.5) / 2
    ax.text(mid_point, text_y, current_type, ha='center', va='center', fontsize=18, fontweight='bold')
    # ------------------------------
    # 6. 设置坐标轴
    # ------------------------------
    ax.set_xlabel('时间')
    ax.set_ylabel('功率（kW）')
    ax.set_title(title)
    ax.set_xticks(x + bar_width * (len(df_columns) - 1) / 2)
    ax.set_xticklabels(hours, rotation=45, ha='right')
    ax.grid(True, linestyle='--', alpha=0.5)
    ax.legend(loc='upper left', bbox_to_anchor=(1.01, 1.0), borderaxespad=0.0, frameon=True)
    # ------------------------------
    # 7. 设置图像样式
    # ------------------------------
    # 调整布局，防止标签被截断
    fig.subplots_adjust(right=0.86)
    plt.tight_layout()
    # 图像保存
    plt.savefig(img_dir.joinpath(f"{title}.png"), dpi=300)
    # 显示图形
    # plt.show()




# 测试代码 main 函数
def main():
    # ------------------------------
    # params
    # ------------------------------
    # proj1_month_map = {
    #     "summer": {
    #         "title": "夏季（8 月、9 月）",
    #         "month_list": [8, 9],
    #         "year_lsit": [2024, 2024],
    #     },
    #     "winter": {
    #         "title": "冬季（1 月、12 月）",
    #         "month_list": [12, 1],
    #         "year_lsit": [2024, 2025],
    #     },
    #     "other": {
    #         "title": "其他月份（2～7 月，10 月，11 月）",
    #         "month_list": [2, 3, 4, 5, 6, 7, 10, 11],
    #         "year_lsit": [2025, 2025, 2025, 2025, 2025, 2024, 2024, 2024],
    #     },
    # }
    month_map = {
        "summer": {
            "title": "夏季（7 月、8 月、9 月）",
            "month_list": [7, 8, 9],
            "year_lsit": [2025, 2025, 2025],
        },
        "winter": {
            "title": "冬季（1 月、12 月）",
            "month_list": [12, 1],
            "year_lsit": [2025, 2026],
        },
        "other": {
            "title": "其他月份（2～6 月，10 月，11 月）",
            "month_list": [2, 3, 4, 5, 6, 10, 11],
            "year_lsit": [2026, 2026, 2025, 2025, 2025, 2025, 2025],
        },
    }
    # ------------------------------
    # input data
    # ------------------------------
    # 仓库根:data/results 相对仓库根定位,与运行 CWD 无关
    repo_root = Path(__file__).resolve().parents[3]
    data_dir = repo_root / "data" / "bess_charge_discharge" / "wuhu"
    price_data_name = "ele_price"
    strategy_source_data_name = "schedule_result_scale_40000"
    strategy_target_data_name = "schedule_result_scale_40000_processed"

    # 处理策略数据
    if not data_dir.joinpath(f"{strategy_target_data_name}.csv").exists():
        process_strategy_data(data_dir, strategy_source_data_name, strategy_target_data_name)
    
    for month_mode in ["summer", "winter", "other"]:
        # 读取电价数据
        df_price = read_price(
            data_dir, 
            data_name=price_data_name, 
            year=month_map[month_mode]["year_lsit"][0], 
            month=month_map[month_mode]["month_list"][0],
        )
        print(df_price)
        
        # 读取处理后的策略数据
        df_load = read_load(
            data_dir, 
            data_target_name = strategy_target_data_name, 
            month_list=month_map[month_mode]["month_list"],
        )
        print(df_load)
        
        # 合并数据
        df = df_load.merge(df_price, how="left", on="hour")
        print(df)
        # ------------------------------
        # output data
        # ------------------------------
        imgs_dir = repo_root / "results" / "bess_charge_discharge" / "wuhu"
        imgs_dir.mkdir(parents=True, exist_ok=True)
        plot_data(
            df, 
            title=month_map[month_mode]["title"],
            img_dir=imgs_dir, 
            year_list=month_map[month_mode]["year_lsit"],
            month_list=month_map[month_mode]["month_list"],
            text_position=600,
        )

if __name__ == "__main__":
    main()
