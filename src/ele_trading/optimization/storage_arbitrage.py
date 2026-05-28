from __future__ import annotations

import numpy as np
from pulp import LpBinary, LpMaximize, LpProblem, LpVariable, PULP_CBC_CMD, lpSum, value

from .interfaces import CapacitySizingResult, StorageArbitrageResult


def solve_storage_arbitrage(
    prices,
    soc0=5.0,
    soc_min=1.0,
    soc_max=10.0,
    p_ch_max=3.0,
    p_dis_max=3.0,
    eta_ch=0.95,
    eta_dis=0.95,
    deg_cost=0.01,
    dt=1.0,
    enforce_terminal_soc=False,
):
    """求解单市场储能套利问题。

    这是文档 23.5 的工程化包装版本：
    - 决策变量包括充电、放电、SOC 与互斥状态。
    - 目标是最大化电价套利收益减去退化成本。
    """
    T = range(len(prices))
    m = LpProblem('storage_arbitrage', LpMaximize)

    p_ch = {t: LpVariable(f'p_ch_{t}', lowBound=0, upBound=p_ch_max) for t in T}
    p_dis = {t: LpVariable(f'p_dis_{t}', lowBound=0, upBound=p_dis_max) for t in T}
    soc = {t: LpVariable(f'soc_{t}', lowBound=soc_min, upBound=soc_max) for t in T}
    u_ch = {t: LpVariable(f'u_ch_{t}', cat=LpBinary) for t in T}
    u_dis = {t: LpVariable(f'u_dis_{t}', cat=LpBinary) for t in T}

    for t in T:
        # 互斥约束：同一时刻不能同时充电和放电。
        m += u_ch[t] + u_dis[t] <= 1
        m += p_ch[t] <= p_ch_max * u_ch[t]
        m += p_dis[t] <= p_dis_max * u_dis[t]

        # SOC 递推体现储能跨时段耦合，是储能优化的核心动态约束。
        if t == 0:
            m += soc[t] == soc0 + eta_ch * p_ch[t] * dt - (p_dis[t] * dt) / eta_dis
        else:
            m += soc[t] == soc[t - 1] + eta_ch * p_ch[t] * dt - (p_dis[t] * dt) / eta_dis

    if enforce_terminal_soc:
        # 若要求日终回到初始 SOC，可避免“透支未来”带来的不公平套利。
        m += soc[len(prices) - 1] == soc0

    m += lpSum(
        prices[t] * (p_dis[t] - p_ch[t]) * dt - deg_cost * (p_ch[t] + p_dis[t]) * dt
        for t in T
    )

    m.solve(PULP_CBC_CMD(msg=False))

    return {
        'objective': value(m.objective),
        'p_ch': [value(p_ch[t]) for t in T],
        'p_dis': [value(p_dis[t]) for t in T],
        'soc': [value(soc[t]) for t in T],
    }


def solve_storage_arbitrage_typed(**kwargs) -> StorageArbitrageResult:
    result = solve_storage_arbitrage(**kwargs)
    return StorageArbitrageResult(**result)


