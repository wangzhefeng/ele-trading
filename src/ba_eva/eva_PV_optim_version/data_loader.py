# -*- coding: utf-8 -*-

# ***************************************************
# * File        : 1.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042014
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

from calendar import monthrange
import numpy as np
import pandas as pd

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
# from utils.log_util import logger


# ##############################
# 负责批量读取目录下的 Excel 文件，统一生成 `Time` 和 `P_kw` 字段，并保留来源文件名。它只做最基础的时间列拼接和数值规范化，不处理插值、补点或异常值修正
# ##############################
def read_power_folder_raw(folder_path: str,
                          date_col: str = "数据日期",
                          time_col: str = "时间",
                          power_col: str = "功率(KW)",
                          keep_source: bool = True) -> pd.DataFrame:
    """
    仅负责读取 + Time 构造 + 字段规范
    不做任何清洗、补点、插值、修正
    """
    dfs = []
    for file in Path(folder_path).glob("*.xlsx"):
        df = pd.read_excel(file)
        # ========= 1. 构造 Time =========
        time_series = pd.to_datetime(
            df[date_col].astype(str) + " " + df[time_col].astype(str),
            errors="coerce"
        )
        out = pd.DataFrame({
            "Time": time_series,
            "P_kw": df[power_col]
        })
        out["P_kw"] = pd.to_numeric(out["P_kw"], errors="coerce")

        if keep_source:
            out["source_file"] = file.name

        # 只做最基础的合法性过滤
        out = out.dropna(subset=["Time"])

        dfs.append(out)

    if not dfs:
        raise ValueError(f"目录 {folder_path} 下未找到可读取的 Excel 文件")

    df_all = (
        pd.concat(dfs, ignore_index=True)
          .sort_values("Time")
          .reset_index(drop=True)
    )
    df_all = df_all.drop(columns=["source_file"])
    df_all = df_all.sort_values("Time")

    return df_all

# ##############################
# 根据手工给定的月电量字典，展开成 2025 年每日电量目标，用于后续对负荷曲线进行“总量回填”
# ##############################
def build_daily_energy_2025(month_energy_2025):
    daily_energy = {}
    for m_str, E_month in month_energy_2025.items():
        m = int(m_str.replace("月", ""))
        days = monthrange(2025, m)[1]
        for d in range(1, days + 1):
            daily_energy[pd.Timestamp(2025, m, d).date()] = E_month / days
    
    return daily_energy

# ##############################
# 用日总电量约束来回填 2025 年负荷曲线中的缺失点。其核心思路是：先计算当天已知点电量，再把剩余日电量按照时间插值后的权重分配到缺失点
# ##############################
def fill_2025_power_by_daily_energy(df, daily_energy, freq="15min"):
    df = df.copy()
    dt_hours = pd.to_timedelta(freq).total_seconds() / 3600

    df["date"] = df["Time"].dt.date
    df["year"] = df["Time"].dt.year

    for date, E_day in daily_energy.items():
        mask_day = (df["date"] == date) & (df["year"] == 2025)
        day_df = df.loc[mask_day]

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
            # .fillna(method="bfill")
            # .fillna(method="ffill")
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

        df.loc[
            day_df.index[mask_arr],
            "P_kw"
        ] = P_fill

    return df.drop(columns=["date", "year"])

# ##############################
# 保留历史形状特征，并将其迁移到 2025 年序列中，属于“用历史曲线形状辅助构造未来负荷”的方法
# ##############################
def smooth_2024_shape(df):
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
    df_2024 = df[
        (df["Time"].dt.year == 2024) &
        (df["Time"].dt.month >= 9)
    ].copy()

    df_2024["Time"] = df_2024["Time"] + pd.DateOffset(years=1)
    return df_2024
# ##############################
# 进一步处理整天缺失的问题，按邻近日期的曲线形状去补齐缺口
# ##############################
def fill_missing_days_by_nearest(df_2025, missing_days, freq="15T"):
    df = df_2025.copy()

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


def load_data(raw_data_dir, energy_data_path):
    if not energy_data_path.exists():
        # ------------------------------
        # 读取“负荷曲线”数据
        # ------------------------------
        df_raw = read_power_folder_raw(folder_path=raw_data_dir)
        # ------------------------------
        # 手工指定的月度电量参数，用于构造 2025 年每日目标电量
        # ------------------------------
        month_energy_2025 = {
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
        # ------------------------------
        # 
        # ------------------------------
        # 1. 构建 2025 日电量
        daily_energy_2025 = build_daily_energy_2025(month_energy_2025)
        # 2. 补全 2025 年功率（强约束）
        df_step1 = fill_2025_power_by_daily_energy(df_raw, daily_energy_2025)
        # 3. 修复 2024 年形态（弱约束）
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
        df_2025 = df_final[df_final["Time"].dt.year == 2025].copy()
        df_2025 = df_2025.sort_values("Time").reset_index(drop=True)

        all_days_2025 = pd.date_range(start="2025-01-01", end="2025-12-31", freq="D").date
        exist_days_2025 = df_2025["Time"].dt.date.unique()
        
        missing_days_2025 = sorted(set(all_days_2025) - set(exist_days_2025))
        
        df_2025 = fill_missing_days_by_nearest(df_2025, missing_days_2025)
        # ------------------------------
        # data save
        # ------------------------------
        df_2025.to_csv(energy_data_path, index=False, encoding="utf-8")
    else:
        df_2025 = pd.read_csv(energy_data_path)

    return df_2025




# 测试代码 main 函数
def main():
    # data path
    raw_energy_data_dir = Path("src/ba_eva/dataset/负荷曲线/")
    energy_data_path = Path("src/ba_eva/dataset/temp/df_2025.csv")
    # data load
    df_2025 = load_data(raw_data_dir=raw_energy_data_dir, energy_data_path=energy_data_path)
    print(df_2025)

if __name__ == "__main__":
    main()
