# -*- coding: utf-8 -*-
"""多节点储能容量扫描与经济评估模块。

对多个电价节点进行容量轮巡扫描，使用 PuLP MILP 求解最优调度，
叠加容量衰减模型计算 IRR 和全寿命经济指标。
从 ba_eva_optim_version/ba_duli.py 整合。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import Any

import numpy as np
import pandas as pd
from pulp import (
    LpMaximize,
    LpProblem,
    LpVariable,
    LpStatus,
    PULP_CBC_CMD,
    lpSum,
    value,
)


# ============================================================
# IRR 计算
# ============================================================
def compute_irr(
    cash_flows: list[float],
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """二分法计算内部收益率 (IRR)。

    Parameters
    ----------
    cash_flows : list[float]
        现金流序列，第 0 项为投资（负值），后续为每年净现金流。
    tol : float
        NPV 收敛容差。
    max_iter : int
        最大迭代次数。

    Returns
    -------
    float
        IRR（小数形式，0.2 = 20%）。无解时返回 0.0。
    """
    if len(cash_flows) < 2:
        return 0.0

    # 检查现金流方向：全部同号则无 IRR
    has_negative = any(cf < 0 for cf in cash_flows)
    has_positive = any(cf > 0 for cf in cash_flows)
    if not has_negative or not has_positive:
        return 0.0

    def npv(rate: float) -> float:
        return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cash_flows))

    # 动态扩展上界：直到 NPV 变号
    low, high = -0.99, 1.0
    npv_low = npv(low)
    npv_high = npv(high)

    for _ in range(20):
        if npv_low * npv_high <= 0:
            break
        high *= 10
        npv_high = npv(high)
    else:
        return 0.0

    for _ in range(max_iter):
        mid = (low + high) / 2
        npv_mid = npv(mid)
        if abs(npv_mid) < tol:
            return mid
        if npv_mid * npv_low < 0:
            high, npv_high = mid, npv_mid
        else:
            low, npv_low = mid, npv_mid

    return (low + high) / 2


# ============================================================
# 配置数据类
# ============================================================
@dataclass(slots=True)
class StorageSizingConfig:
    """多节点容量扫描配置。"""
    # 寿命
    life_years: int = 10
    life_cycles: int = 4000
    max_cycles_per_year: float | None = None
    max_daily_cycles: float = 1.0
    # 电池物理
    dod: float = 0.9
    eta_charge: float = 0.98
    eta_discharge: float = 0.98
    soc_init: float = 0.5
    soc_min: float = 0.1
    soc_max: float = 1.0
    # 经济
    capex_per_kwh: float = 1500.0
    opex_per_kwh_year: float = 30.0
    discount_rate: float = 0.08
    # 容量扫描范围
    cap_min_mwh: float = 50.0
    cap_max_mwh: float = 200.0
    cap_step_mwh: float = 10.0
    # 调度阈值
    discharge_price_threshold: float = 300.0
    allow_negative_price: bool = True
    # 衰减
    capacity_end_ratio: float = 0.7
    # 并网
    line_limit_mw: float = 400.0
    c_rate: float = 0.5
    grid_charge_fee: float = 14.5


# ============================================================
# 结果数据类
# ============================================================
@dataclass(slots=True)
class CapacitySweepRow:
    """单个 (节点, 容量) 的经济评估结果。"""
    capacity_mwh: float = 0.0
    power_mw: float = 0.0
    c_rate_effective: float = 0.0
    irr_percent: float = 0.0
    annual_revenue_wan: float = 0.0
    life_revenue_wan: float = 0.0
    life_net_wan: float = 0.0
    charge_mwh_year1: float = 0.0
    discharge_mwh_year1: float = 0.0
    utilization: float = 0.0
    daily_cycles_year1: float = 0.0


@dataclass(slots=True)
class NodeScanResult:
    """单节点容量扫描结果。"""
    node_name: str = ""
    sweep_df: pd.DataFrame | None = None
    best: CapacitySweepRow | None = None


@dataclass(slots=True)
class MultiNodeScanResult:
    """多节点扫描结果。"""
    nodes: dict[str, NodeScanResult] = field(default_factory=dict)
    summary_df: pd.DataFrame | None = None


# ============================================================
# 单节点 MILP 求解
# ============================================================
def _solve_single_capacity(
    prices: np.ndarray,
    dt: float,
    days: float,
    cap_mwh: float,
    cfg: StorageSizingConfig,
) -> dict[str, Any] | None:
    """对单一容量求解 PuLP MILP 套利调度。"""
    p_max = min(cfg.c_rate * cap_mwh, cfg.line_limit_mw)
    if p_max <= 1e-6:
        return None

    T = len(prices)
    discharge_allowed = (prices >= cfg.discharge_price_threshold).astype(float)

    model = LpProblem("storage_arbitrage", LpMaximize)

    ch = LpVariable.dicts("ch", range(T), lowBound=0, upBound=p_max)
    dis = LpVariable.dicts("dis", range(T), lowBound=0, upBound=p_max)
    soc = LpVariable.dicts(
        "soc", range(T),
        lowBound=cfg.soc_min * cap_mwh,
        upBound=cfg.soc_max * cap_mwh,
    )

    # 目标：∑(放电收益 - 充电成本)
    model += lpSum(
        (prices[t] * dis[t] * cfg.eta_discharge
         - (prices[t] + cfg.grid_charge_fee) * ch[t] / cfg.eta_charge) * dt
        for t in range(T)
    )

    # SOC 动力学
    for t in range(T):
        soc_prev = cfg.soc_init * cap_mwh if t == 0 else soc[t - 1]
        model += soc[t] == soc_prev + (
            ch[t] * cfg.eta_charge - dis[t] / cfg.eta_discharge
        ) * dt

    # 不允许放电的时段
    for t in range(T):
        if discharge_allowed[t] < 0.5:
            model += dis[t] <= 0.0

    # 不允许负价充电
    if not cfg.allow_negative_price:
        for t in range(T):
            if prices[t] < 0:
                model += ch[t] <= 0.0

    # 年循环约束
    total_discharge_energy = lpSum(dis[t] * dt for t in range(T))
    if cfg.max_cycles_per_year is not None:
        model += total_discharge_energy <= cfg.max_cycles_per_year * cap_mwh * (days / 365.0)

    # 日循环约束
    if cfg.max_daily_cycles is not None:
        model += total_discharge_energy <= cfg.max_daily_cycles * cap_mwh * days

    model.solve(PULP_CBC_CMD(msg=False))
    status = LpStatus[model.status]
    if status != "Optimal":
        return None

    ch_v = np.array([value(ch[t]) for t in range(T)])
    dis_v = np.array([value(dis[t]) for t in range(T)])

    # 首年满容量收益
    revenue_series = (
        prices * dis_v * cfg.eta_discharge
        - (prices + cfg.grid_charge_fee) * ch_v / cfg.eta_charge
    ) * dt
    annual_revenue_1 = float(revenue_series.sum()) * 365.0 / days

    return {
        "status": status,
        "annual_revenue_1": annual_revenue_1,
        "ch": ch_v,
        "dis": dis_v,
    }


# ============================================================
# 衰减经济评估
# ============================================================
def _evaluate_with_degradation(
    cap_mwh: float,
    sol: dict[str, Any],
    dt: float,
    days: float,
    cfg: StorageSizingConfig,
) -> CapacitySweepRow:
    """叠加衰减 + CAPEX/OPEX，计算 IRR 等经济指标。"""
    annual_revenue_1 = sol["annual_revenue_1"]
    ch_period = float(sol["ch"].sum()) * dt
    dis_period = float(sol["dis"].sum()) * dt
    factor_year = 365.0 / days

    charge_mwh_y1 = ch_period * factor_year
    discharge_mwh_y1 = dis_period * factor_year

    capex = cap_mwh * 1000.0 * cfg.capex_per_kwh
    opex_y1 = cap_mwh * 1000.0 * cfg.opex_per_kwh_year

    # 线性衰减
    Y = cfg.life_years
    if Y > 1:
        step = (1.0 - cfg.capacity_end_ratio) / (Y - 1)
    else:
        step = 0.0
    year_ratios = [max(cfg.capacity_end_ratio, 1.0 - step * y) for y in range(Y)]

    revenues = [annual_revenue_1 * r for r in year_ratios]
    opexes = [opex_y1 * r for r in year_ratios]
    cash_flows = [-capex] + [revenues[y] - opexes[y] for y in range(Y)]

    irr = compute_irr(cash_flows)
    life_revenue = sum(revenues)
    life_net = sum(revenues[y] - opexes[y] for y in range(Y))
    annual_net_1 = revenues[0] - opexes[0]

    life_charge = sum(charge_mwh_y1 * r for r in year_ratios)
    life_discharge = sum(discharge_mwh_y1 * r for r in year_ratios)

    utilization = (
        life_discharge / (cfg.life_cycles * cap_mwh)
        if cfg.life_cycles > 0 and cap_mwh > 0
        else 0.0
    )
    daily_cycles_y1 = discharge_mwh_y1 / cap_mwh / 365.0 if cap_mwh > 0 else 0.0

    p_max = min(cfg.c_rate * cap_mwh, cfg.line_limit_mw)
    c_rate_eff = p_max / cap_mwh if cap_mwh > 0 else 0.0

    wan = lambda x: round(x / 1e4, 2)

    return CapacitySweepRow(
        capacity_mwh=cap_mwh,
        power_mw=round(p_max, 2),
        c_rate_effective=round(c_rate_eff, 3),
        irr_percent=round(irr * 100, 2),
        annual_revenue_wan=wan(annual_net_1),
        life_revenue_wan=wan(life_revenue),
        life_net_wan=wan(life_net),
        charge_mwh_year1=round(charge_mwh_y1, 2),
        discharge_mwh_year1=round(discharge_mwh_y1, 2),
        utilization=round(utilization, 4),
        daily_cycles_year1=round(daily_cycles_y1, 4),
    )


# ============================================================
# 单节点扫描
# ============================================================
def scan_single_node(
    price_series: pd.Series,
    time_index: pd.DatetimeIndex,
    node_name: str = "node",
    cfg: StorageSizingConfig = StorageSizingConfig(),
) -> NodeScanResult:
    """对单个电价节点进行容量轮巡扫描。

    Parameters
    ----------
    price_series : Series
        电价序列，单位 元/MWh。
    time_index : DatetimeIndex
        对应时间索引。
    node_name : str
        节点名称。
    cfg : StorageSizingConfig
        扫描配置。

    Returns
    -------
    NodeScanResult
    """
    prices = price_series.values.astype(float)
    times = pd.DatetimeIndex(time_index)

    dt_hours = float((times[1] - times[0]).total_seconds() / 3600.0)
    total_hours = (times[-1] - times[0]).total_seconds() / 3600.0
    days = max(total_hours / 24.0, 1.0)

    caps = np.arange(cfg.cap_min_mwh, cfg.cap_max_mwh + 1e-9, cfg.cap_step_mwh)
    rows: list[CapacitySweepRow] = []

    for cap in caps:
        sol = _solve_single_capacity(prices, dt_hours, days, cap, cfg)
        if sol is None:
            continue
        rows.append(_evaluate_with_degradation(cap, sol, dt_hours, days, cfg))

    if not rows:
        return NodeScanResult(node_name=node_name)

    df_sweep = pd.DataFrame([asdict(r) for r in rows])
    df_sweep = df_sweep.sort_values("irr_percent", ascending=False).reset_index(drop=True)

    best_row = rows[df_sweep.index[0]] if len(rows) > 0 else None

    return NodeScanResult(
        node_name=node_name,
        sweep_df=df_sweep,
        best=best_row,
    )


# ============================================================
# 多节点扫描
# ============================================================
def scan_multiple_nodes(
    df: pd.DataFrame,
    time_col: str = "Time",
    price_cols: list[str] | None = None,
    cfg: StorageSizingConfig = StorageSizingConfig(),
) -> MultiNodeScanResult:
    """扫描多个电价节点并生成对比汇总。

    Parameters
    ----------
    df : DataFrame
        输入数据，至少包含 time_col + 若干电价列。
    time_col : str
        时间列名。
    price_cols : list[str] | None
        要分析的电价列名列表。为 None 时自动识别所有数值列。
    cfg : StorageSizingConfig
        扫描配置。

    Returns
    -------
    MultiNodeScanResult
    """
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    times = df[time_col]

    if price_cols is None:
        price_cols = [
            c for c in df.columns
            if c != time_col and np.issubdtype(df[c].dtype, np.number)
        ]

    nodes: dict[str, NodeScanResult] = {}
    summary_rows: list[dict] = []

    for col in price_cols:
        s = df[[time_col, col]].dropna()
        if s.empty:
            continue

        result = scan_single_node(
            price_series=s[col],
            time_index=pd.DatetimeIndex(s[time_col]),
            node_name=col,
            cfg=cfg,
        )
        nodes[col] = result

        if result.best is not None:
            summary_rows.append({"节点": col, **asdict(result.best)})

    summary_df = pd.DataFrame(summary_rows) if summary_rows else pd.DataFrame()

    return MultiNodeScanResult(nodes=nodes, summary_df=summary_df)
