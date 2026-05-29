# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim_0.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042018
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
from typing import Dict

import numpy as np
import pandas as pd
from wind_pv_es_calc.storage_optim_common import (
    njit, NUMBA_OK,
    PlanConfigFast,
    dispatch_numba,
    infer_dt_hours, align_to_time, monthly_kwh,
)


# 当前脚本的定位更接近“基于负荷与单位 PV 曲线的最小 PV 投资搜索”。
# 虽然文件名里包含 BESS，但当前主规划函数并没有真正搜索储能容量，
# 而是保留了储能调度接口与结果字段，方便后续把 BESS 联合优化接回主流程。

# ------------------------------
# 年度调度（Numba）
# ------------------------------
# 给定全年逐时负荷与 PV 出力，按“先直供、后充电、再放电补缺口”的口径做能量结算。
# 返回的 pv_used 包含两部分：
# 1) 光伏直接供负荷的电量
# 2) 先由 PV 充入电池、再由电池放出供负荷的电量
# 因此 direct_e = pv_used - bess_dis，表示不经过电池的直接消纳电量。
@njit
def _dispatch_annual_numba(load_kw, 
                           pv_kw, 
                           dt_hours, 
                           batt_kwh,
                           eta_roundtrip, 
                           c_rate,
                           soc_init_frac, soc_min_frac, soc_max_frac):
    soc0 = soc_init_frac
    if soc0 < soc_min_frac:
        soc0 = soc_min_frac
    if soc0 > soc_max_frac:
        soc0 = soc_max_frac

    pv_gen, pv_used, load_e, bess_dis = dispatch_numba(
        load_kw,
        pv_kw,
        dt_hours,
        batt_kwh,
        eta_roundtrip,
        c_rate,
        soc0,
        soc_min_frac,
        soc_max_frac,
    )
    direct_e = pv_used - bess_dis
    
    return pv_gen, pv_used, load_e, direct_e, bess_dis


def _dispatch_annual(load_kw, pv_kw, dt_hours, batt_kwh, cfg: PlanConfigFast):
    # 统一封装调度结果字段，便于上层规划逻辑只关心业务指标，不关心底层实现细节。
    if cfg.use_numba and NUMBA_OK:
        return dict(zip(
            ["pv_gen_kwh", "pv_used_kwh", "load_kwh", "direct_used_kwh", "bess_discharge_kwh"],
            _dispatch_annual_numba(
                load_kw, 
                pv_kw, 
                dt_hours, 
                batt_kwh,
                cfg.eta_roundtrip, 
                cfg.c_rate,
                cfg.soc_init_frac, 
                cfg.soc_min_frac, 
                cfg.soc_max_frac
            )
        ))

    # fallback 只保证输出字段口径一致，不保证与 numba 分支完全同逻辑：
    # 1) 不裁剪负值
    # 2) 不模拟电池充放电
    # 因此它更像兼容旧行为的保底路径，而不是等价的年度调度器。
    direct = np.minimum(load_kw, pv_kw)
    return {
        "pv_gen_kwh": float(pv_kw.sum() * dt_hours),
        "pv_used_kwh": float(direct.sum() * dt_hours),
        "load_kwh": float(load_kw.sum() * dt_hours),
        "direct_used_kwh": float(direct.sum() * dt_hours),
        "bess_discharge_kwh": 0.0,
    }

