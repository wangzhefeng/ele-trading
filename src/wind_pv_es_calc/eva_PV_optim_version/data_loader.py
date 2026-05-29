# -*- coding: utf-8 -*-

# ***************************************************
# * File        : data_loader.py
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

import numpy as np
import pandas as pd
from calendar import monthrange


def read_power_folder_raw(folder_path: str,
                          date_col: str = "数据日期",
                          time_col: str = "时间",
                          power_col: str = "功率(KW)",
                          keep_source: bool = True) -> pd.DataFrame:
    """
    负责批量读取目录下的 Excel 文件，统一生成 `Time` 和 `P_kw` 字段，并保留来源文件名。
    它只做最基础的时间列拼接和数值规范化，不处理插值、补点或异常值修正
    """
    dfs = []
    for file in Path(folder_path).glob("*.xlsx"):
        # 1.读取文件
        df = pd.read_excel(file)
        # 2.构造 Time
        time_series = pd.to_datetime(df[date_col].astype(str) + " " + df[time_col].astype(str), errors="coerce")
        # 3.构造 DataFrame
        out = pd.DataFrame({
            "Time": time_series,
            "P_kw": df[power_col]
        })
        # 4.数值规范化
        out["P_kw"] = pd.to_numeric(out["P_kw"], errors="coerce")
        # 5. 保留文件名
        if keep_source:
            out["source_file"] = file.name
        # 6. 去除空值: 只做最基础的合法性过滤
        out = out.dropna(subset=["Time"])
        # 7. 收集数据集
        dfs.append(out)
    # 数据集验证
    if not dfs:
        raise ValueError(f"目录 {folder_path} 下未找到可读取的 Excel 文件")
    # 数据合并
    df_all = pd.concat(dfs, ignore_index=True).sort_values("Time").reset_index(drop=True)
    # 删除 source_file 字段
    df_all = df_all.drop(columns=["source_file"])
    df_all = df_all.sort_values("Time")

    return df_all


def build_daily_energy(year_of_data):
    """
    根据手工给定的月电量字典，展开成 2025 年每日电量目标，用于后续对负荷曲线进行“总量回填”
    """
    # 手工指定的月度电量参数，用于构造 2025 年每日目标电量
    month_energy = {
        "1月": 49603488, 
        "2月": 45597288, 
        "3月": 58249488, 
        "4月": 56562529, 
        "5月": 61035744, 
        "6月": 62137944, 
        "7月": 66238920, 
        "8月": 63494376, 
        "9月": 53279952, 
        "10月": 57649944, 
        "11月": 54806928, 
        "12月": 56779800,
    }
    daily_energy = {}
    for m_str, E_month in month_energy.items():
        m = int(m_str.replace("月", ""))
        days = monthrange(year_of_data, m)[1]
        for d in range(1, days + 1):
            daily_energy[pd.Timestamp(year_of_data, m, d).date()] = E_month / days
    
    return daily_energy


def fill_power_by_daily_energy(df, year_of_data, daily_energy, freq="15min"):
    """
    用日总电量约束来回填 year_of_data 年负荷曲线中的缺失点。
    其核心思路是：先计算当天已知电量，再把剩余日电量按照时间插值后的权重分配到缺失点
    """
    dt_hours = pd.to_timedelta(freq).total_seconds() / 3600
    
    df = df.copy()
    df["date"] = df["Time"].dt.date
    df["year"] = df["Time"].dt.year

    for date, E_day in daily_energy.items():
        day_df = df.loc[(df["date"] == date) & (df["year"] == year_of_data)]

        if day_df.empty:
            continue
        # ===== 使用 Time 仅用于插值 =====
        day_df_t = day_df.set_index("Time")

        known_mask = day_df_t["P_kw"].notna()
        missing_mask = day_df_t["P_kw"].isna()

        E_known = (day_df_t.loc[known_mask, "P_kw"] * dt_hours).sum()
        E_missing = E_day - E_known

        if E_missing <= 0 or missing_mask.sum() == 0:
            df.loc[day_df.index[missing_mask.values], "P_kw"] = 0.0
            continue

        weight = (
            day_df_t["P_kw"]
            .interpolate(method="time")
            .ffill()
            .bfill()
            .clip(lower=0)
        )
        # ===== 关键修复：只用 numpy 位置索引 =====
        mask_arr = missing_mask.values
        w = weight.values[mask_arr]
        if w.sum() <= 0:
            w = np.ones_like(w)
        P_fill = w / w.sum() * (E_missing / dt_hours)
        df.loc[day_df.index[mask_arr], "P_kw"] = P_fill

    return df.drop(columns=["date", "year"])


