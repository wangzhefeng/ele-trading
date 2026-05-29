# -*- coding: utf-8 -*-
"""MILP 储能容量+调度联合优化模块。

基于 PuLP/CBC 求解器，同时优化储能额定功率、额定容量和充放电策略。
从 ba_eva_optim_version/ba_eva_1.py (SCIP) 迁移并整合为 PuLP 实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from pulp import (
    LpBinary,
    LpMaximize,
    LpProblem,
    LpVariable,
    LpStatus,
    PULP_CBC_CMD,
    lpSum,
    value,
)


# ============================================================
# 配置数据类
# ============================================================
@dataclass(slots=True)
class MILPCapacitySizerConfig:
    """MILP 储能容量优化配置。"""
    # 时间
    time_interval_hours: float = 0.25
    # 电池物理
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    min_depth_of_discharge: float = 0.1
    c_rate: float = 0.5
    # 循环约束
    max_cycles_per_year: int = 650
    min_utilization: float = 0.75
    min_power_ratio: float = 0.0
    # 经济参数（CAPEX/OPEX 年化折现）
    capex_per_kwh: float = 2500.0
    opex_per_cycle_kwh: float = 0.1
    battery_life_years: int = 10
    discount_rate: float = 0.08
    # 运行约束（V2 特性）
    periodic_soc: bool = True
    periodic_soc_frac: float = 0.1
    switch_gap_periods: int = 1
    min_continuity_periods: int = 2
    # 求解器
    solver_time_limit: int = 200
    # 容量上限（PuLP 需要 Big-M，设为负荷峰值的合理倍数）
    capacity_upper_bound: float = 0.0


@dataclass(slots=True)
class MILPCapacitySizerResult:
    """MILP 储能容量优化结果。"""
    feasible: bool
    solver_status: str = ""
    # 最优容量
    optimal_power_kw: float = 0.0
    optimal_capacity_kwh: float = 0.0
    # 经济指标
    net_objective_yuan: float = 0.0
    annualized_capex_yuan: float = 0.0
    # 调度计划
    charge_schedule: list[float] = field(default_factory=list)
    discharge_schedule: list[float] = field(default_factory=list)
    soc_schedule: list[float] = field(default_factory=list)
    # 统计
    charge_kwh: float = 0.0
    discharge_kwh: float = 0.0
    roundtrip_efficiency: float = 0.0
    actual_utilization: float = 0.0


# ============================================================
# 主求解函数
# ============================================================
def solve_milp_capacity(
    price_curve: np.ndarray | list,
    load_curve: np.ndarray | list,
    transformer_rating: float,
    cfg: MILPCapacitySizerConfig = MILPCapacitySizerConfig(),
) -> MILPCapacitySizerResult:
    """MILP 联合优化储能容量与调度策略。

    给定电价曲线、负荷曲线和变压器容量，同时优化储能额定功率、
    额定容量和充放电计划，最大化净套利收益（扣除年化 CAPEX 和循环 OPEX）。

    Parameters
    ----------
    price_curve : array-like
        电价曲线，单位 元/kWh。
    load_curve : array-like
        负荷曲线，单位 kW（与 transformer_rating 一致）。
    transformer_rating : float
        变压器额定容量，单位 kW 或 kVA。
    cfg : MILPCapacitySizerConfig
        优化配置。

    Returns
    -------
    MILPCapacitySizerResult
    """
    price_curve = np.asarray(price_curve, dtype=float)
    load_curve = np.asarray(load_curve, dtype=float)
    T = len(price_curve)
    dt = cfg.time_interval_hours

    # 最大循环次数（按数据时长折算）
    hours_per_year = 365 * 24
    total_hours = T * dt
    max_cycles = int(cfg.max_cycles_per_year * max(total_hours / hours_per_year, 1.0))

    # 容量上界（Big-M）
    if cfg.capacity_upper_bound > 0:
        Cap_max = cfg.capacity_upper_bound
    else:
        Cap_max = float(load_curve.max()) * 24.0
    P_max = cfg.c_rate * Cap_max

    # ---------- 建模 ----------
    model = LpProblem("Storage_Capacity_Sizing", LpMaximize)

    # 决策变量
    P_ch = LpVariable.dicts("P_ch", range(T), lowBound=0)
    P_dis = LpVariable.dicts("P_dis", range(T), lowBound=0)
    E = LpVariable.dicts("E", range(T), lowBound=0)
    u_ch = LpVariable.dicts("u_ch", range(T), cat=LpBinary)
    u_dis = LpVariable.dicts("u_dis", range(T), cat=LpBinary)
    Cap_rated = LpVariable("Cap_rated", lowBound=0, upBound=Cap_max)

    # McCormick 辅助变量：P_ch_en[t] ≈ c_rate * Cap_rated * u_ch[t]
    # 仅在 min_power_ratio > 0 时需要
    P_ch_en = {}
    P_dis_en = {}
    if cfg.min_power_ratio > 0:
        for t in range(T):
            P_ch_en[t] = LpVariable(f"P_ch_en_{t}", lowBound=0, upBound=P_max)
            P_dis_en[t] = LpVariable(f"P_dis_en_{t}", lowBound=0, upBound=P_max)

    # ---------- 约束 ----------
    for t in range(T):
        # 功率上限
        model += P_ch[t] <= cfg.c_rate * Cap_rated
        model += P_dis[t] <= cfg.c_rate * Cap_rated

        # 充放电互斥
        model += u_ch[t] + u_dis[t] <= 1

        if cfg.min_power_ratio > 0:
            # McCormick 包络线松弛
            mpr = cfg.min_power_ratio
            cr = cfg.c_rate

            # P_ch_en[t] ≈ cr * Cap_rated * u_ch[t]
            model += P_ch_en[t] <= cr * Cap_rated
            model += P_ch_en[t] <= P_max * u_ch[t]
            model += P_ch_en[t] >= cr * Cap_rated - P_max * (1 - u_ch[t])
            model += P_ch_en[t] >= 0

            model += P_dis_en[t] <= cr * Cap_rated
            model += P_dis_en[t] <= P_max * u_dis[t]
            model += P_dis_en[t] >= cr * Cap_rated - P_max * (1 - u_dis[t])
            model += P_dis_en[t] >= 0

            model += P_ch[t] >= mpr * P_ch_en[t]
            model += P_dis[t] >= mpr * P_dis_en[t]
        # min_power_ratio == 0 时无需额外约束：
        # P_ch[t] >= 0 和 P_ch[t] <= c_rate * Cap_rated 已足够

        # 放电不超过负荷
        model += P_dis[t] <= load_curve[t]

        # 变压器容量约束
        model += load_curve[t] + P_ch[t] <= transformer_rating

        # SOC 动力学
        if t == 0:
            model += E[t] == cfg.periodic_soc_frac * Cap_rated
        else:
            model += (
                E[t]
                == E[t - 1]
                + P_ch[t] * cfg.charge_efficiency * dt
                - P_dis[t] * dt / cfg.discharge_efficiency
            )

        # SOC 上下限
        model += E[t] <= Cap_rated
        model += E[t] >= Cap_rated * cfg.min_depth_of_discharge

    # 周期性 SOC 约束
    if cfg.periodic_soc:
        model += E[T - 1] == cfg.periodic_soc_frac * Cap_rated

    # 充放电切换间隔约束
    gap = cfg.switch_gap_periods
    for t in range(gap, T):
        model += u_ch[t] + u_dis[t - gap] <= 1
        model += u_dis[t] + u_ch[t - gap] <= 1

    # 充放电连续性约束
    cont = cfg.min_continuity_periods
    if cont >= 2:
        for t in range(T - cont):
            for k in range(1, cont):
                model += u_ch[t + k] >= u_ch[t] - u_ch[t + cont]
                model += u_dis[t + k] >= u_dis[t] - u_dis[t + cont]

    # 总放电量约束
    total_discharge = lpSum(P_dis[t] * dt for t in range(T))
    model += total_discharge >= max_cycles * Cap_rated * cfg.min_utilization
    model += total_discharge <= max_cycles * Cap_rated

    # ---------- 目标函数 ----------
    weighted_prices = [price_curve[t] * dt for t in range(T)]
    discharge_revenue = lpSum(weighted_prices[t] * P_dis[t] for t in range(T))
    charge_cost = lpSum(weighted_prices[t] * P_ch[t] for t in range(T))

    # 年化 CAPEX（资本回收因子）
    r = cfg.discount_rate
    n = cfg.battery_life_years
    crf = r * (1 + r) ** n / ((1 + r) ** n - 1) if n > 0 else 1.0
    annualized_capex = cfg.capex_per_kwh * Cap_rated * crf

    # 循环 OPEX
    opex = lpSum(P_dis[t] * dt * cfg.opex_per_cycle_kwh for t in range(T))

    model += discharge_revenue - charge_cost - annualized_capex - opex

    # ---------- 求解 ----------
    model.solve(PULP_CBC_CMD(msg=False, timeLimit=cfg.solver_time_limit))
    status = LpStatus[model.status]

    if status != "Optimal":
        return MILPCapacitySizerResult(feasible=False, solver_status=status)

    # ---------- 提取结果 ----------
    optimal_capacity = value(Cap_rated)
    optimal_power = optimal_capacity * cfg.c_rate

    charge_schedule = [value(P_ch[t]) for t in range(T)]
    discharge_schedule = [value(P_dis[t]) for t in range(T)]
    soc_schedule = [value(E[t]) for t in range(T)]

    charge_kwh = sum(charge_schedule) * dt
    discharge_kwh = sum(discharge_schedule) * dt
    rt_eff = discharge_kwh / charge_kwh if charge_kwh > 0 else 0.0

    actual_utilization = (
        discharge_kwh / (max_cycles * optimal_capacity)
        if optimal_capacity > 0
        else 0.0
    )

    return MILPCapacitySizerResult(
        feasible=True,
        solver_status=status,
        optimal_power_kw=optimal_power,
        optimal_capacity_kwh=optimal_capacity,
        net_objective_yuan=value(model.objective),
        annualized_capex_yuan=cfg.capex_per_kwh * optimal_capacity * crf,
        charge_schedule=charge_schedule,
        discharge_schedule=discharge_schedule,
        soc_schedule=soc_schedule,
        charge_kwh=charge_kwh,
        discharge_kwh=discharge_kwh,
        roundtrip_efficiency=rt_eff,
        actual_utilization=actual_utilization,
    )
