# -*- coding: utf-8 -*-

# ***************************************************
# * File        : ba_eva_1.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-11
# * Version     : 1.0.051115
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


"""
改进内容：
1. 区分 CAPEX/OPEX 成本并年化折现；
2. SOC 改为周期性约束（SOC=10% → 10%）；
3. 增加充放电切换间隔15min约束；
4. 增加充放电连续性约束（至少2个点）；
5. 保持15分钟分辨率；
"""

import numpy as np
import pandas as pd
from pyscipopt import Model, quicksum


def optimize_storage(
    price_curve,
    load_curve,
    transformer_rating,
    time_interval_hours=0.25,  # 改为15分钟粒度
    charge_efficiency=0.95,
    discharge_efficiency=0.95,
    min_depth_of_discharge=0.1,
    min_power_ratio=0.0,
    max_cycles_per_year=650,
    min_utilization=0.75,
    # ========== 新增 CAPEX/OPEX 参数 ==========
    capex_per_kwh=2500,       # 投资成本 元/kWh
    opex_per_cycle_kwh=0.1,   # 循环衰减成本 元/kWh
    battery_life_years=10,    # 寿命期 年
    discount_rate=0.08,       # 折现率
    c_rate=0.5,
):
    """
    优化储能系统的额定功率和容量
    """

    total_periods = len(price_curve)
    hours_per_year = 365 * 24
    total_hours = total_periods * time_interval_hours
    max_cycles = int(max_cycles_per_year * max(total_hours / hours_per_year, 1.0))

    model = Model("Storage Optimization V2")
    model.setParam("limits/time", 200)
    model.setParam("numerics/feastol", 1e-4)
    model.setParam("numerics/dualfeastol", 1e-4)

    P_ch, P_dis, E, u_ch, u_dis = {}, {}, {}, {}, {}

    for t in range(total_periods):
        P_ch[t] = model.addVar(lb=0, name=f"P_ch_{t}")
        P_dis[t] = model.addVar(lb=0, name=f"P_dis_{t}")
        E[t] = model.addVar(lb=0, name=f"E_{t}")
        u_ch[t] = model.addVar(vtype="B", name=f"u_ch_{t}")
        u_dis[t] = model.addVar(vtype="B", name=f"u_dis_{t}")

    Cap_rated = model.addVar(lb=0, name="Cap_rated")
    P_rated = Cap_rated * c_rate

    # ========== 约束 ==========
    for t in range(total_periods):
        model.addCons(P_ch[t] <= P_rated)
        model.addCons(P_dis[t] <= P_rated)
        model.addCons(u_ch[t] + u_dis[t] <= 1)

        if min_power_ratio > 0:
            model.addCons(P_ch[t] >= P_rated * min_power_ratio * u_ch[t])
            model.addCons(P_dis[t] >= P_rated * min_power_ratio * u_dis[t])
        else:
            model.addCons(P_ch[t] <= P_rated * u_ch[t])
            model.addCons(P_dis[t] <= P_rated * u_dis[t])

        model.addCons(P_dis[t] <= load_curve[t])
        model.addCons(load_curve[t] + P_ch[t] <= transformer_rating)

        # SOC周期性约束：SOC从10%到10%
        if t == 0:
            model.addCons(E[t] == Cap_rated * 0.1)
        else:
            model.addCons(
                E[t]
                == E[t - 1]
                + P_ch[t] * charge_efficiency * time_interval_hours
                - P_dis[t] * time_interval_hours / discharge_efficiency
            )

        model.addCons(E[t] <= Cap_rated)
        model.addCons(E[t] >= Cap_rated * min_depth_of_discharge)

    # 结束时SOC = 初始SOC = 10%
    model.addCons(E[total_periods - 1] == Cap_rated * 0.1)

    # 新增：充放电切换间隔约束（禁止15分钟内反向）
    for t in range(1, total_periods):
        model.addCons(u_ch[t] + u_dis[t - 1] <= 1)
        model.addCons(u_dis[t] + u_ch[t - 1] <= 1)

    # 新增：充放电至少连续两个点
    for t in range(total_periods - 2):
        model.addCons(u_ch[t + 1] >= u_ch[t] - u_ch[t + 2])
        model.addCons(u_dis[t + 1] >= u_dis[t] - u_dis[t + 2])

    # 总放电量约束
    total_discharge = quicksum(
        P_dis[t] * time_interval_hours for t in range(total_periods)
    )
    model.addCons(total_discharge >= max_cycles * Cap_rated * min_utilization)
    model.addCons(total_discharge <= max_cycles * Cap_rated)

    # ========== 目标函数 ==========
    weighted_prices = [
        price_curve[t] * time_interval_hours for t in range(total_periods)
    ]
    discharge_revenue = quicksum(
        weighted_prices[t] * P_dis[t] for t in range(total_periods)
    )
    charge_cost = quicksum(weighted_prices[t] * P_ch[t] for t in range(total_periods))

    # 年化投资成本 + 循环OPEX成本
    annualized_capex = (
        capex_per_kwh
        * Cap_rated
        * (discount_rate * (1 + discount_rate) ** battery_life_years)
        / ((1 + discount_rate) ** battery_life_years - 1)
    )
    storage_cost = (
        quicksum(P_dis[t] * time_interval_hours * opex_per_cycle_kwh for t in range(total_periods))
        + annualized_capex
    )

    model.setObjective(discharge_revenue - charge_cost - storage_cost, "maximize")

    # ========== 求解 ==========
    model.optimize()
    status = model.getStatus()
    print(f"求解状态: {status}")

    income = model.getObjVal()
    optimal_power = model.getVal(P_rated)
    optimal_capacity = model.getVal(Cap_rated)
    total_discharge_value = sum(
        model.getVal(P_dis[t]) * time_interval_hours for t in range(total_periods)
    )
    actual_utilization = (
        total_discharge_value / (max_cycles * optimal_capacity)
        if optimal_capacity > 0
        else 0
    )

    charge_schedule = [model.getVal(P_ch[t]) for t in range(total_periods)]
    discharge_schedule = [model.getVal(P_dis[t]) for t in range(total_periods)]
    energy_level = [model.getVal(E[t]) for t in range(total_periods)]

    print("\n-------------充放电统计------------------")
    charge_kwh = sum(charge_schedule) * time_interval_hours
    discharge_kwh = sum(discharge_schedule) * time_interval_hours
    print(f"总充电电量: {charge_kwh/1000:.2f} MWh")
    print(f"总放电电量: {discharge_kwh/1000:.2f} MWh")
    print(f"充放电效率: {discharge_kwh/charge_kwh*100:.2f}%" if charge_kwh > 0 else "无充电")

    return (
        income,
        optimal_power,
        optimal_capacity,
        actual_utilization,
        charge_schedule,
        discharge_schedule,
        energy_level,
        charge_kwh,
        discharge_kwh,
    )


