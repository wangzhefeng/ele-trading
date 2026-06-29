# -*- coding: utf-8 -*-
"""光储项目 IRR 扫描模块。

三段式收益模型(PV 自用 → 储能平移弃电 → 余电上网≤20%）, 
轮巡储能容量 × 购电电价, 计算光储整体 IRR。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from ..evaluation.metrics import compute_irr


@dataclass(slots=True)
class PVBESSIRRConfig:
    """光储 IRR 扫描配置。"""
    pv_capex_yuan: float = 2_300_000_000.0
    bess_capex_per_kwh: float = 800.0
    export_price_per_kwh: float = 0.285
    max_export_ratio: float = 0.20
    life_years: int = 20
    platform_fee_yuan_per_year: float = 9_000_000.0
    o_and_m_per_kwh: float = 0.04


@dataclass(slots=True)
class PVBESSIRRRow:
    """单个 (储能容量, 购电价) 组合的 IRR 结果。"""
    bess_mwh: float = 0.0
    buy_price_per_kwh: float = 0.0
    annual_revenue_yuan: float = 0.0
    annual_energy_mwh: float = 0.0
    annual_om_yuan: float = 0.0
    annual_cf_yuan: float = 0.0
    total_capex_yuan: float = 0.0
    irr_percent: float | None = None


@dataclass(slots=True)
class DeltaIRRRow:
    """相邻储能容量的 IRR 变化。"""
    buy_price_per_kwh: float = 0.0
    bess_from_mwh: float = 0.0
    bess_to_mwh: float = 0.0
    delta_irr_percent: float | None = None


@dataclass(slots=True)
class PVBESSIRRResult:
    """光储 IRR 扫描完整结果。"""
    scan_df: pd.DataFrame | None = None
    delta_df: pd.DataFrame | None = None
    best: PVBESSIRRRow | None = None


def simulate_annual_gain(
    bess_mwh: float,
    buy_price_per_kwh: float,
    df: pd.DataFrame,
    pv_col: str = "PV",
    load_col: str = "Load",
    curtail_col: str = "Curtail",
    cfg: PVBESSIRRConfig = PVBESSIRRConfig(),
) -> tuple[float, float]:
    """三段式收益模型(优先级: PV 自用 → 储能平移弃电 → 余电上网）。

    Parameters
    ----------
    bess_mwh : float
        储能容量 (MWh)。
    buy_price_per_kwh : float
        购电/自用电价 (元/kWh)。
    df : DataFrame
        月度或时段数据, 包含 PV、Load、Curtail 列, 单位 MWh。
    pv_col, load_col, curtail_col : str
        列名。
    cfg : PVBESSIRRConfig
        配置。

    Returns
    -------
    (annual_gain, annual_energy)
        annual_gain: 年度总收益(元）
        annual_energy: 年度总售出/使用电量(MWh, 用于 O&M 计算）
    """
    export_price = cfg.export_price_per_kwh
    max_export = cfg.max_export_ratio

    annual_gain = 0.0
    annual_energy = 0.0

    for _, row in df.iterrows():
        PV = row[pv_col]
        Load = row[load_col]
        Curtail = row[curtail_col]

        PV_self = min(PV, Load)
        Gain1 = PV_self * 1000 * buy_price_per_kwh
        load_after_PV = max(Load - PV_self, 0)

        bess_used = min(bess_mwh, Curtail, load_after_PV)
        Gain3 = bess_used * 1000 * buy_price_per_kwh

        PV_left = PV - PV_self - bess_used
        PV_export = min(max(PV_left, 0), PV * max_export)
        Gain2 = PV_export * 1000 * export_price

        annual_gain += Gain1 + Gain2 + Gain3
        annual_energy += PV_self + bess_used + PV_export

    return annual_gain, annual_energy


def _compute_single_irr(
    bess_mwh: float,
    buy_price_per_kwh: float,
    df: pd.DataFrame,
    pv_col: str,
    load_col: str,
    curtail_col: str,
    cfg: PVBESSIRRConfig,
) -> PVBESSIRRRow:
    """计算单个 (储能容量, 购电价) 的 IRR。"""
    annual_gain, annual_energy = simulate_annual_gain(
        bess_mwh, buy_price_per_kwh, df,
        pv_col=pv_col, load_col=load_col, curtail_col=curtail_col, cfg=cfg,
    )

    om = annual_energy * 1000 * cfg.o_and_m_per_kwh
    annual_cf = annual_gain - om - cfg.platform_fee_yuan_per_year

    bess_capex = bess_mwh * 1000 * cfg.bess_capex_per_kwh
    total_capex = cfg.pv_capex_yuan + bess_capex

    cashflows = [-total_capex] + [annual_cf] * cfg.life_years
    irr = compute_irr(cashflows)

    return PVBESSIRRRow(
        bess_mwh=bess_mwh,
        buy_price_per_kwh=buy_price_per_kwh,
        annual_revenue_yuan=round(annual_gain, 2),
        annual_energy_mwh=round(annual_energy, 2),
        annual_om_yuan=round(om, 2),
        annual_cf_yuan=round(annual_cf, 2),
        total_capex_yuan=round(total_capex, 2),
        irr_percent=round(irr * 100, 2) if irr > 0 else None,
    )


def scan_pv_bess_irr(
    df: pd.DataFrame,
    bess_range: np.ndarray | list,
    buy_price_range: np.ndarray | list,
    pv_col: str = "PV",
    load_col: str = "Load",
    curtail_col: str = "Curtail",
    cfg: PVBESSIRRConfig = PVBESSIRRConfig(),
) -> PVBESSIRRResult:
    """光储 IRR 扫描: 轮巡储能容量 × 购电电价。

    Parameters
    ----------
    df : DataFrame
        月度或时段数据, 包含 PV、Load、Curtail 列, 单位 MWh。
    bess_range : array-like
        储能容量扫描范围 (MWh)。
    buy_price_range : array-like
        购电电价扫描范围 (元/kWh)。
    pv_col, load_col, curtail_col : str
        列名。
    cfg : PVBESSIRRConfig
        配置。

    Returns
    -------
    PVBESSIRRResult
    """
    rows: list[PVBESSIRRRow] = []

    for bp in buy_price_range:
        for cap in bess_range:
            row = _compute_single_irr(cap, bp, df, pv_col, load_col, curtail_col, cfg)
            rows.append(row)

    if not rows:
        return PVBESSIRRResult()

    scan_df = pd.DataFrame([asdict(r) for r in rows])

    delta_rows: list[DeltaIRRRow] = []
    for bp in buy_price_range:
        sub = scan_df[scan_df["buy_price_per_kwh"] == bp].sort_values("bess_mwh")
        for i in range(len(sub) - 1):
            s1 = sub.iloc[i]
            s2 = sub.iloc[i + 1]
            irr1 = s1["irr_percent"]
            irr2 = s2["irr_percent"]
            delta = (irr2 - irr1) if irr1 is not None and irr2 is not None else None
            delta_rows.append(DeltaIRRRow(
                buy_price_per_kwh=bp,
                bess_from_mwh=s1["bess_mwh"],
                bess_to_mwh=s2["bess_mwh"],
                delta_irr_percent=round(delta, 4) if delta is not None else None,
            ))

    delta_df = pd.DataFrame([asdict(r) for r in delta_rows]) if delta_rows else None

    best_row = max(rows, key=lambda r: r.irr_percent or 0)

    return PVBESSIRRResult(
        scan_df=scan_df,
        delta_df=delta_df,
        best=best_row,
    )
