"""IRR 目标型 Wind+PV+BESS 容量规划。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ele_trading.evaluation.metrics import compute_irr
from ele_trading.utils.data_alignment import as_time_series, align_to_time
from ele_trading.utils.time_index import infer_dt_hours

from .wind_pv_bess_planner import WindPVBESSPlanConfig, _dispatch_annual


@dataclass(slots=True)
class WindPVBESSIRRPlanConfig:
    """IRR 目标型 Wind+PV+BESS 容量规划配置。"""

    target_owner_price_yuan_per_kwh: float = 0.32
    grid_buy_price_yuan_per_kwh: float = 0.36
    green_price_adder_yuan_per_kwh: float = 0.074
    target_irr: float = 0.08
    irr_tolerance: float = 0.002

    wind_max_mw: float = 280.0
    pv_max_mw: float = 140.0
    bess_max_mwh: float = 1000.0
    wind_step_mw: float = 10.0
    pv_step_mw: float = 10.0
    bess_step_mwh: float = 20.0

    self_use_ratio_min: float = 0.60
    load_cover_ratio_min: float = 0.35

    wind_capex_yuan_per_kw: float = 5000.0
    pv_capex_yuan_per_kwp: float = 3500.0
    bess_capex_yuan_per_kwh: float = 1500.0
    annual_opex_ratio: float = 0.02
    life_years: int = 15

    eta_roundtrip: float = 0.92
    c_rate: float = 0.5
    soc_init_frac: float = 0.5
    soc_min_frac: float = 0.1
    soc_max_frac: float = 1.0
    switch_gap_hours: float = 0.0
    use_numba: bool = True


@dataclass(slots=True)
class WindPVBESSIRRResult:
    """IRR 目标型 Wind+PV+BESS 容量规划结果。"""

    status: str
    wind_mw: float = 0.0
    pv_mw: float = 0.0
    bess_mwh: float = 0.0
    green_price: float = 0.0
    ppa_price: float = 0.0
    owner_avg_price: float = 0.0
    irr: float | None = None
    total_capex_yuan: float = 0.0
    annual_revenue_yuan: float = 0.0
    annual_opex_yuan: float = 0.0
    annual_cashflow_yuan: float = 0.0
    annual_green_used_kwh: float = 0.0
    annual_grid_buy_kwh: float = 0.0
    self_use_ratio: float = 0.0
    load_cover_ratio: float = 0.0
    curtail_kwh: float = 0.0
    diagnostics: pd.DataFrame | None = None
    message: str | None = None


def plan_wind_pv_bess_for_target_irr(
    df_load: pd.DataFrame,
    wind_unit_kw: pd.Series | pd.DataFrame,
    pv_unit_kw: pd.Series | pd.DataFrame,
    *,
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: WindPVBESSIRRPlanConfig = WindPVBESSIRRPlanConfig(),
) -> WindPVBESSIRRResult:
    """扫描风光储容量组合，寻找满足 IRR 目标的最低投资方案。"""
    aligned = _prepare_arrays(df_load, wind_unit_kw, pv_unit_kw, load_col, time_col)
    load_kw_arr, wind_unit_arr, pv_unit_arr, dt_hours = aligned

    dispatch_cfg = WindPVBESSPlanConfig(
        eta_roundtrip=cfg.eta_roundtrip,
        c_rate=cfg.c_rate,
        soc_init_frac=cfg.soc_init_frac,
        soc_min_frac=cfg.soc_min_frac,
        soc_max_frac=cfg.soc_max_frac,
        self_use_ratio_min=cfg.self_use_ratio_min,
        load_cover_ratio_min=cfg.load_cover_ratio_min,
        switch_gap_hours=cfg.switch_gap_hours,
        use_numba=cfg.use_numba,
    )
    switch_gap_steps = int(round(cfg.switch_gap_hours / dt_hours)) if cfg.switch_gap_hours > 0 else 0

    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for wind_mw in _scan_values(0.0, cfg.wind_max_mw, cfg.wind_step_mw):
        wind_kw_arr = wind_unit_arr * float(wind_mw)
        for pv_mw in _scan_values(0.0, cfg.pv_max_mw, cfg.pv_step_mw):
            pv_kw_arr = pv_unit_arr * float(pv_mw) * 1000.0
            for bess_mwh in _scan_values(0.0, cfg.bess_max_mwh, cfg.bess_step_mwh):
                st = _dispatch_annual(
                    load_kw_arr,
                    wind_kw_arr,
                    pv_kw_arr,
                    np.zeros_like(load_kw_arr, dtype=np.float64),
                    dt_hours,
                    float(bess_mwh) * 1000.0,
                    dispatch_cfg,
                    switch_gap_steps,
                )
                evaluated = _evaluate_candidate(float(wind_mw), float(pv_mw), float(bess_mwh), st, cfg)
                reason = evaluated.pop("reason")
                if reason == "ok":
                    candidates.append(evaluated)
                elif reason in {"non_positive_ppa", "irr_out_of_tolerance"}:
                    diagnostics.append({**evaluated, "reason": reason})

    if candidates:
        best = min(candidates, key=lambda row: (row["total_capex_yuan"], row["irr_gap"]))
        return _result_from_row("ok", best, pd.DataFrame(candidates))

    if diagnostics:
        diag_df = pd.DataFrame(diagnostics).sort_values("irr_gap", na_position="last").reset_index(drop=True)
        return WindPVBESSIRRResult(
            status="no_solution",
            diagnostics=diag_df,
            message="未找到满足 PPA/IRR 约束的风光储组合。",
        )

    return WindPVBESSIRRResult(
        status="no_solution",
        diagnostics=pd.DataFrame(),
        message="未找到满足物理消纳约束的风光储组合。",
    )


def _prepare_arrays(
    df_load: pd.DataFrame,
    wind_unit_kw: pd.Series | pd.DataFrame,
    pv_unit_kw: pd.Series | pd.DataFrame,
    load_col: str,
    time_col: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    df = df_load[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    load_kw_arr = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    load_kw_arr = np.ascontiguousarray(load_kw_arr, dtype=np.float64)

    wind_s = as_time_series(
        wind_unit_kw,
        time_col=time_col,
        value_cols=("wind_unit_kw", "WindPower_MW", "wind_mw", "wind_kw", "WindPower_kW", "WindPower", "value"),
        scale=1.0,
    )
    wind_unit_arr = align_to_time(df[time_col], wind_s)

    pv_s = as_time_series(
        pv_unit_kw,
        time_col=time_col,
        value_cols=("pv_unit_kw", "pv_kw", "u", "value"),
        scale=1.0,
    )
    pv_unit_arr = align_to_time(df[time_col], pv_s)

    return load_kw_arr, wind_unit_arr, pv_unit_arr, infer_dt_hours(df[time_col])


def _evaluate_candidate(
    wind_mw: float,
    pv_mw: float,
    bess_mwh: float,
    st: dict[str, float],
    cfg: WindPVBESSIRRPlanConfig,
) -> dict[str, Any]:
    gen = st["ren_gen_kwh"]
    used = st["ren_used_kwh"]
    load = st["load_kwh"]
    if gen <= 1e-9 or used <= 1e-9 or load <= 1e-9:
        return {"reason": "no_generation", "irr_gap": np.inf}

    self_use = used / gen
    cover = used / load
    if self_use < cfg.self_use_ratio_min or cover < cfg.load_cover_ratio_min:
        return {"reason": "physical_infeasible", "irr_gap": np.inf}

    grid_buy_kwh = max(load - used, 0.0)
    green_price = (
        cfg.target_owner_price_yuan_per_kwh * load
        - cfg.grid_buy_price_yuan_per_kwh * grid_buy_kwh
    ) / used
    ppa_price = green_price - cfg.green_price_adder_yuan_per_kwh
    owner_avg_price = (
        green_price * used + cfg.grid_buy_price_yuan_per_kwh * grid_buy_kwh
    ) / load

    row = {
        "wind_mw": wind_mw,
        "pv_mw": pv_mw,
        "bess_mwh": bess_mwh,
        "green_price": float(green_price),
        "ppa_price": float(ppa_price),
        "owner_avg_price": float(owner_avg_price),
        "annual_green_used_kwh": float(used),
        "annual_grid_buy_kwh": float(grid_buy_kwh),
        "self_use_ratio": float(self_use),
        "load_cover_ratio": float(cover),
        "curtail_kwh": float(st.get("curtail_kwh", 0.0)),
    }

    if green_price <= 0.0 or ppa_price <= 0.0:
        return {**row, "reason": "non_positive_ppa", "irr": np.nan, "irr_gap": np.inf}

    total_capex = (
        wind_mw * 1000.0 * cfg.wind_capex_yuan_per_kw
        + pv_mw * 1000.0 * cfg.pv_capex_yuan_per_kwp
        + bess_mwh * 1000.0 * cfg.bess_capex_yuan_per_kwh
    )
    annual_revenue = green_price * used
    annual_opex = total_capex * cfg.annual_opex_ratio
    annual_cf = annual_revenue - annual_opex
    irr = compute_irr([-total_capex] + [annual_cf] * int(cfg.life_years))
    irr_gap = abs(irr - cfg.target_irr)

    row.update({
        "total_capex_yuan": float(total_capex),
        "annual_revenue_yuan": float(annual_revenue),
        "annual_opex_yuan": float(annual_opex),
        "annual_cashflow_yuan": float(annual_cf),
        "irr": float(irr),
        "irr_gap": float(irr_gap),
    })
    if irr_gap > cfg.irr_tolerance:
        return {**row, "reason": "irr_out_of_tolerance"}
    return {**row, "reason": "ok"}


def _result_from_row(status: str, row: dict[str, Any], diagnostics: pd.DataFrame | None) -> WindPVBESSIRRResult:
    return WindPVBESSIRRResult(
        status=status,
        wind_mw=float(row["wind_mw"]),
        pv_mw=float(row["pv_mw"]),
        bess_mwh=float(row["bess_mwh"]),
        green_price=float(row["green_price"]),
        ppa_price=float(row["ppa_price"]),
        owner_avg_price=float(row["owner_avg_price"]),
        irr=float(row["irr"]),
        total_capex_yuan=float(row["total_capex_yuan"]),
        annual_revenue_yuan=float(row["annual_revenue_yuan"]),
        annual_opex_yuan=float(row["annual_opex_yuan"]),
        annual_cashflow_yuan=float(row["annual_cashflow_yuan"]),
        annual_green_used_kwh=float(row["annual_green_used_kwh"]),
        annual_grid_buy_kwh=float(row["annual_grid_buy_kwh"]),
        self_use_ratio=float(row["self_use_ratio"]),
        load_cover_ratio=float(row["load_cover_ratio"]),
        curtail_kwh=float(row["curtail_kwh"]),
        diagnostics=diagnostics,
    )


def _scan_values(lo: float, hi: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("scan step must be positive")
    values: list[float] = []
    v = lo
    while v <= hi + 1e-9:
        values.append(round(v, 9))
        v += step
    if not values or values[-1] < hi - 1e-9:
        values.append(float(hi))
    return values