# ------------------------------
# 主规划函数（完整版）
# 把单位光伏出力曲线与全年负荷曲线结合起来，搜索满足约束的最小投资方案。
# 当前版本的实际行为是“只搜索 PV 装机容量”，BESS 仍是接口占位：
# 调度函数支持电池参数，但主循环固定用 batt_kwh=0.0，因此结果不会真正搜索储能容量。
# ------------------------------
def plan_pv_bess_min_capex_fast(df_2025: pd.DataFrame,
                                pv_unit_kw: pd.Series,
                                load_col: str = "P_kw",
                                time_col: str = "Time",
                                cfg: PlanConfigFast = PlanConfigFast()) -> Dict[str, object]:
    """
    输出：
      PV装机容量(kWp)
      PV全年各月发电量(kWh)
      PV投资(元)
      储能装机容量(kWh)
      储能投资(元)
    """
    # 先把输入负荷整理成统一的时间序列格式，避免后续时序计算受原始顺序影响。
    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    
    # 根据时间列推断单个时段长度，后续所有功率到电量的换算都依赖这个 dt。
    dt_hours = infer_dt_hours(df[time_col])

    # unit_kw 表示“1 kWp 光伏在各时刻的出力曲线”，需要先对齐到负荷时间轴。
    # 如果时间戳不完全一致，这里会做插值和缺失补零。
    unit_kw = align_to_time(df[time_col], pv_unit_kw)
    print(len(unit_kw))

    # 负荷统一转成 float64 数组，后续调度函数按 ndarray 处理更高效。
    load_kw = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    peak_load = float(load_kw.max())
    # 搜索上界默认取“步长下限”和“3 倍峰值负荷”中的较大者。
    # 这不是严格的业务上界，只是为了给研究型穷举一个足够大的搜索空间。
    pv_max_kwp = cfg.pv_max_kwp or max(cfg.pv_step_coarse_kwp, 3.0 * peak_load)
    
    # 先计算“每 1 kWp 对应的月发电量”，找到最优 PV 容量后可直接线性缩放输出月度结果。
    unit_monthly_kwh = monthly_kwh(df[time_col], unit_kw, dt_hours)
    
    # 全年负荷总电量是覆盖率约束的分母，也是全年快速剪枝的基准量。
    load_kwh_total = float(load_kw.sum() * dt_hours)

    # best 保存当前找到的最便宜可行方案；当前成本口径只包含 PV CAPEX。
    best = None
    # 按粗粒度步长枚举 PV 装机容量，寻找第一个满足约束的最小 CAPEX 方案。
    pv_candidates = np.arange(cfg.pv_step_coarse_kwp, pv_max_kwp + 1e-9, cfg.pv_step_coarse_kwp)
    for pv_kwp in pv_candidates:
        # 单位曲线按装机容量线性放大，得到该候选容量下的全年 PV 功率曲线。
        pv_kw = unit_kw * pv_kwp

        # ---- 快速剪枝 ----
        # 如果全年总发电量连“目标覆盖率对应的最低能量门槛”都达不到，
        # 就没必要再做逐时调度计算。
        if pv_kw.sum() * dt_hours < cfg.load_cover_ratio_min * load_kwh_total:
            continue

        # 这里显式传入 batt_kwh=0.0，说明当前主规划并未真正启用 BESS 搜索。
        # 换句话说，这一步是在“无储能”假设下评估 PV 的年度消纳表现。
        stats = _dispatch_annual(load_kw, pv_kw, dt_hours, 0.0, cfg)
        if stats["pv_gen_kwh"] <= 0:
            continue

        # 自用率：PV 最终被负荷消纳的比例。
        self_use = stats["pv_used_kwh"] / stats["pv_gen_kwh"]
        # 覆盖率：全年负荷里有多大比例由 PV 消纳贡献覆盖。
        cover = stats["pv_used_kwh"] / load_kwh_total

        # 两个比例约束都满足，才认为该候选容量在业务上可接受。
        if self_use < cfg.self_use_ratio_min or cover < cfg.load_cover_ratio_min:
            continue

        pv_capex = pv_kwp * cfg.pv_capex_yuan_per_kwp
        # 当前版本的总投资只有 PV CAPEX，尚未把储能投资纳入优化目标。
        total_capex = pv_capex

        # 在所有可行解中保留总投资最小的方案。
        if best is None or total_capex < best["total_capex_yuan"]:
            best = {
                "pv_kwp": float(pv_kwp),
                "pv_monthly_kwh": unit_monthly_kwh * pv_kwp,
                "pv_capex_yuan": pv_capex,
                "bess_kwh": 0.0,
                "bess_capex_yuan": 0.0,
                "total_capex_yuan": total_capex,
                "self_use_ratio": self_use,
                "load_cover_ratio": cover,
                "engine": "numba" if (cfg.use_numba and NUMBA_OK) else "python",
            }

    if best is None:
        raise ValueError("未找到满足约束的 PV+储能配置，请检查比例或扩大搜索范围。")

    return best

# ------------------------------
# data check
# ------------------------------
# 这是一个“年能量下界估算器”，不看逐时曲线错配，只用年电量与年利用小时做量级校验。
# 适合快速回答“如果要达到某个覆盖率，大概要配多少 MWp”。
def simple_energy_sanity_check(df_2025: pd.DataFrame,
                               time_col="Time",
                               load_col="P_kw",
                               target_cover=0.30,
                               self_use_min=0.60,
                               yield_list=(1000, 1100, 1200, 1300),  # kWh/kWp·年
                               ):
    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col)

    dt_h = df[time_col].diff().dropna().mode().iloc[0].total_seconds() / 3600
    load_kwh_year = df[load_col].sum() * dt_h

    pv_used_target = target_cover * load_kwh_year
    pv_gen_required = pv_used_target / self_use_min

    rows = []
    for y in yield_list:
        rows.append({
            "yield_kWh_per_kWp_yr": y,
            "pv_required_MWp": pv_gen_required / y / 1000
        })

    return {
        "load_gwh_year": load_kwh_year / 1e6,
        "pv_used_target_gwh": pv_used_target / 1e6,
        "pv_gen_required_gwh": pv_gen_required / 1e6,
        "pv_required_table": pd.DataFrame(rows)
    }

