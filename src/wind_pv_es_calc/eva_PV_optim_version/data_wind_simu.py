# -*- coding: utf-8 -*-

# ***************************************************
# * File        : wind_simu.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042014
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import copy
import requests
from pathlib import Path
from dataclasses import dataclass

import pandas as pd
import numpy as np
from windpowerlib import WindTurbine, ModelChain


def fetch_era5_land_open_meteo(
    lat: float,
    lon: float,
    start_date: str,   # YYYY-MM-DD
    end_date: str,     # YYYY-MM-DD
) -> pd.DataFrame:
    """
    Open-Meteo 接口获取 ERA5-Land 小时级天气数据，主要使用风速和气温作为风电建模输入
    返回列：Time, wind_speed_100m, temperature_2m
    """
    url = "https://archive-api.open-meteo.com/v1/era5"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ["wind_speed_100m", "temperature_2m"],
        "timezone": "UTC",
    }

    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    data = r.json()["hourly"]

    df = pd.DataFrame({
        "Time": pd.to_datetime(data["time"]),
        "wind_speed_100m": data["wind_speed_100m"],
        "temperature_2m": data["temperature_2m"],
    })

    return df.sort_values("Time").reset_index(drop=True)


def resample_hourly_to_15min(df_hourly: pd.DataFrame) -> pd.DataFrame:
    """
    把小时级气象数据插值或重采样到更细粒度，便于与 15 分钟负荷曲线对齐

    Args:
        df_hourly (pd.DataFrame): _description_

    Returns:
        pd.DataFrame: _description_
    """
    df = df_hourly.copy()
    df["Time"] = pd.to_datetime(df["Time"])
    df = df.set_index("Time").sort_index()

    full_index = pd.date_range(df.index.min(), df.index.max(), freq="15min")
    df = (
        df.reindex(full_index)
        .interpolate("time")
        .ffill()
        .bfill()
    )
    df.index.name = "Time"
    
    return df.reset_index()


def calibrate_energy_with_cap(
    p_base_mw: np.ndarray,
    cap_mw: float,
    dt_h: float,
    target_flh: float,
    tol_flh: float = 0.2,
    max_iter: int = 60,
) -> np.ndarray:
    """
    # TODO 未使用：对原始风功率序列做年发电量校准，并施加装机上限约束
    在 clip(0, cap_mw) 约束下，通过二分法寻找缩放系数, 使等效利用小时逼近 target_flh。
    """
    target_energy = cap_mw * target_flh

    def energy(scale: float) -> float:
        return float(np.clip(p_base_mw * scale, 0.0, cap_mw).sum() * dt_h)

    # 边界情况
    if energy(1.0) <= 1e-12:
        return np.zeros_like(p_base_mw)

    lo, hi = 0.0, 1.0
    while energy(hi) < target_energy and hi < 1e6:
        hi *= 2.0

    # 如果无论如何都达不到（理论上极少见）
    if energy(hi) < target_energy:
        return np.clip(p_base_mw * hi, 0.0, cap_mw)

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        e_mid = energy(mid)
        flh_mid = e_mid / cap_mw

        if abs(flh_mid - target_flh) <= tol_flh:
            return np.clip(p_base_mw * mid, 0.0, cap_mw)

        if e_mid < target_energy:
            lo = mid
        else:
            hi = mid

    return np.clip(p_base_mw * 0.5 * (lo + hi), 0.0, cap_mw)


@dataclass
class WindFarmConfig:
    """
    定义风场级别的关键参数，如装机规模、轮毂高度、功率曲线参数、等效利用小时数目标、峰值比例限制等
    """
    # 时间
    year: int = 2025
    freq: str = "1h" # "15min", "15T"
    # 校正目标
    mean_wind_speed_140m: float = 5.5
    eq_full_load_hours: float = 1900.0
    # 高度与地形
    meteo_height_m: float = 100.0
    met_mast_height_m: float = 140.0
    hub_height_m: float = 140.0
    shear_alpha: float = 0.2
    # 风机（≤8MW，低风速）
    rated_power_kw: float = 5000.0
    cut_in: float = 3.0
    rated_speed: float = 11.0
    cut_out: float = 25.0
    # 峰值 ≤ 1.2 × 装机
    max_power_ratio: float = 1.2
    # others
    farm_capacity_mw: float = 80.0
    flh_tol: float = 0.3


