# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim2.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042014
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

import pandas as pd
from ba_eva.eva_PV_optim_version.storage_optim_common import UnitsConfig, PlanConfigFast
from ba_eva.eva_PV_optim_version.storage_optim_Wind_BESS import plan_energy_system




# 测试 / 演示入口
# 这部分代码负责：
# 1. 构造负荷样例；
# 2. 生成风电模拟曲线；
# 3. 生成光伏模拟曲线（当前示例里未真正传入主规划函数）；
# 4. 组装输入后调用 plan_energy_system()。
def main():
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("src/ba_eva/dataset/temp/df_2025.csv")
    df_2025 = load_data(energy_data_path=energy_data_path)
    # 对构造出的负荷曲线做一次总量比例校准，使其贴近目标年电量口径。
    df_2025["P_kw"] = df_2025["P_kw"] / 704234268 * 685436401
    print(df_2025)
    # ------------------------------
    # 风电功率数据
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_wind_simu import generate_wind_data
    wind_data_path = Path("src/ba_eva/dataset/temp/df_wind_2026.csv")
    df_wind = generate_wind_data(
        farm_capacity_mw=110.0,
        mean_wind_speed_140m=5.5,
        eq_full_load_hours=1920.7,
        lat=28.42,
        lon=117.88,
        wind_data_path=wind_data_path
    )
    print(df_wind)
    # ------------------------------
    # 光伏功率数据
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_pv_simu import generate_pv_data
    pv_data_path = Path("src/ba_eva/dataset/temp/df_pv_2025.csv")
    pv_kw_28 = generate_pv_data(
        df=df_2025,
        lat=28.42,
        lon=117.88,
        capacity_kwp=28250,
        pv_data_path=pv_data_path,
        plot_img=False
    )
    print(pv_kw_28)
    # ------------------------------
    # 主流程运行
    # ------------------------------
    # 1. 明确转成 datetime，避免后续时间对齐和步长推断出错。
    df_2025["Time"] = pd.to_datetime(df_2025["Time"])
    df_wind["Time"] = pd.to_datetime(df_wind["Time"])

    # 2. 全部设为 Time 索引，便于统一按时间轴对齐。
    df_load = df_2025.set_index("Time")[["P_kw"]]
    df_wind = df_wind.set_index("Time")[["WindPower_MW"]]
    df_pv = pv_kw_28.to_frame(name="PV_kw")  # 若 pv_kw_28 是 Series
    # ------------------------------
    # run version 1
    # ------------------------------
    # 这里的示例要求：
    # - 新能源至少覆盖 30% 的负荷；
    # - 储能搜索上限设为 200 MWh。
    cfg = PlanConfigFast(
        load_cover_ratio_min=0.3,
        batt_hi_max_kwh=2e5,
    )
    units = UnitsConfig(
        load_power="kW",
        wind_power="MW",
    )
    # 当前示例实际只传入了风电，因此这次运行是“纯风 + 储能”的年度消纳评估。
    # 如果要测试风光储联合场景，可以取消下方 pv_unit_kw 的注释。
    res_ess = plan_energy_system(
        df_load=df_load,
        # pv_unit_kw=df_pv,
        wind_input=df_wind,
        time_col="Time",
        load_col="P_kw",
        cfg=cfg,
        units=units,
    )
    print(res_ess)
    # ------------------------------
    # run version 2
    # ------------------------------
    cfg = PlanConfigFast(
        load_cover_ratio_min=2.0,
        batt_hi_max_kwh=20000.0,
    )
    units = UnitsConfig(
        load_power="kW",
        wind_power="MW",
    )
    res = plan_energy_system(
        df_load=df_2025,
        # pv_unit_kw=df_pv,
        wind_input=df_wind,
        time_col="Time",
        load_col="P_kw",
        cfg=cfg,
        units=units,
    )
    print(res)

if __name__ == "__main__":
    main()