# 这是一个“基于单位 PV 曲线总年发电量”的量级校验器。
# 它比固定年利用小时更贴近本项目的 PV 曲线，但本质上仍是能量法估算，不是正式优化模型。
def curve_based_energy_check(df_2025: pd.DataFrame,
                             pv_unit_kw: pd.Series,
                             time_col="Time",
                             load_col="P_kw",
                             target_cover=0.30,
                             self_use_min=0.60):
    df = df_2025[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col)

    pv_unit_kw = pv_unit_kw.reindex(df[time_col]).fillna(0.0)

    dt_h = df[time_col].diff().dropna().mode().iloc[0].total_seconds() / 3600
    load_kwh_year = df[load_col].sum() * dt_h

    # 单位 kWp 年发电量
    yield_curve = pv_unit_kw.sum() * dt_h

    pv_used_target = target_cover * load_kwh_year
    pv_gen_required = pv_used_target / self_use_min
    pv_kwp_required = pv_gen_required / yield_curve

    return {
        "load_gwh_year": load_kwh_year / 1e6,
        "yield_curve_kWh_per_kWp": yield_curve,
        "pv_required_MWp": pv_kwp_required / 1000
    }




# 测试代码 main 函数
def main():
    # 这是研究脚本式的演示入口，用来串起数据准备、PV 曲线生成、规划求解和结果对比。
    # 它适合本地快速验证思路，不代表稳定的产品化入口。
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from wind_pv_es_calc.eva_PV_optim_version.data_loader import load_data
    # data path
    energy_data_path = Path("data/wind_pv_es_calc/temp/df_2025.csv")
    # data load
    df_2025 = load_data(energy_data_path=energy_data_path)
    print(df_2025)
    # ------------------------------
    # PV power data
    # ------------------------------
    from wind_pv_es_calc.eva_PV_optim_version.data_pv_simu import generate_pv_data
    # data path
    pv_data_path = Path("data/wind_pv_es_calc/temp/df_pv_2025.csv")
    # 这里生成的是 1.0 kWp 光伏对应的逐时出力曲线，后续通过线性缩放得到任意装机容量。
    pv_kw = generate_pv_data(df=df_2025, lat=40.55, lon=113.4, capacity_kwp=1.0, pv_data_path=pv_data_path, plot_img=False)
    print(pv_kw)
    # ------------------------------
    # 光伏 + 储能测算
    # ------------------------------
    cfg = PlanConfigFast(
        pv_step_fine_kwp=500.0,
        load_cover_ratio_min=0.35,
    )
    res = plan_pv_bess_min_capex_fast(
        df_2025=df_2025,
        pv_unit_kw=pv_kw,
        load_col="P_kw",
        time_col="Time",
        cfg=cfg,
    )
    print("PV装机(kWp):", res["pv_kwp"])
    print("PV投资(元):", res["pv_capex_yuan"])
    print("储能容量(kWh):", res["bess_kwh"])
    print("储能投资(元):", res["bess_capex_yuan"])
    print("\nPV各月发电量(kWh):")
    print(res["pv_monthly_kwh"])
    print("\n约束指标：")
    print("口径:", cfg.constraint_mode)
    print("PV自用率 PV_used / PV_gen:", res["self_use_ratio"])
    print("PV覆盖率 PV_used / Load:", res["load_cover_ratio"])
    # 导出的是规划结果对应的月度 PV 发电量，便于和外部报表或后续分析衔接。
    res["pv_monthly_kwh"].to_csv("data/wind_pv_es_calc/temp/pv_monthly_kwh.csv")
    # ------------------------------
    # 两个 sanity check 用于判断规划结果的量级是否合理，不替代正式优化模型。
    # ------------------------------
    out_A = simple_energy_sanity_check(df_2025)
    print("年用电量(GWh):", out_A["load_gwh_year"])
    print(out_A["pv_required_table"])

    out_B = curve_based_energy_check(df_2025, pv_kw)
    print(out_B)
    
    # comparison 里的 C 项目前是手工对照值 244.0，不是从 res 动态读取出来的规划结果。
    # 它更像一次研究比对时留下的参考数，而不是正式报表字段。
    comparison = pd.DataFrame([
        {
            "method": "A-年能量下界(1200h)",
            "pv_mwp": out_A["pv_required_table"].query("yield_kWh_per_kWp_yr==1200")["pv_required_MWp"].iloc[0]
        },
        {
            "method": "B-单位曲线能量",
            "pv_mwp": out_B["pv_required_MWp"]
        },
        {
            "method": "C-规划模型输出",
            "pv_mwp": 244.0
        }
    ])
    print(comparison)

if __name__ == "__main__":
    main()
