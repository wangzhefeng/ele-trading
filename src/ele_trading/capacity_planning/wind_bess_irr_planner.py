"""风储项目 IRR 扫描模块。

与 pv_bess_irr_planner.py 对称，但采用两段式收益模型：

风储与光储在收益结构上有本质差异：
  - 光储：PV 可能 > Load 产生余电上网收益，三段式（自用 → 平移弃电 → 上网）
  - 风储：Wind 通常 < Load（负荷远大于风电），无余电上网，退化为两段式
    （风电直供 → 储能平移弃风补缺）

两段式收益模型：
  1. 风电直供：    min(Wind, Load) × buy_price
  2. 储能平移弃风：min(BESS, Curtail, load_after_wind) × buy_price

轮巡储能容量 × 购电电价，计算风储整体 IRR。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..evaluation.metrics import compute_irr


@dataclass(slots=True)
class WindBESSIRRConfig:
    """风储 IRR 扫描配置。"""
    wind_capex_yuan: float = 5_000_000_000.0       # 风电总投资（元），按项目规模设定
    bess_capex_per_kwh: float = 800.0              # 储能单位投资（元/kWh）
    export_price_per_kwh: float = 0.285            # 上网电价（元/kWh），仅当有上网电量时生效
    max_export_ratio: float = 0.20                 # 最大上网比例（占风电发电量）
    life_years: int = 20                           # 项目寿命（年）
    platform_fee_yuan_per_year: float = 9_000_000.0  # 平台费/管理费（元/年）
    o_and_m_per_kwh: float = 0.04                  # 运维成本（元/kWh）


@dataclass(slots=True)
class WindBESSIRRRow:
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
class WindBESSIRRResult:
    """风储 IRR 扫描完整结果。"""
    scan_df: pd.DataFrame | None = None
    delta_df: pd.DataFrame | None = None
    best: WindBESSIRRRow | None = None


def simulate_annual_gain(
    bess_mwh: float,
    buy_price_per_kwh: float,
    df: pd.DataFrame,
    wind_col: str = "Wind",
    load_col: str = "Load",
    curtail_col: str = "Curtail",
    cfg: WindBESSIRRConfig = WindBESSIRRConfig(),
) -> tuple[float, float]:
    """两段式收益模型（风电直供 → 储能平移弃风，含可选余电上网）。

    Parameters
    ----------
    bess_mwh : float
        储能容量 (MWh)。
    buy_price_per_kwh : float
        购电/自用电价 (元/kWh)。
    df : DataFrame
        月度或时段数据，包含 Wind、Load、Curtail 列，单位 MWh。
    wind_col, load_col, curtail_col : str
        列名。
    cfg : WindBESSIRRConfig
        配置。

    Returns
    -------
    (annual_gain, annual_energy)
        annual_gain: 年度总收益（元）
        annual_energy: 年度总售出/使用电量（MWh，用于 O&M 计算）
    """
    export_price = cfg.export_price_per_kwh
    max_export = cfg.max_export_ratio

    annual_gain = 0.0
    annual_energy = 0.0

    for _, row in df.iterrows():
        Wind = row[wind_col]
        Load = row[load_col]
        Curtail = row[curtail_col]

        # ── 第一段：风电直供负荷 ──
        Wind_direct = min(Wind, Load)
        Gain1 = Wind_direct * 1000 * buy_price_per_kwh
        load_after_wind = max(Load - Wind_direct, 0.0)

        # ── 第二段：储能平移弃风补缺 ──
        # BESS 取弃风电量中能被负荷缺口消纳的部分
        bess_used = min(bess_mwh, Curtail, load_after_wind)
        Gain2 = bess_used * 1000 * buy_price_per_kwh

        # ── 可选第三段：余电上网（风储场景较少见，保留与光储对称的出口） ──
        # 仅当风电 + 储能后仍有剩余且配置允许上网时生效
        Wind_left = Wind - Wind_direct - bess_used
        Wind_export = min(max(Wind_left, 0.0), Wind * max_export)
        Gain3 = Wind_export * 1000 * export_price

        annual_gain += Gain1 + Gain2 + Gain3
        annual_energy += Wind_direct + bess_used + Wind_export

    return annual_gain, annual_energy


def _compute_single_irr(
    bess_mwh: float,
    buy_price_per_kwh: float,
    df: pd.DataFrame,
    wind_col: str,
    load_col: str,
    curtail_col: str,
    cfg: WindBESSIRRConfig,
) -> WindBESSIRRRow:
    """计算单个 (储能容量, 购电价) 的 IRR。"""
    annual_gain, annual_energy = simulate_annual_gain(
        bess_mwh, buy_price_per_kwh, df,
        wind_col=wind_col, load_col=load_col, curtail_col=curtail_col, cfg=cfg,
    )

    om = annual_energy * 1000 * cfg.o_and_m_per_kwh
    annual_cf = annual_gain - om - cfg.platform_fee_yuan_per_year

    bess_capex = bess_mwh * 1000 * cfg.bess_capex_per_kwh
    total_capex = cfg.wind_capex_yuan + bess_capex

    cashflows = [-total_capex] + [annual_cf] * cfg.life_years
    irr = compute_irr(cashflows)

    return WindBESSIRRRow(
        bess_mwh=bess_mwh,
        buy_price_per_kwh=buy_price_per_kwh,
        annual_revenue_yuan=round(annual_gain, 2),
        annual_energy_mwh=round(annual_energy, 2),
        annual_om_yuan=round(om, 2),
        annual_cf_yuan=round(annual_cf, 2),
        total_capex_yuan=round(total_capex, 2),
        irr_percent=round(irr * 100, 2) if irr > 0 else None,
    )


def scan_wind_bess_irr(
    df: pd.DataFrame,
    bess_range: np.ndarray | list,
    buy_price_range: np.ndarray | list,
    wind_col: str = "Wind",
    load_col: str = "Load",
    curtail_col: str = "Curtail",
    cfg: WindBESSIRRConfig = WindBESSIRRConfig(),
) -> WindBESSIRRResult:
    """风储 IRR 扫描：轮巡储能容量 × 购电电价。

    Parameters
    ----------
    df : DataFrame
        月度或时段数据，包含 Wind、Load、Curtail 列，单位 MWh。
    bess_range : array-like
        储能容量扫描范围 (MWh)。
    buy_price_range : array-like
        购电电价扫描范围 (元/kWh)。
    wind_col, load_col, curtail_col : str
        列名。
    cfg : WindBESSIRRConfig
        配置。

    Returns
    -------
    WindBESSIRRResult
    """
    rows: list[WindBESSIRRRow] = []

    for bp in buy_price_range:
        for cap in bess_range:
            row = _compute_single_irr(cap, bp, df, wind_col, load_col, curtail_col, cfg)
            rows.append(row)

    if not rows:
        return WindBESSIRRResult()

    scan_df = pd.DataFrame([asdict(r) for r in rows])

    # 边际 IRR 变化：同一购电价下，相邻储能容量的 IRR 差
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

    return WindBESSIRRResult(
        scan_df=scan_df,
        delta_df=delta_df,
        best=best_row,
    )
