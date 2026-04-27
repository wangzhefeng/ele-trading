# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim21.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042018
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import os
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import matplotlib.pyplot as plt

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
# from utils.log_util import logger



# 测试代码 main 函数
def main():
    # ##############################
    # TODO 
    # ##############################
    from ba_eva.optim_version.data_loader import load_data
    from ba_eva.optim_version.wind_simu import generate_wind_data
    from ba_eva.optim_version.pv_simu import generate_pv_data
    # ------------------------------
    # 负荷数据
    # ------------------------------
    # df_2025 = pd.read_csv("D:\\228-售前测算\\乌兰察布\\df_2025.csv", encoding="utf_8_sig")
    df_2025 = load_data()
    df_2025["P_kw"] = df_2025["P_kw"] / 704234268 * 685436401
    # ------------------------------
    # wind power data
    # ------------------------------
    df_wind = generate_wind_data(farm_capacity_mw=110.0, mean_wind_speed_140m=5.5, eq_full_load_hours=1920.7, lat=28.42, lon=117.88)
    # ------------------------------
    # PV(Photo Voltaics) power data
    # ------------------------------
    pv_kw_28 = generate_pv_data(df=df_2025, lat=28.42, lon=117.88, capacity_kwp=28250)
    # ------------------------------
    # run
    # ------------------------------
    # 1. 确保时间列为 datetime
    df_2025["Time"] = pd.to_datetime(df_2025["Time"])
    df_wind["Time"] = pd.to_datetime(df_wind["Time"])

    # 2. 全部设为 Time 索引
    df_load = df_2025.set_index("Time")[["P_kw"]]
    df_wind = df_wind.set_index("Time")[["WindPower_MW"]]
    df_pv = pv_kw_28.to_frame(name="PV_kw")        # 若 pv_kw_28 是 Series

    # 3. concat 合并（按时间对齐）
    df_total = pd.concat([df_load, df_pv, df_wind], axis=1)

    # 4. 如需要 Time 列
    df_total = df_total.reset_index()
    df = df_total.copy()
    df["Wind_kw"] = df["WindPower_MW"] * 1000
    df["NetLoad_kw"] = df["P_kw"] - df["PV_kw"] - df["Wind_kw"]
    # TODO 补充路径
    # df.to_csv("df_total.csv")
    start = pd.Timestamp("2025-10-01")
    end = pd.Timestamp("2025-10-31")
    # ------------------------------
    # df_10
    # ------------------------------
    df_10 = df[(df["Time"] > start) & (df["Time"] < end)]

    plt.figure(figsize=(14, 6))
    plt.plot(df_10["Time"], df_10["P_kw"], label="Load (kW)", linewidth=1.8)
    # plt.plot(df_10["Time"], df_10["PV_kw"], label="PV (kW)", linewidth=1.5)
    plt.plot(df_10["Time"], df_10["Wind_kw"], label="Wind (kW)", linewidth=1.5)
    plt.xlabel("Time")
    plt.ylabel("Power (kW)")
    plt.title("Load / PV / Wind Power Time Series")
    plt.legend(loc="upper right")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()
    # ------------------------------
    # df_NetLoad_kw
    # ------------------------------
    df_NetLoad_kw = df[(df["Time"] > start) & (df["Time"] < end)]
    
    plt.figure(figsize=(14, 5))
    plt.plot(df["Time"], df_NetLoad_kw["NetLoad_kw"], color="black", linewidth=1.6)
    plt.axhline(0, linestyle="--", color="red", alpha=0.7)
    plt.xlabel("Time")
    plt.ylabel("Net Load (kW)")
    plt.title("Net Load = Load - PV - Wind")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    main()