def smooth_2024_shape(df):
    """
    保留历史形状特征，并将其迁移到 2025 年序列中，属于“用历史曲线形状辅助构造未来负荷”的方法
    """
    df = df.copy()
    mask_2024 = (df["Time"].dt.year == 2024) & (df["Time"].dt.month >= 9)
    df_2024 = df.loc[mask_2024]

    if df_2024.empty:
        return df
    
    s = (
        df_2024
        .set_index("Time")["P_kw"]
        .interpolate(method="time")
        .rolling(3, center=True, min_periods=1)
        .mean()
    )
    # 用位置回写，彻底避免索引对齐问题
    df.loc[df_2024.index, "P_kw"] = s.values

    return df


def shift_2024_to_2025(df):
    """
    把 2024 形状平移到 2025，再补整天缺失
    """
    df_2024 = df[(df["Time"].dt.year == 2024) & (df["Time"].dt.month >= 9)].copy()
    df_2024["Time"] = df_2024["Time"] + pd.DateOffset(years=1)
    
    return df_2024


def fill_missing_days_by_nearest(df_raw, missing_days, freq="15T"):
    """
    进一步处理整天缺失的问题，按邻近日期的曲线形状去补齐缺口
    """
    df = df_raw.copy()
    for day in missing_days:
        # 取最近的已有日期（这里明确用 9/3）
        ref_day = min(
            df["Time"].dt.date.unique(),
            key=lambda d: abs(pd.Timestamp(d) - pd.Timestamp(day))
        )
        day_curve = df[df["Time"].dt.date == ref_day].copy()
        # 平移日期
        day_curve["Time"] = day_curve["Time"].apply(
            lambda t: t.replace(year=day.year, month=day.month, day=day.day)
        )
        df = pd.concat([df, day_curve], ignore_index=True)

    return df.sort_values("Time").reset_index(drop=True)


def load_data(energy_data_path, year_of_data, raw_data_dir=None):
    if not energy_data_path.exists() and raw_data_dir is not None:
        # 1.读取“负荷曲线”数据
        df_raw = read_power_folder_raw(folder_path=raw_data_dir)
        # 2.构建制定某年日电量
        daily_energy = build_daily_energy(year_of_data)
        # 3.补全某年功率（强约束）
        df_step1 = fill_power_by_daily_energy(df_raw, year_of_data, daily_energy)
        # 4.修复 2024 年形态（弱约束）
        df_step2 = smooth_2024_shape(df_step1)
        # 4. 平移 2024 → 2025
        df_shifted = shift_2024_to_2025(df_step2)
        # 5. 合并
        df_final = (
            pd.concat([df_step2, df_shifted])
            .sort_values("Time")
            .reset_index(drop=True)
        )
        # ------------------------------
        # 
        # ------------------------------
        df = df_final[df_final["Time"].dt.year == year_of_data].copy()
        df = df.sort_values("Time").reset_index(drop=True)

        all_days = pd.date_range(start=f"{year_of_data}-01-01", end=f"{year_of_data}-12-31", freq="D").date
        exist_days = df["Time"].dt.date.unique()
        missing_days = sorted(set(all_days) - set(exist_days))
        df = fill_missing_days_by_nearest(df, missing_days)
        # ------------------------------
        # data save
        # ------------------------------
        df.to_csv(energy_data_path, index=False, encoding="utf-8")
    else:
        df = pd.read_csv(energy_data_path)

    return df




# 测试代码 main 函数
def main():
    # data path
    raw_energy_data_dir = Path("data/wind_pv_es_calc/负荷曲线/")
    energy_data_path = Path("data/wind_pv_es_calc/temp/df_2025.csv")
    # data load
    df = load_data(energy_data_path=energy_data_path, year_of_data=2025, raw_data_dir=raw_energy_data_dir)
    print(df)
    from utils.plot_ts import series_plot
    series_plot(df=df, time_col="Time", value_col="P_kw")

if __name__ == "__main__":
    main()
