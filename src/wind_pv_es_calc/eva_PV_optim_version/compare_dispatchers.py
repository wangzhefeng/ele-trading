# -*- coding: utf-8 -*-
"""
对比 BESS_1 (evaluate/dispatch_numba) 与 BESS_2 (simulate_dispatch_offgrid_shiftable)
在同一输入下的仿真结果差异。
"""
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)

import numpy as np
import pandas as pd

from wind_pv_es_calc.storage_optim_common import (
    PlanConfigFast, evaluate, BESSConfig, ShiftPolicy, align_and_merge,
)
from wind_pv_es_calc.eva_PV_optim_version.storage_optim_Wind_BESS_2 import (
    simulate_dispatch_offgrid_shiftable,
)


def main():
    # ==============================
    # 1. 加载数据
    # ==============================
    from wind_pv_es_calc.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("data/wind_pv_es_calc/temp/df_2025.csv")
    df_2025 = load_data(energy_data_path=energy_data_path, year_of_data=2025)
    df_2025["P_kw"] = df_2025["P_kw"] / 704234268 * 685436401

    from wind_pv_es_calc.eva_PV_optim_version.data_wind_simu import generate_wind_data
    wind_data_path = Path("data/wind_pv_es_calc/temp/df_wind_2025.csv")
    df_wind = generate_wind_data(
        farm_capacity_mw=110.0,
        mean_wind_speed_140m=5.5,
        eq_full_load_hours=1920.7,
        lat=28.42,
        lon=117.88,
        wind_data_path=wind_data_path,
    )

    # ==============================
    # 2. 统一数据对齐（用 align_and_merge 一条路径，确保两调度器输入完全一致）
    # ==============================
    df_2025["Time"] = pd.to_datetime(df_2025["Time"])
    df_wind["Time"] = pd.to_datetime(df_wind["Time"])
    df_load = df_2025[["Time", "P_kw"]]
    df_wind_input = df_wind[["Time", "WindPower_MW"]]

    df_merged, dt_h = align_and_merge(df_load, df_wind_input, "P_kw", "WindPower_MW")
    load_kw = df_merged["Load_kW"].to_numpy(dtype=float)
    wind_kw = df_merged["Wind_kW"].to_numpy(dtype=float)
    dt = dt_h

    print(f"数据长度: {len(load_kw)}, dt={dt:.4f}h")
    print(f"负荷总电量: {load_kw.sum() * dt / 1e6:.2f} MWh")
    print(f"风电总电量: {wind_kw.sum() * dt / 1e6:.2f} MWh")
    print(f"负荷范围: [{load_kw.min():.1f}, {load_kw.max():.1f}] kW")
    print(f"风电范围: [{wind_kw.min():.1f}, {wind_kw.max():.1f}] kW")

    # ==============================
    # 4. 统一参数
    # ==============================
    # BESS_1: PlanConfigFast, eta_roundtrip=0.92, c_rate=0.5
    cfg1 = PlanConfigFast(
        eta_roundtrip=0.92,
        c_rate=0.5,
        soc_init_frac=0.5,
        soc_min_frac=0.1,
        soc_max_frac=1.0,
        use_numba=True,
    )

    # BESS_2: BESSConfig, eta_charge=eta_discharge=0.92, c_rate=0.5 (对齐)
    bess2 = BESSConfig(
        eta_charge=0.92,
        eta_discharge=0.92,
        soc_init=0.5,
        soc_min=0.1,
        soc_max=1.0,
        c_rate=0.5,
        enforce_terminal_soc=False,
    )
    # 平移策略：开启
    policy_shift = ShiftPolicy(
        enable_shift=True,
        lookahead_steps=8,
        shift_max_frac_of_wind=0.30,
    )
    # 平移策略：关闭（纯贪心，与 BESS_1 可比）
    policy_no_shift = ShiftPolicy(
        enable_shift=False,
        lookahead_steps=0,
        shift_max_frac_of_wind=0.0,
    )

    # ==============================
    # 5. 遍历储能容量，对比两个调度器
    # ==============================
    capacities_mwh = [0, 10, 50, 100, 200, 500, 1000, 1500, 2000]
    results = []

    for cap_mwh in capacities_mwh:
        cap_kwh = cap_mwh * 1000.0

        # --- BESS_1: evaluate() ---
        r1 = evaluate(load_kw, wind_kw, dt, cap_kwh, cfg1)

        # --- BESS_2 (no shift): 纯贪心 ---
        r2_no_shift = simulate_dispatch_offgrid_shiftable(
            load_kw, wind_kw, dt, cap_mwh, bess2, policy_no_shift,
        )

        # --- BESS_2 (with shift): 平移充电 ---
        r2_shift = simulate_dispatch_offgrid_shiftable(
            load_kw, wind_kw, dt, cap_mwh, bess2, policy_shift,
        )

        results.append({
            "cap_mwh": cap_mwh,
            # BESS_1 指标
            "bess1_self_use": r1["self_use_ratio"],
            "bess1_coverage": r1["load_cover_ratio"],
            "bess1_used_mwh": r1["used_kwh"] / 1e3,
            "bess1_discharge_mwh": r1["bess_discharge_kwh"] / 1e3,
            # BESS_2 无平移
            "bess2_ns_self": r2_no_shift["metrics"]["green_self_consumption"],
            "bess2_ns_cov": r2_no_shift["metrics"]["load_coverage"],
            "bess2_ns_used_mwh": r2_no_shift["energy_kwh"]["served"] / 1e3,
            "bess2_ns_discharge_mwh": r2_no_shift["energy_kwh"]["discharge_out"] / 1e3,
            # BESS_2 有平移
            "bess2_s_self": r2_shift["metrics"]["green_self_consumption"],
            "bess2_s_cov": r2_shift["metrics"]["load_coverage"],
            "bess2_s_used_mwh": r2_shift["energy_kwh"]["served"] / 1e3,
            "bess2_s_discharge_mwh": r2_shift["energy_kwh"]["discharge_out"] / 1e3,
        })

    # ==============================
    # 6. 输出对比表
    # ==============================
    df_res = pd.DataFrame(results)

    print("\n" + "=" * 100)
    print("调度器对比结果 (统一参数: eta=0.92, c_rate=0.5, soc=[0.1, 1.0], soc_init=0.5)")
    print("=" * 100)

    print("\n--- 新能源自消纳率 (self_use_ratio / green_self_consumption) ---")
    print(f"{'容量(MWh)':>10} | {'BESS_1':>10} | {'BESS_2(无平移)':>14} | {'BESS_2(有平移)':>14} | {'差(无平移)':>10} | {'差(有平移)':>10}")
    print("-" * 80)
    for _, row in df_res.iterrows():
        d1 = row["bess2_ns_self"] - row["bess1_self_use"]
        d2 = row["bess2_s_self"] - row["bess1_self_use"]
        print(f"{row['cap_mwh']:>10.0f} | {row['bess1_self_use']:>10.4f} | {row['bess2_ns_self']:>14.4f} | {row['bess2_s_self']:>14.4f} | {d1:>+10.4f} | {d2:>+10.4f}")

    print("\n--- 负荷覆盖率 (load_cover_ratio / load_coverage) ---")
    print(f"{'容量(MWh)':>10} | {'BESS_1':>10} | {'BESS_2(无平移)':>14} | {'BESS_2(有平移)':>14} | {'差(无平移)':>10} | {'差(有平移)':>10}")
    print("-" * 80)
    for _, row in df_res.iterrows():
        d1 = row["bess2_ns_cov"] - row["bess1_coverage"]
        d2 = row["bess2_s_cov"] - row["bess1_coverage"]
        print(f"{row['cap_mwh']:>10.0f} | {row['bess1_coverage']:>10.4f} | {row['bess2_ns_cov']:>14.4f} | {row['bess2_s_cov']:>14.4f} | {d1:>+10.4f} | {d2:>+10.4f}")

    print("\n--- 供电量(MWh) ---")
    print(f"{'容量(MWh)':>10} | {'BESS_1':>10} | {'BESS_2(无平移)':>14} | {'BESS_2(有平移)':>14}")
    print("-" * 65)
    for _, row in df_res.iterrows():
        print(f"{row['cap_mwh']:>10.0f} | {row['bess1_used_mwh']:>10.1f} | {row['bess2_ns_used_mwh']:>14.1f} | {row['bess2_s_used_mwh']:>14.1f}")

    print("\n--- 放电量(MWh) ---")
    print(f"{'容量(MWh)':>10} | {'BESS_1':>10} | {'BESS_2(无平移)':>14} | {'BESS_2(有平移)':>14}")
    print("-" * 65)
    for _, row in df_res.iterrows():
        print(f"{row['cap_mwh']:>10.0f} | {row['bess1_discharge_mwh']:>10.1f} | {row['bess2_ns_discharge_mwh']:>14.1f} | {row['bess2_s_discharge_mwh']:>14.1f}")

    # ==============================
    # 7. 结论摘要
    # ==============================
    max_diff_self = (df_res["bess2_ns_self"] - df_res["bess1_self_use"]).abs().max()
    max_diff_cov = (df_res["bess2_ns_cov"] - df_res["bess1_coverage"]).abs().max()
    print(f"\n{'=' * 60}")
    print("结论:")
    print(f"  BESS_1 vs BESS_2(无平移) 最大自消纳率差异: {max_diff_self:.4f}")
    print(f"  BESS_1 vs BESS_2(无平移) 最大覆盖率差异:   {max_diff_cov:.4f}")
    shift_gain_self = (df_res["bess2_s_self"] - df_res["bess2_ns_self"]).max()
    shift_gain_cov = (df_res["bess2_s_cov"] - df_res["bess2_ns_cov"]).max()
    print(f"  平移充电带来的最大自消纳率提升: {shift_gain_self:.4f}")
    print(f"  平移充电带来的最大覆盖率提升:   {shift_gain_cov:.4f}")


if __name__ == "__main__":
    main()
