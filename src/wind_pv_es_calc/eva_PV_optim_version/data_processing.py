# -*- coding: utf-8 -*-

# ***************************************************
# * File        : data_processing.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-30
# * Version     : 1.0.043014
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
from pathlib import Path

import pandas as pd


def data_processor(load_transfer_coef=685436401/704234268, 
                   farm_capacity_mw=110.0,
                   mean_wind_speed_140m=5.5,
                   eq_full_load_hours=1920.7,
                   lat=28.42,
                   lon=117.88,
                   capacity_kwp=28250,
                   data_combine=True):
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from src.wind_pv_es_calc.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("data/wind_pv_es_calc/temp/df_2025.csv")
    df_load = load_data(energy_data_path=energy_data_path)
    if load_transfer_coef is not None:
        df_load["P_kw"] = df_load["P_kw"] * load_transfer_coef
    print(df_load)
    # ------------------------------
    # wind power data
    # ------------------------------
    from src.wind_pv_es_calc.eva_PV_optim_version.data_wind_simu import generate_wind_data
    wind_data_path = Path("data/wind_pv_es_calc/temp/df_wind_2025.csv")
    df_wind = generate_wind_data(
        farm_capacity_mw=farm_capacity_mw, 
        mean_wind_speed_140m=mean_wind_speed_140m, 
        eq_full_load_hours=eq_full_load_hours, 
        lat=lat, 
        lon=lon, 
        wind_data_path=wind_data_path
    )
    print(df_wind)
    # ------------------------------
    # PV(Photo Voltaics) power data
    # ------------------------------
    from src.wind_pv_es_calc.eva_PV_optim_version.data_pv_simu import generate_pv_data
    pv_data_path = Path("data/wind_pv_es_calc/temp/df_pv_2025.csv")
    df_pv = generate_pv_data(
        df=df_load, 
        lat=lat, 
        lon=lon, 
        capacity_kwp=capacity_kwp, 
        pv_data_path=pv_data_path, 
        plot_img=False
    )
    print(df_pv)
    # ------------------------------
    # data combine
    # ------------------------------
    if data_combine:
        # 1. 确保时间列为 datetime
        df_load["Time"] = pd.to_datetime(df_load["Time"])
        df_wind["Time"] = pd.to_datetime(df_wind["Time"])
        # 2. 全部设为 Time 索引
        df_load = df_load.set_index("Time")[["P_kw"]]
        df_wind = df_wind.set_index("Time")[["WindPower_MW"]]
        df_pv = df_pv.to_frame(name="PV_kw")        # 若 df_pv 是 Series
        # 3. concat 合并（按时间对齐）
        df_total = pd.concat([df_load, df_pv, df_wind], axis=1)
        df_total = df_total.reset_index()
        # 4. 如需要 Time 列
        df = df_total.copy()
        df["Wind_kw"] = df["WindPower_MW"] * 1000
        df["NetLoad_kw"] = df["P_kw"] - df["PV_kw"] - df["Wind_kw"]
        df.to_csv("data/wind_pv_es_calc/temp/df_total.csv")
        print(df)

        return df_load, df_pv, df_wind, df
    else:
        return df_load, df_pv, df_wind




# 测试代码 main 函数
def main():
    from utils.plot_ts import plot_load_pv_wind_netload
    df_load, df_pv, df_wind, df = data_processor(data_combine=True)

    # 2025-10 数据
    start, end = pd.Timestamp("2025-10-01"), pd.Timestamp("2025-10-31")
    df_10 = df[(df["Time"] > start) & (df["Time"] < end)]
    
    plot_load_pv_wind_netload(df=df_10) 
    
if __name__ == "__main__":
    main()
