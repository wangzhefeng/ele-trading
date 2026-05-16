# -*- coding: utf-8 -*-

# python libraries
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
plt.style.use('seaborn-v0_8-whitegrid')
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


def plot_data(df, 
              ycols: List, 
              current_time: datetime, 
              response_before_1h: datetime,
              baseline_coef_period: Dict, 
              climbing_period: Dict, 
              response_period: Dict, 
              strategy_period: Dict, 
              peak1_period: Dict,
              peak2_period: Dict, 
              charge_period: Dict,
              response_mode: str,
              route: str,
              title: str, 
              xlabel: str="时间", 
              ylabel: str="功率(kW)"):
    # ------------------------------
    # 绘图设置
    # ------------------------------
    # 用来正常显示中文标签
    font_name = ["Arial Unicode MS", "SimHei"]
    mpl.rcParams["font.sans-serif"] = font_name[0]
    # 用来显示负号
    mpl.rcParams["axes.unicode_minus"] = False
    # 标题名
    title_name = f"{title}-[响应时间: {response_period['start'].time()}-{response_period['end'].time()}]"
    # 文件保存路径
    response_time_len = (
        response_period["end"] + timedelta(minutes=5) - response_period["start"]
    ).total_seconds() / 3600
    notice_time_len = np.round((response_period["start"] - current_time).total_seconds() / 3600, 2)
    response_date = response_period["start"].date()
    img_dir = Path(
        f"./model/model_packages/Demand_Response_optim/result/{response_date}/{response_mode}/{route}/response-{response_time_len}/notice-{notice_time_len}"
    )
    img_dir.mkdir(parents=True, exist_ok=True)
    file_name = (
        f"{route}-"
        f"{response_mode}-"
        f"{response_period['start'].hour:02d}{response_period['start'].minute:02d}-"
        f"{response_period['end'].hour:02d}{response_period['end'].minute:02d}"
    )
    img_path = img_dir.joinpath(f"{title}-[{file_name}].png")
    # ------------------------------
    # 画布
    # ------------------------------
    # fig, ax = plt.subplots(figsize=(25, 8))
    plt.figure(figsize=(25, 8))
    # ------------------------------
    # 绘图
    # ------------------------------
    ycols_map = {
        "demand_load": "用电负荷预测值",
        "strategy_load": "无需求响应策略负荷",
        "strategy_load_new": "需求响应调整后策略负荷",
        "baseline": "基线负荷", 
        "aidc_load": "无需求响应关口表负荷(预测值)", 
        "aidc_load_new": "需求响应调整后关口表负荷(预测值)",
        "soc": "储能SOC",
        "ele_price": "电价",
    }
    for ycol in ycols:
        if ycol == "baseline":
            plt.plot(df["time"], df[ycol], "o", label=ycols_map[ycol])
        elif ycol == "aidc_load":
            plt.plot(df["time"], df[ycol], linewidth=3.0, label=ycols_map[ycol])
        else:
            plt.plot(df["time"], df[ycol], linewidth=2.0, label=ycols_map[ycol])
    if current_time >= min(df["time"].values) and current_time <= max(df["time"].values):
        plt.axvline(x=current_time, color="red", linestyle="--")
        plt.axvline(x=response_before_1h, color="green", linestyle="--")
    plt.axvspan(baseline_coef_period["start"], baseline_coef_period["end"]+timedelta(minutes=5), facecolor = "blue", alpha = 0.1, zorder=0)
    plt.axvspan(climbing_period["start"], climbing_period["end"]+timedelta(minutes=5), facecolor = "yellow", alpha = 0.1, zorder=0)
    plt.axvspan(response_period["start"], response_period["end"]+timedelta(minutes=5), facecolor = "red", alpha = 0.1, zorder=0)
    plt.axvspan(strategy_period["start"], strategy_period["end"]+timedelta(minutes=5), facecolor = "darkgray", alpha = 0.2, zorder=0)
    # 充放电
    plt.axvspan(peak1_period["start"], peak1_period["end"]+timedelta(minutes=5), facecolor = "darkblue", alpha = 0.1, zorder=0)
    plt.axvspan(peak2_period["start"], peak2_period["end"]+timedelta(minutes=5), facecolor = "darkblue", alpha = 0.1, zorder=0)
    plt.axvspan(charge_period["start"], charge_period["end"]+timedelta(minutes=5), facecolor = "green", alpha = 0.1, zorder=0)
    # ------------------------------
    # 设置坐标轴
    # ------------------------------
    # 图例
    plt.legend()
    # 标题、坐标轴
    plt.title(title_name)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    # 调整布局，防止标签被截断
    plt.tight_layout()
    # ------------------------------
    # 设置图像样式
    # ------------------------------
    plt.savefig(img_path, dpi=300)
    plt.close()

