# -*- coding: utf-8 -*-

# ***************************************************
# * File        : pv_simu.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042014
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
from pathlib import Path

import pandas as pd
import pvlib
from pvlib.location import Location

from utils.plot_ts import plot_daily_pv_shape


def simulate_pv_output(
    time_index,
    lat: float,
    lon: float,
    capacity_kwp: float = 1.0,
    tilt: float | None = None,
    azimuth: float = 180.0,
    system_loss: float = 0.20,     # ← 提高系统损失
    temp_coeff: float = -0.004,
    cloud_factor: float = 0.75,    # ← 新增云量折减
) -> pd.Series:
    """
    光伏出力模拟核心建模入口。它基于时间索引、经纬度、装机容量、倾角、方位角、系统损耗、温度系数和云量折减等参数，
    通过 `pvlib` 计算光伏 AC 输出功率序列。输出是以时间为索引的 `pv_kw` 序列

    Args:
        time_index (_type_): _description_
        lat (float): _description_
        lon (float): _description_
        capacity_kwp (float, optional): _description_. Defaults to 1.0.
        tilt (float | None, optional): _description_. Defaults to None.
        azimuth (float, optional): _description_. Defaults to 180.0.
        system_loss (float, optional): _description_. Defaults to 0.20.
        cloud_factor (float, optional): _description_. Defaults to 0.75.

    Returns:
        pd.Series: _description_
    """
    # 时间标准化
    time_index = pd.to_datetime(time_index)
    if not isinstance(time_index, pd.DatetimeIndex):
        time_index = pd.DatetimeIndex(time_index)
    if time_index.tz is None:
        time_index = time_index.tz_localize("Asia/Shanghai")

    if tilt is None:
        tilt = abs(lat)

    location = Location(lat, lon, tz="Asia/Shanghai")

    solpos = location.get_solarposition(time_index)
    clearsky = location.get_clearsky(time_index, model="ineichen")

    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt,
        surface_azimuth=azimuth,
        solar_zenith=solpos["zenith"],
        solar_azimuth=solpos["azimuth"],
        dni=clearsky["dni"],
        ghi=clearsky["ghi"],
        dhi=clearsky["dhi"],
    )

    poa_global = poa["poa_global"].clip(lower=0) * cloud_factor

    temp_cell = pvlib.temperature.pvsyst_cell(
        poa_global, temp_air=30, wind_speed=1
    )

    dc = pvlib.pvsystem.pvwatts_dc(
        poa_global, temp_cell, pdc0=1000, gamma_pdc=temp_coeff
    )

    ac = pvlib.inverter.pvwatts(dc, pdc0=1000)
    ac = ac * (1 - system_loss)

    pv_kw = (ac / 1000) * capacity_kwp
    
    return pv_kw.clip(lower=0, upper=capacity_kwp).rename("pv_kw").tz_localize(None)


def validate_equivalent_hours(pv_kw: pd.Series, capacity_kwp: float):
    """
    用于校验光伏序列的年等效利用小时数，判断模拟结果是否落在合理范围

    Args:
        pv_kw (pd.Series): _description_
        capacity_kwp (float): _description_

    Returns:
        _type_: _description_
    """
    dt_h = (pv_kw.index[1] - pv_kw.index[0]).total_seconds() / 3600
    annual_kwh = (pv_kw * dt_h).sum()
    eq_hours = annual_kwh / capacity_kwp
    
    return eq_hours


def generate_pv_data(df, lat=40.55, lon=113.4, capacity_kwp=100.0, pv_data_path=None, plot_img=False):
    """
    模拟数据生成

    Args:
        df (_type_): _description_
        lat (float, optional): _description_. Defaults to 40.55.
        lon (float, optional): _description_. Defaults to 113.4.
        capacity_kwp (float, optional): _description_. Defaults to 100.0.
        pv_data_path (_type_, optional): _description_. Defaults to None.
        plot_img (bool, optional): _description_. Defaults to False.

    Returns:
        _type_: _description_
    """
    if not pv_data_path.exists():
        # 通过 `pvlib` 计算光伏 AC 输出功率序列。输出是以时间为索引的 `pv_kw` 序列
        pv_kw = simulate_pv_output(
            time_index=df["Time"],
            lat=lat,
            lon=lon,
            capacity_kwp=capacity_kwp,   # 单位 kWp(MW?)
        )
        # 校验光伏序列的年等效利用小时数，判断模拟结果是否落在合理范围
        eq_h = validate_equivalent_hours(pv_kw, capacity_kwp=capacity_kwp)
        print("等效小时:", round(eq_h, 1))
        # 抽取某一天的光伏出力曲线并作图，主要服务于结果可视化和形状检查
        # if plot_img:
        #     plot_daily_pv_shape(pv_kw, "2025-06-15")
        # 数据保存
        tmp = pv_kw.rename("pv_kw").to_frame()
        tmp.index.name = "Time"
        tmp.to_csv(pv_data_path, index=True, encoding="utf-8")
    else:
        tmp = pd.read_csv(pv_data_path, encoding="utf-8", index_col="Time", parse_dates=True)
        pv_kw = tmp["pv_kw"]

    return pv_kw  # pd.Series, index=DatetimeIndex




# 测试代码 main 函数
def main():
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from src.wind_pv_es_calc.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("data/wind_pv_es_calc/temp/df_2025.csv")
    df_2025 = load_data(energy_data_path=energy_data_path)
    print(df_2025)
    # ------------------------------
    # PV power data
    # ------------------------------
    pv_data_path = Path("data/wind_pv_es_calc/temp/df_pv_2025.csv")
    pv_kw = generate_pv_data(df=df_2025, lat=40.55, lon=113.4, capacity_kwp=100.0, pv_data_path=pv_data_path, plot_img=False)
    print(pv_kw)

if __name__ == "__main__":
    main()