# ========== 主程序入口（保持原逻辑） ==========
if __name__ == "__main__":
    price_df = pd.read_csv("merged_df.csv", parse_dates=["日期时间"])
    load_df = pd.read_excel("兴达_processed.xlsx", skiprows=96, names=["time", "load"], parse_dates=["time"])

    # 保持15分钟粒度，不重采样
    filter_mask = (
        (load_df["time"] >= "2025-02-19 00:00:00") & (load_df["time"] <= "2025-02-19 23:45:00")
    ) | (
        (load_df["time"] >= "2025-08-16 00:00:00") & (load_df["time"] <= "2025-08-16 23:45:00")
    )
    load_df = load_df[~filter_mask]
    load_df["period"] = list(range(96)) * (len(load_df) // 96)
    load_curve = load_df.groupby("period")["load"].mean()
    price_df["Load"] = np.array(load_curve.tolist() * (len(price_df) // 96))

    price_curve = price_df["电能价格"].values / 1000  # 元/kWh
    load_curve = price_df["Load"].values
    transformer_rating = int(load_curve.max() * 2.0)
    storage_cost_per_kwh = 0.65
    time_interval_hours = 0.25  # 保持15分钟

    for min_utilization in [0.5, 0.6, 0.7]:
        for c_rate in [0.25, 0.5, 1.0]:
            print("\n-------------限定条件------------------")
            print(f"变压器容量: {transformer_rating} KVA")
            print(f"最小利用率: {min_utilization*100:.1f}%")
            print(f"C倍率: {c_rate}C")

            (
                income,
                optimal_power,
                optimal_capacity,
                actual_utilization,
                charge_schedule,
                discharge_schedule,
                energy_level,
                charge_kwh,
                discharge_kwh,
            ) = optimize_storage(
                price_curve,
                load_curve,
                transformer_rating,
                time_interval_hours=time_interval_hours,
                charge_efficiency=0.95,
                discharge_efficiency=0.95,
                min_depth_of_discharge=0.1,
                min_power_ratio=0.1,
                max_cycles_per_year=650,
                min_utilization=min_utilization,
                capex_per_kwh=2500,
                opex_per_cycle_kwh=0.1,
                battery_life_years=10,
                discount_rate=0.08,
                c_rate=c_rate,
            )

            print("\n-------------最终结果------------------")
            print(f"最优储能额定功率: {optimal_power/1000:.2f} MW")
            print(f"最优储能额定容量: {optimal_capacity/1000:.2f} MWh")
            print(f"实际利用率: {actual_utilization:.2%}")
            print(f"充电电量: {charge_kwh/1000:.2f} MWh")
            print(f"放电电量: {discharge_kwh/1000:.2f} MWh")
            print(f"套利净收益（含CAPEX/OPEX年化）: {income/10000:.2f} 万元")





# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