class WindFarmPowerModelERA5Land:
    """
    主模型。它通过风速高度换算、自定义功率曲线、风机配置和年能量回补逻辑，把气象输入变成风电功率序列
    """
    def __init__(self, cfg: WindFarmConfig):
        self.cfg = cfg
        self.dt_h = pd.to_timedelta(cfg.freq).total_seconds() / 3600.0
    # -----------------------------
    # 幂律
    # -----------------------------
    @staticmethod
    def power_law(v, h_from, h_to, alpha):
        return v * (h_to / h_from) ** alpha
    # -----------------------------
    # 风机
    # -----------------------------
    def build_turbine(self) -> WindTurbine:
        v = np.arange(0, 31, 0.5)
        p = np.zeros_like(v)

        m1 = (v >= self.cfg.cut_in) & (v < self.cfg.rated_speed)
        p[m1] = (
            ((v[m1] - self.cfg.cut_in)
             / (self.cfg.rated_speed - self.cfg.cut_in)) ** 3
            * self.cfg.rated_power_kw
        )

        m2 = (v >= self.cfg.rated_speed) & (v < self.cfg.cut_out)
        p[m2] = self.cfg.rated_power_kw

        return WindTurbine(
            hub_height=self.cfg.hub_height_m,
            nominal_power=self.cfg.rated_power_kw,
            power_curve=pd.DataFrame(
                {"wind_speed": v, "value": p}
            ),
        )
    # ------------------------------
    # 关键函数：带峰值约束的迭代能量回补
    # ------------------------------
    @staticmethod
    def _rescale_with_cap_iterative(
        p_base_mw: np.ndarray,
        cap_mw: float,
        dt_h: float,
        target_flh: float,
        max_ratio: float,
        tol_flh: float,
        max_iter: int = 8,
    ) -> np.ndarray:
        """
        目标：
        1) 峰值 ≤ max_ratio × cap_mw
        2) 年 FLH ≈ target_flh
        3) 能量守恒（削峰后通过未饱和时段回补）
        """
        target_energy = cap_mw * target_flh
        p = p_base_mw.copy()
        max_p = cap_mw * max_ratio

        for _ in range(max_iter):
            # ---- 1. 全局缩放到目标能量 ----
            cur_energy = p.sum() * dt_h
            if cur_energy <= 0:
                break
            p *= target_energy / cur_energy

            # ---- 2. 峰值约束 ----
            p_clipped = np.minimum(p, max_p)

            # ---- 3. 计算当前 FLH ----
            energy = p_clipped.sum() * dt_h
            flh = energy / cap_mw

            if abs(flh - target_flh) <= tol_flh:
                return p_clipped

            # ---- 4. 回补被削掉的能量 ----
            deficit = target_energy - energy
            if deficit <= 0:
                return p_clipped

            margin = max_p - p_clipped
            margin[margin < 0] = 0.0
            if margin.sum() <= 0:
                return p_clipped

            p = p_clipped + margin / margin.sum() * (deficit / dt_h)

        return p_clipped
    # ------------------------------
    # 主函数
    # ------------------------------ 
    def simulate(self, lat: float, lon: float, output_unit: str = "MW") -> pd.DataFrame:
        # ========= 1. 气象 =========
        df_hour = fetch_era5_land_open_meteo(
            lat=lat,
            lon=lon,
            start_date=f"{self.cfg.year}-01-01",
            end_date=f"{self.cfg.year}-12-31",
        )
        # df_15 = resample_hourly_to_15min(df_hour)
        df_15 = copy.deepcopy(df_hour)
        ti = pd.date_range(
            f"{self.cfg.year}-01-01 00:00:00",
            f"{self.cfg.year}-12-31 23:45:00",
            freq=self.cfg.freq,
        )
        df_15 = (
            df_15.set_index("Time")
            .reindex(ti)
            .interpolate("time")
            .ffill()
            .bfill()
        )
        # ========= 2. 风速处理 =========
        ws_100 = df_15["wind_speed_100m"].clip(lower=0)
        ws_140 = self.power_law(
            ws_100,
            self.cfg.meteo_height_m,
            self.cfg.met_mast_height_m,
            self.cfg.shear_alpha,
        )
        ws_140 *= self.cfg.mean_wind_speed_140m / ws_140.mean()
        ws_hub = self.power_law(
            ws_140,
            self.cfg.met_mast_height_m,
            self.cfg.hub_height_m,
            self.cfg.shear_alpha,
        )
        # ========= 3. 单机功率 =========
        weather = pd.DataFrame({
            ("wind_speed", self.cfg.hub_height_m): ws_hub.values,
            ("temperature", 2.0): (df_15["temperature_2m"] + 273.15).values,
        }, index=ti)
        # 风机
        turbine = self.build_turbine()
        mc = ModelChain(turbine).run_model(weather)
        p_single_kw = mc.power_output.values
        # ========= 4. 风场聚合 =========
        n_turb = max(1, int(round(self.cfg.farm_capacity_mw * 1000 / turbine.nominal_power)))
        p_base_mw = p_single_kw * n_turb / 1000.0
        # ========= 5. 二次标定（核心） =========
        p_final = self._rescale_with_cap_iterative(
            p_base_mw=p_base_mw,
            cap_mw=self.cfg.farm_capacity_mw,
            dt_h=self.dt_h,
            target_flh=self.cfg.eq_full_load_hours,
            max_ratio=self.cfg.max_power_ratio,
            tol_flh=self.cfg.flh_tol,
        )
        # ========= 6. 输出 =========
        unit = output_unit.upper()
        if unit == "MW":
            return pd.DataFrame({"WindPower_MW": p_final}, index=ti)
        elif unit == "KW":
            return pd.DataFrame({"WindPower_kW": p_final * 1000.0}, index=ti)
        else:
            raise ValueError("output_unit must be 'MW' or 'kW'")


