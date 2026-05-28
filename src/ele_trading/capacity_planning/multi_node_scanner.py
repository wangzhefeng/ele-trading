# -*- coding: utf-8 -*-
"""多节点储能容量扫描与经济评估模块。

对多个电价节点进行容量轮巡扫描，使用 PuLP MILP 求解最优调度，
叠加容量衰减模型计算 IRR 和全寿命经济指标。
"""
from __future__ import annotations

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

from ..evaluation.metrics import compute_irr


@dataclass(slots=True)
class StorageSizingConfig:
    """多节点容量扫描配置。"""
    life_years: int = 10
    life_cycles: int = 4000
    max_cycles_per_year: float | None = None
    max_daily_cycles: float = 1.0
    dod: float = 0.9
    eta_charge: float = 0.98
    eta_discharge: float = 0.98
    soc_init: float = 0.5
    soc_min: float = 0.1
    soc_max: float = 1.0
    capex_per_kwh: float = 1500.0
    opex_per_kwh_year: float = 30.0
    discount_rate: float = 0.08
    cap_min_mwh: float = 50.0
    cap_max_mwh: float = 200.0
    cap_step_mwh: float = 10.0
    discharge_price_threshold: float = 300.0
    allow_negative_price: bool = True
    capacity_end_ratio: float = 0.7
    line_limit_mw: float = 400.0
    c_rate: float = 0.5
    grid_charge_fee: float = 14.5


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

    model += lpSum(
        (prices[t] * dis[t] * cfg.eta_discharge
         - (prices[t] + cfg.grid_charge_fee) * ch[t] / cfg.eta_charge) * dt
        for t in range(T)
    )

    for t in range(T):
        soc_prev = cfg.soc_init * cap_mwh if t == 0 else soc[t - 1]
        model += soc[t] == soc_prev + (
            ch[t] * cfg.eta_charge - dis[t] / cfg.eta_discharge
        ) * dt

    for t in range(T):
        if discharge_allowed[t] < 0.5:
            model += dis[t] <= 0.0

    if not cfg.allow_negative_price:
        for t in range(T):
            if prices[t] < 0:
                model += ch[t] <= 0.0

    total_discharge_energy = lpSum(dis[t] * dt for t in range(T))
    if cfg.max_cycles_per_year is not None:
        model += total_discharge_energy <= cfg.max_cycles_per_year * cap_mwh * (days / 365.0)

    if cfg.max_daily_cycles is not None:
        model += total_discharge_energy <= cfg.max_daily_cycles * cap_mwh * days

    model.solve(PULP_CBC_CMD(msg=False))
    status = LpStatus[model.status]
    if status != "Optimal":
        return None

    ch_v = np.array([value(ch[t]) for t in range(T)])
    dis_v = np.array([value(dis[t]) for t in range(T)])

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
        annual_revenue_wan=wan(revenues[0] - opexes[0]),
        life_revenue_wan=wan(life_revenue),
        life_net_wan=wan(life_net),
        charge_mwh_year1=round(charge_mwh_y1, 2),
        discharge_mwh_year1=round(discharge_mwh_y1, 2),
        utilization=round(utilization, 4),
        daily_cycles_year1=round(daily_cycles_y1, 4),
    )


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