def check_input_data(df_history_future, 
                     df_strategy_period, 
                     period_map: Dict, 
                     response_mode: str, 
                     route: str="lingang_A"):
    """
    数据检查
    """
    current_time=period_map["current_time"]
    response_before_1h=period_map["response_before_1h"]["start"]
    baseline_coef_period=period_map["baseline_coef"]
    climbing_period=period_map["climbing"]
    response_period=period_map["response"]
    strategy_period=period_map["strategy"]
    peak1_period=period_map["peak1_discharge"]
    peak2_period=period_map["peak2_discharge"]
    charge_period=period_map["charge"]
    # all
    plot_data(
        df=df_history_future, 
        ycols=[
            "demand_load", 
            "strategy_load", 
            "aidc_load"
        ], current_time=current_time,
        response_before_1h=response_before_1h,
        baseline_coef_period=baseline_coef_period, climbing_period=climbing_period, 
        response_period=response_period, strategy_period=strategy_period, 
        peak1_period=peak1_period, peak2_period=peak2_period, charge_period=charge_period,
        response_mode=response_mode,
        route=route,
        title="关口表-负荷-策略[all]", xlabel="时间", ylabel="功率(kW)",
    )
    plot_data(
        df=df_history_future, 
        ycols=["soc"], current_time=current_time,
        response_before_1h=response_before_1h,
        baseline_coef_period=baseline_coef_period, climbing_period=climbing_period, 
        response_period=response_period, strategy_period=strategy_period, 
        peak1_period=peak1_period, peak2_period=peak2_period, charge_period=charge_period,
        response_mode=response_mode,
        route=route,
        title="SOC[all]", xlabel="时间", ylabel="kWh or %",
    )
    plot_data(
        df=df_history_future, 
        ycols=["ele_price"], current_time=current_time,
        response_before_1h=response_before_1h,
        baseline_coef_period=baseline_coef_period, climbing_period=climbing_period, 
        response_period=response_period, strategy_period=strategy_period, 
        peak1_period=peak1_period, peak2_period=peak2_period, charge_period=charge_period,
        response_mode=response_mode,
        route=route,
        title="电价[all]", xlabel="时间", ylabel="元/kWh",
    )
    # strategy_period
    plot_data(
        df=df_strategy_period, 
        ycols=["demand_load", "strategy_load", "aidc_load"], current_time=current_time, 
        response_before_1h=response_before_1h,
        baseline_coef_period=baseline_coef_period, climbing_period=climbing_period, 
        response_period=response_period, strategy_period=strategy_period, 
        peak1_period=peak1_period, peak2_period=peak2_period, charge_period=charge_period,
        response_mode=response_mode,
        route=route,
        title="关口表-负荷-策略[strategy]", xlabel="时间", ylabel="功率(kW)",
    )
    plot_data(
        df=df_strategy_period, 
        ycols=["soc"], current_time=current_time, 
        response_before_1h=response_before_1h,
        baseline_coef_period=baseline_coef_period, climbing_period=climbing_period, 
        response_period=response_period, strategy_period=strategy_period, 
        peak1_period=peak1_period, peak2_period=peak2_period, charge_period=charge_period,
        response_mode=response_mode,
        route=route,
        title="SOC[strategy]", xlabel="时间", ylabel="kWh or %",
    )
    plot_data(
        df=df_strategy_period, 
        ycols=["ele_price"], current_time=current_time, 
        response_before_1h=response_before_1h,
        baseline_coef_period=baseline_coef_period, climbing_period=climbing_period, 
        response_period=response_period, strategy_period=strategy_period, 
        peak1_period=peak1_period, peak2_period=peak2_period, charge_period=charge_period,
        response_mode=response_mode,
        route=route,
        title="电价[strategy]", xlabel="时间", ylabel="元/kWh",
    )

def plot_results(df_strategy_period: pd.DataFrame,
                 df_strategy_period_new: pd.DataFrame,
                 df_baseline: pd.DataFrame = None,
                 period_map: Dict = {},
                 response_mode: str="日前",
                 route: str="lingang_A"):
    """
    结果可视化
    """
    # ------------------------------
    # data preprocessing
    # ------------------------------
    df_strategy_period["strategy_load_new"] = df_strategy_period["time"].map(
        df_strategy_period_new.set_index("time")["strategy_load"]
    )
    df_strategy_period["aidc_load_new"] = df_strategy_period.apply(
        lambda x: x["demand_load"] - x["strategy_load_new"], axis=1
    )
    df_strategy_period["baseline"] = df_strategy_period["time"].map(
        df_baseline.set_index("time")["value"]
    )
    # ------------------------------
    # plot data
    # ------------------------------ 
    plot_data(
        df=df_strategy_period, 
        ycols=[
            "demand_load", 
            "strategy_load", 
            "strategy_load_new", 
            "baseline", 
            # "aidc_load", 
            # "aidc_load_new",
        ], 
        current_time=period_map["current_time"],
        response_before_1h=period_map["response_before_1h"]["start"],
        baseline_coef_period=period_map["baseline_coef"], 
        climbing_period=period_map["climbing"], 
        response_period=period_map["response"], 
        strategy_period=period_map["strategy"], 
        peak1_period=period_map["peak1_discharge"],
        peak2_period=period_map["peak2_discharge"],
        charge_period=period_map["charge"],
        response_mode=response_mode,
        route=route,
        title=f"关口表-负荷-策略", 
        xlabel="时间", 
        ylabel="功率(kW)",
    )