def generate_wind_data(farm_capacity_mw=200.0, mean_wind_speed_140m=7.27, eq_full_load_hours=2590, lat=40.55, lon=113.4, wind_data_path=None):
    """
    模拟数据生成

    Args:
        farm_capacity_mw (float, optional): _description_. Defaults to 200.0.
        mean_wind_speed_140m (float, optional): _description_. Defaults to 7.27.
        eq_full_load_hours (int, optional): _description_. Defaults to 2590.
        lat (float, optional): _description_. Defaults to 40.55.
        lon (float, optional): _description_. Defaults to 113.4.
        wind_data_path (_type_, optional): _description_. Defaults to None.

    Returns:
        _type_: _description_
    """
    if not wind_data_path.exists():
        # config
        cfg_wind = WindFarmConfig(
            farm_capacity_mw=farm_capacity_mw,
            mean_wind_speed_140m=mean_wind_speed_140m,
            eq_full_load_hours=eq_full_load_hours,
        )
        # model
        model = WindFarmPowerModelERA5Land(cfg_wind)
        # result
        df_wind = model.simulate(lat=lat, lon=lon)
        df_wind["Time"] = df_wind.index
        df_wind = df_wind[["Time", "WindPower_MW"]]
        # result save
        df_wind.to_csv(wind_data_path, index=False, encoding="utf-8")
    else:
        df_wind = pd.read_csv(wind_data_path, encoding="utf-8")

    return df_wind




# 测试代码 main 函数
def main():
    # data path
    wind_data_path = Path("data/wind_pv_es_calc/temp/df_wind_2025.csv")
    # data load
    df_wind = generate_wind_data(
        farm_capacity_mw=200.0, 
        mean_wind_speed_140m=7.27, 
        eq_full_load_hours=2590, 
        lat=40.55, 
        lon=113.4, 
        wind_data_path=wind_data_path
    )
    print(df_wind)

if __name__ == "__main__":
    main()