def solve_capacity_sizing(
    price_curve,
    load_curve,
    transformer_rating,
    time_interval_hours=0.25,
    charge_efficiency=0.95,
    discharge_efficiency=0.95,
    min_depth_of_discharge=0.1,
    c_rate=0.5,
    max_cycles_per_year=650,
    min_utilization=0.75,
    min_power_ratio=0.0,
    capex_per_kwh=2500.0,
    opex_per_cycle_kwh=0.1,
    battery_life_years=10,
    discount_rate=0.08,
    periodic_soc=True,
    periodic_soc_frac=0.1,
    switch_gap_periods=1,
    min_continuity_periods=2,
    solver_time_limit=200,
    capacity_upper_bound=0.0,
) -> CapacitySizingResult:
    """MILP 联合优化储能容量与调度策略。

    在 solve_storage_arbitrage 基础上，将额定容量 Cap_rated 也作为决策变量，
    同时优化容量、功率和充放电计划，最大化净套利收益（扣除年化 CAPEX 和循环 OPEX）。

    Parameters
    ----------
    price_curve : array-like
        电价曲线，单位 元/kWh。
    load_curve : array-like
        负荷曲线，单位 kW。
    transformer_rating : float
        变压器额定容量，单位 kW 或 kVA。
    time_interval_hours : float
        时间步长（小时），默认 0.25（15 分钟）。
    charge_efficiency / discharge_efficiency : float
        充放电效率。
    min_depth_of_discharge : float
        最小放电深度（SOC 下限比例）。
    c_rate : float
        倍率（功率/容量比）。
    max_cycles_per_year : int
        年最大循环次数。
    min_utilization : float
        最小利用率约束。
    min_power_ratio : float
        最小功率比例（>0 时启用 McCormick 包络松弛）。
    capex_per_kwh : float
        单位容量投资成本（元/kWh）。
    opex_per_cycle_kwh : float
        单次循环运维成本（元/kWh）。
    battery_life_years : int
        电池寿命（年）。
    discount_rate : float
        折现率。
    periodic_soc : bool
        是否要求周期性 SOC 回归。
    periodic_soc_frac : float
        周期性 SOC 目标比例。
    switch_gap_periods : int
        充放电切换最小间隔（时段数）。
    min_continuity_periods : int
        最小连续充放电时段数。
    solver_time_limit : int
        求解器时间限制（秒）。
    capacity_upper_bound : float
        容量上界（0 = 自动设为负荷峰值的 24 倍）。

    Returns
    -------
    CapacitySizingResult
    """
    price_curve = np.asarray(price_curve, dtype=float)
    load_curve = np.asarray(load_curve, dtype=float)
    T = len(price_curve)
    dt = time_interval_hours

    hours_per_year = 365 * 24
    total_hours = T * dt
    max_cycles = int(max_cycles_per_year * max(total_hours / hours_per_year, 1.0))

    if capacity_upper_bound > 0:
        Cap_max = capacity_upper_bound
    else:
        Cap_max = float(load_curve.max()) * 24.0
    P_max = c_rate * Cap_max

    model = LpProblem("Storage_Capacity_Sizing", LpMaximize)

    P_ch = LpVariable.dicts("P_ch", range(T), lowBound=0)
    P_dis = LpVariable.dicts("P_dis", range(T), lowBound=0)
    E = LpVariable.dicts("E", range(T), lowBound=0)
    u_ch = LpVariable.dicts("u_ch", range(T), cat=LpBinary)
    u_dis = LpVariable.dicts("u_dis", range(T), cat=LpBinary)
    Cap_rated = LpVariable("Cap_rated", lowBound=0, upBound=Cap_max)

    P_ch_en = {}
    P_dis_en = {}
    if min_power_ratio > 0:
        for t in range(T):
            P_ch_en[t] = LpVariable(f"P_ch_en_{t}", lowBound=0, upBound=P_max)
            P_dis_en[t] = LpVariable(f"P_dis_en_{t}", lowBound=0, upBound=P_max)

    for t in range(T):
        model += P_ch[t] <= c_rate * Cap_rated
        model += P_dis[t] <= c_rate * Cap_rated
        model += u_ch[t] + u_dis[t] <= 1

        if min_power_ratio > 0:
            mpr = min_power_ratio
            cr = c_rate
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

        model += P_dis[t] <= load_curve[t]
        model += load_curve[t] + P_ch[t] <= transformer_rating

        if t == 0:
            model += E[t] == periodic_soc_frac * Cap_rated
        else:
            model += (
                E[t]
                == E[t - 1]
                + P_ch[t] * charge_efficiency * dt
                - P_dis[t] * dt / discharge_efficiency
            )

        model += E[t] <= Cap_rated
        model += E[t] >= Cap_rated * min_depth_of_discharge

    if periodic_soc:
        model += E[T - 1] == periodic_soc_frac * Cap_rated

    gap = switch_gap_periods
    for t in range(gap, T):
        model += u_ch[t] + u_dis[t - gap] <= 1
        model += u_dis[t] + u_ch[t - gap] <= 1

    cont = min_continuity_periods
    if cont >= 2:
        for t in range(T - cont):
            for k in range(1, cont):
                model += u_ch[t + k] >= u_ch[t] - u_ch[t + cont]
                model += u_dis[t + k] >= u_dis[t] - u_dis[t + cont]

    total_discharge = lpSum(P_dis[t] * dt for t in range(T))
    model += total_discharge >= max_cycles * Cap_rated * min_utilization
    model += total_discharge <= max_cycles * Cap_rated

    weighted_prices = [price_curve[t] * dt for t in range(T)]
    discharge_revenue = lpSum(weighted_prices[t] * P_dis[t] for t in range(T))
    charge_cost = lpSum(weighted_prices[t] * P_ch[t] for t in range(T))

    r = discount_rate
    n = battery_life_years
    crf = r * (1 + r) ** n / ((1 + r) ** n - 1) if n > 0 else 1.0
    annualized_capex = capex_per_kwh * Cap_rated * crf

    opex = lpSum(P_dis[t] * dt * opex_per_cycle_kwh for t in range(T))

    model += discharge_revenue - charge_cost - annualized_capex - opex

    model.solve(PULP_CBC_CMD(msg=False, timeLimit=solver_time_limit))
    status = str(model.status)

    if status != "Optimal":
        return CapacitySizingResult(feasible=False, solver_status=status)

    optimal_capacity = value(Cap_rated)
    optimal_power = optimal_capacity * c_rate

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

    return CapacitySizingResult(
        feasible=True,
        solver_status=status,
        optimal_power_kw=optimal_power,
        optimal_capacity_kwh=optimal_capacity,
        net_objective_yuan=value(model.objective),
        annualized_capex_yuan=capex_per_kwh * optimal_capacity * crf,
        charge_schedule=charge_schedule,
        discharge_schedule=discharge_schedule,
        soc_schedule=soc_schedule,
        charge_kwh=charge_kwh,
        discharge_kwh=discharge_kwh,
        roundtrip_efficiency=rt_eff,
        actual_utilization=actual_utilization,
    )
