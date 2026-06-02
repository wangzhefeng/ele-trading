"""Wind+PV+BESS 容量规划模块

PV 粗扫 + 细扫两阶段搜索 + BESS 二分搜索，Numba JIT 加速调度引擎。
支持能量门槛检查（gate check）和充放切换间隔（switch_gap_hours）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ele_trading.utils.data_alignment import as_time_series, align_to_time
from ele_trading.utils.time_index import infer_dt_hours, monthly_kwh

from .dispatch_algo import dispatch_annual, _NUMBA_OK


@dataclass(slots=True)
class WindPVBESSPlanConfig:
    """
    Wind+PV+BESS 容量规划配置。
    """
    # 成本
    pv_capex_yuan_per_kwp: float = 2000.0
    bess_capex_yuan_per_kwh: float = 1000.0
    # 储能物理参数
    eta_roundtrip: float = 0.92
    c_rate: float = 0.5
    soc_init_frac: float = 0.5
    soc_min_frac: float = 0.1
    soc_max_frac: float = 1.0
    # 约束阈值
    self_use_ratio_min: float = 0.60
    load_cover_ratio_min: float = 0.20
    # PV 搜索参数
    pv_step_coarse_kwp: float = 2000.0
    pv_step_fine_kwp: float = 250.0
    pv_refine_window_kwp: float = 8000.0
    pv_min_kwp: float = 0.0
    pv_max_kwp: float | None = None
    # BESS 搜索参数
    enable_bess: bool = True
    batt_hi_init_kwh: float = 500.0
    batt_hi_max_kwh: float = 1e7
    batt_bisect_iter: int = 26
    batt_tol_kwh: float = 1.0
    # 能量门槛检查
    enable_gate_check: bool = True
    gate_target_ratio: float = 0.30
    # 充放切换间隔
    switch_gap_hours: float = 0.0
    # Numba 加速
    use_numba: bool = True


@dataclass(slots=True)
class WindPVBESSResult:
    """
    Wind+PV+BESS 容量规划结果。
    """
    status: str  # "ok", "gate_failed", "no_solution"
    pv_kwp: float = 0.0
    bess_kwh: float = 0.0
    self_use_ratio: float = 0.0
    load_cover_ratio: float = 0.0
    pv_gen_kwh_annual: float = 0.0
    pv_gen_kwh_monthly: pd.Series | None = None
    wind_gen_kwh_annual: float = 0.0
    wind_gen_kwh_monthly: pd.Series | None = None
    pv_capex_yuan: float = 0.0
    bess_capex_yuan: float = 0.0
    total_capex_yuan: float = 0.0
    engine: str = "python"
    switch_gap_hours: float = 0.0
    gate: dict | None = None
    debug: dict | None = None
    message: str | None = None


# ============================================================
# 电池二分：给定 PV，找最小 BESS
# ============================================================
def _find_min_bess_kwh(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    dt_hours: float,
    cfg: WindPVBESSPlanConfig,
    switch_gap_steps: int = 0,
) -> tuple[float, dict[str, float]] | None:
    """返回 (bess_kwh, stats)；若找不到可行解返回 None。"""

    def feasible(batt_kwh: float) -> tuple[bool, dict[str, float]]:
        st = dispatch_annual(load_kw, wind_kw, pv_kw, other_kw, batt_kwh, dt_hours, cfg, switch_gap_steps)
        gen = st["ren_gen_kwh"]
        used = st["ren_used_kwh"]
        load = st["load_kwh"]
        if gen <= 1e-9:
            return False, st
        self_use = used / gen
        cover = used / load if load > 1e-9 else 0.0
        ok = (self_use >= cfg.self_use_ratio_min) and (cover >= cfg.load_cover_ratio_min)
        st["self_use_ratio"] = self_use
        st["load_cover_ratio"] = cover
        return ok, st

    # 先试 0
    ok0, st0 = feasible(0.0)
    if ok0:
        return 0.0, st0

    # 扩上界
    hi = float(cfg.batt_hi_init_kwh)
    for _ in range(40):
        if hi > cfg.batt_hi_max_kwh:
            return None
        ok, st = feasible(hi)
        if ok:
            break
        hi *= 2.0
    else:
        return None

    lo = 0.0
    best_kwh = hi
    best_st = st
    # 二分
    for _ in range(cfg.batt_bisect_iter):
        mid = 0.5 * (lo + hi)
        ok, st_mid = feasible(mid)
        if ok:
            best_kwh, best_st = mid, st_mid
            hi = mid
        else:
            lo = mid
        if (hi - lo) <= cfg.batt_tol_kwh:
            break

    return float(best_kwh), best_st


# ============================================================
# 能量门槛检查
# ============================================================
def energy_gate_check(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    dt_hours: float,
    target_ratio: float = 0.30,
    other_kw: np.ndarray | None = None,
) -> dict[str, Any]:
    """先判断：发电量(风+光+其他) / 用电量 是否 >= target_ratio。"""
    if other_kw is None:
        other_kw = np.zeros_like(load_kw)
    load_kwh = float(load_kw.sum() * dt_hours)
    gen_kwh = float((wind_kw + pv_kw + other_kw).sum() * dt_hours)
    ratio = 0.0 if load_kwh <= 0 else gen_kwh / load_kwh

    return {
        "load_total_kwh": load_kwh,
        "gen_total_kwh": gen_kwh,
        "gen_ratio": ratio,
        "pass_gate": (ratio >= target_ratio),
    }


def evaluate_fixed_wind_pv_bess_capacity(
    df_load: pd.DataFrame,
    *,
    wind_unit_kw: pd.Series | pd.DataFrame,
    pv_unit_kw: pd.Series | pd.DataFrame,
    wind_mw: float,
    pv_mw: float,
    bess_mwh: float,
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: WindPVBESSPlanConfig = WindPVBESSPlanConfig(),
    wind_unit: str = "kW",
    pv_unit: str = "kW",
    other_input: pd.Series | pd.DataFrame | None = None,
    other_unit: str = "kW",
) -> dict[str, float]:
    """评估固定 Wind/PV/BESS 容量组合，不执行容量搜索。

    wind_unit_kw 表示每 1 MW 风电装机对应的 kW 出力曲线。
    pv_unit_kw 表示每 1 kWp 光伏装机对应的 kW 出力曲线。
    """
    df = df_load[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    load_kw_arr = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    load_kw_arr = np.ascontiguousarray(load_kw_arr, dtype=np.float64)

    wind_scale = 1000.0 if wind_unit.lower() == "mw" else 1.0
    wind_s = as_time_series(
        wind_unit_kw,
        time_col=time_col,
        value_cols=("wind_unit_kw", "WindPower_MW", "wind_mw", "wind_kw", "WindPower_kW", "WindPower", "value"),
        scale=wind_scale,
    )
    wind_unit_arr = align_to_time(df[time_col], wind_s)
    wind_kw_arr = np.ascontiguousarray(wind_unit_arr * float(wind_mw), dtype=np.float64)

    pv_scale = 1000.0 if pv_unit.lower() == "mw" else 1.0
    pv_s = as_time_series(
        pv_unit_kw,
        time_col=time_col,
        value_cols=("pv_unit_kw", "pv_kw", "u", "value"),
        scale=pv_scale,
    )
    pv_unit_arr = align_to_time(df[time_col], pv_s)
    pv_kw_arr = np.ascontiguousarray(pv_unit_arr * float(pv_mw) * 1000.0, dtype=np.float64)

    if other_input is None:
        other_kw_arr = np.zeros_like(load_kw_arr, dtype=np.float64)
    else:
        other_scale = 1.0 if other_unit.lower() == "kw" else 1000.0
        other_s = as_time_series(
            other_input,
            time_col=time_col,
            value_cols=("other_kw", "OtherPower_kW", "OtherPower", "value"),
            scale=other_scale,
        )
        other_kw_arr = align_to_time(df[time_col], other_s)

    dt_hours = infer_dt_hours(df[time_col])
    switch_gap_steps = int(round(cfg.switch_gap_hours / dt_hours)) if cfg.switch_gap_hours > 0 else 0
    st = dispatch_annual(
        load_kw_arr,
        wind_kw_arr,
        pv_kw_arr,
        other_kw_arr,
        float(bess_mwh) * 1000.0,
        dt_hours,
        cfg,
        switch_gap_steps,
    )
    gen = st["ren_gen_kwh"]
    used = st["ren_used_kwh"]
    load = st["load_kwh"]
    st["self_use_ratio"] = used / gen if gen > 1e-9 else 0.0
    st["load_cover_ratio"] = used / load if load > 1e-9 else 0.0
    st["dt_hours"] = dt_hours
    return st


# ============================================================
# 主规划函数
# ============================================================
def plan_wind_pv_bess(
    df_load: pd.DataFrame,
    pv_unit_kw: pd.Series | pd.DataFrame,
    wind_input: pd.Series | pd.DataFrame,
    *,
    load_col: str = "P_kw",
    time_col: str = "Time",
    cfg: WindPVBESSPlanConfig = WindPVBESSPlanConfig(),
    wind_unit: str = "MW",
    pv_unit: str = "kW",
    other_input: pd.Series | pd.DataFrame | None = None,
    other_unit: str = "kW",
) -> WindPVBESSResult:
    """
    Wind+PV+BESS 容量规划主入口。

    Args:
        df_load: 负荷数据 DataFrame
        pv_unit_kw: 光伏单位出力 (kW/kWp)
        wind_input: 风电数据 (MW 或 kW)
        load_col: 负荷列名
        time_col: 时间列名
        cfg: 规划配置
        wind_unit: 风电单位 ("MW" 或 "kW")
        pv_unit: 光伏单位 ("kW" 或 "MW")
        other_input: 其他新能源输入（可选）
        other_unit: 其他新能源单位

    Returns:
        WindPVBESSResult: 规划结果
    """
    # ---- 负荷 ----
    df = df_load[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    load_kw_arr = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    load_kw_arr = np.ascontiguousarray(load_kw_arr, dtype=np.float64)

    # ---- 风电 ----
    wind_scale = 1000.0 if wind_unit.lower() == "mw" else 1.0
    wind_s = as_time_series(
        wind_input,
        time_col=time_col,
        value_cols=("WindPower_MW", "wind_mw", "wind_kw", "WindPower_kW", "WindPower"),
        scale=wind_scale,
    )
    wind_kw_arr = align_to_time(df[time_col], wind_s)

    # ---- PV ----
    pv_scale = 1000.0 if pv_unit.lower() == "mw" else 1.0
    pv_unit_s = as_time_series(
        pv_unit_kw,
        time_col=time_col,
        value_cols=("pv_unit_kw", "pv_kw", "u", "value"),
        scale=pv_scale,
    )
    pv_unit_arr = align_to_time(df[time_col], pv_unit_s)

    # ---- 其他新能源 ----
    if other_input is None:
        other_kw_arr = np.zeros_like(load_kw_arr, dtype=np.float64)
    else:
        other_scale = 1.0 if other_unit.lower() == "kw" else 1000.0
        other_s = as_time_series(
            other_input,
            time_col=time_col,
            value_cols=("other_kw", "OtherPower_kW", "OtherPower"),
            scale=other_scale,
        )
        other_kw_arr = align_to_time(df[time_col], other_s)

    # ---- 基础量 ----
    dt_hours = infer_dt_hours(df[time_col])
    load_kwh_total = float(load_kw_arr.sum() * dt_hours)
    wind_kwh_total = float(wind_kw_arr.sum() * dt_hours)
    other_kwh_total = float(other_kw_arr.sum() * dt_hours)
    peak_load = float(load_kw_arr.max()) if len(load_kw_arr) else 0.0
    pv_max_kwp = cfg.pv_max_kwp or max(cfg.pv_step_coarse_kwp, 3.0 * peak_load)
    wind_monthly_kwh = monthly_kwh(df[time_col], wind_kw_arr, dt_hours)

    # ---- 充放切换间隔（步数）----
    switch_gap_steps = int(round(cfg.switch_gap_hours / dt_hours)) if cfg.switch_gap_hours > 0 else 0

    # ---- 能量门槛检查 ----
    gate_result = None
    if cfg.enable_gate_check:
        pv_max_kw_arr = pv_unit_arr * pv_max_kwp
        pv_max_kwh = float(pv_max_kw_arr.sum() * dt_hours)
        total_gen_kwh = wind_kwh_total + pv_max_kwh + other_kwh_total
        gen_ratio = total_gen_kwh / load_kwh_total if load_kwh_total > 0 else 0.0

        gate_result = {
            "load_total_kwh": load_kwh_total,
            "gen_total_kwh": total_gen_kwh,
            "gen_ratio": gen_ratio,
            "pass_gate": (gen_ratio >= cfg.gate_target_ratio),
        }

        if not gate_result["pass_gate"]:
            return WindPVBESSResult(
                status="gate_failed",
                gate=gate_result,
                switch_gap_hours=cfg.switch_gap_hours,
                message=(
                    f"能量门槛未通过：风+光年发电量占比={gen_ratio:.3f}，"
                    f"低于目标{cfg.gate_target_ratio:.2f}。"
                ),
            )

    # ==========================
    # PV 搜索：粗扫 + 可选细扫
    # ==========================
    best: dict[str, Any] | None = None
    best_pv_kwp_coarse: float | None = None
    pv_candidates = np.arange(cfg.pv_min_kwp, pv_max_kwp + 1e-9, cfg.pv_step_coarse_kwp)

    for pv_kwp in pv_candidates:
        pv_kw_arr = pv_unit_arr * float(pv_kwp)
        pv_kwh = float(pv_kw_arr.sum() * dt_hours)

        # 快速能量剪枝
        if (wind_kwh_total + pv_kwh + other_kwh_total) < cfg.load_cover_ratio_min * load_kwh_total:
            continue

        # 可行性判断
        if cfg.enable_bess:
            found = _find_min_bess_kwh(
                load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr,
                dt_hours, cfg, switch_gap_steps,
            )
            if found is None:
                continue
            bess_kwh, st = found
        else:
            st = dispatch_annual(
                load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr, 0.0,
                dt_hours, cfg, switch_gap_steps,
            )
            if st["ren_gen_kwh"] <= 1e-9:
                continue
            self_use = st["ren_used_kwh"] / st["ren_gen_kwh"]
            cover = st["ren_used_kwh"] / st["load_kwh"] if st["load_kwh"] > 1e-9 else 0.0
            if (self_use < cfg.self_use_ratio_min) or (cover < cfg.load_cover_ratio_min):
                continue
            st["self_use_ratio"] = self_use
            st["load_cover_ratio"] = cover
            bess_kwh = 0.0

        # 计算总成本
        pv_capex = float(pv_kwp) * cfg.pv_capex_yuan_per_kwp
        bess_capex = float(bess_kwh) * cfg.bess_capex_yuan_per_kwh
        total_capex = pv_capex + bess_capex

        if (best is None) or (total_capex < best["total_capex_yuan"]):
            best = {
                "pv_kwp": float(pv_kwp),
                "bess_kwh": float(bess_kwh),
                "pv_capex_yuan": pv_capex,
                "bess_capex_yuan": bess_capex,
                "total_capex_yuan": total_capex,
                "self_use_ratio": float(st["self_use_ratio"]),
                "load_cover_ratio": float(st["load_cover_ratio"]),
                "ren_gen_kwh": float(st["ren_gen_kwh"]),
                "ren_used_kwh": float(st["ren_used_kwh"]),
                "direct_used_kwh": float(st["direct_used_kwh"]),
                "bess_discharge_kwh": float(st["bess_discharge_kwh"]),
                "curtail_kwh": float(st.get("curtail_kwh", 0.0)),
                "engine": "numba" if (cfg.use_numba and _NUMBA_OK) else "python",
            }
            best_pv_kwp_coarse = float(pv_kwp)

    if best is None:
        return WindPVBESSResult(
            status="no_solution",
            message="未找到满足新能源自用率/覆盖率约束的方案：请扩大 pv_max_kwp 或放宽比例阈值。",
        )

    # ---- 可选：细扫 ----
    if cfg.pv_step_fine_kwp > 0 and best_pv_kwp_coarse is not None:
        lo = max(cfg.pv_min_kwp, best_pv_kwp_coarse - cfg.pv_refine_window_kwp)
        hi = min(pv_max_kwp, best_pv_kwp_coarse + cfg.pv_refine_window_kwp)
        fine_candidates = np.arange(lo, hi + 1e-9, cfg.pv_step_fine_kwp)

        for pv_kwp in fine_candidates:
            pv_kw_arr = pv_unit_arr * float(pv_kwp)
            pv_kwh = float(pv_kw_arr.sum() * dt_hours)

            if (wind_kwh_total + pv_kwh + other_kwh_total) < cfg.load_cover_ratio_min * load_kwh_total:
                continue

            if cfg.enable_bess:
                found = _find_min_bess_kwh(
                    load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr,
                    dt_hours, cfg, switch_gap_steps,
                )
                if found is None:
                    continue
                bess_kwh, st = found
            else:
                st = dispatch_annual(
                    load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr, 0.0,
                    dt_hours, cfg, switch_gap_steps,
                )
                if st["ren_gen_kwh"] <= 1e-9:
                    continue
                self_use = st["ren_used_kwh"] / st["ren_gen_kwh"]
                cover = st["ren_used_kwh"] / st["load_kwh"] if st["load_kwh"] > 1e-9 else 0.0
                if (self_use < cfg.self_use_ratio_min) or (cover < cfg.load_cover_ratio_min):
                    continue
                st["self_use_ratio"] = self_use
                st["load_cover_ratio"] = cover
                bess_kwh = 0.0

            pv_capex = float(pv_kwp) * cfg.pv_capex_yuan_per_kwp
            bess_capex = float(bess_kwh) * cfg.bess_capex_yuan_per_kwh
            total_capex = pv_capex + bess_capex

            if total_capex < best["total_capex_yuan"]:
                best.update({
                    "pv_kwp": float(pv_kwp),
                    "bess_kwh": float(bess_kwh),
                    "pv_capex_yuan": pv_capex,
                    "bess_capex_yuan": bess_capex,
                    "total_capex_yuan": total_capex,
                    "self_use_ratio": float(st["self_use_ratio"]),
                    "load_cover_ratio": float(st["load_cover_ratio"]),
                    "ren_gen_kwh": float(st["ren_gen_kwh"]),
                    "ren_used_kwh": float(st["ren_used_kwh"]),
                    "direct_used_kwh": float(st["direct_used_kwh"]),
                    "bess_discharge_kwh": float(st["bess_discharge_kwh"]),
                    "curtail_kwh": float(st.get("curtail_kwh", 0.0)),
                })

    # ==========================
    # 输出年/月 PV、风电发电量
    # ==========================
    pv_gen_kw_arr = pv_unit_arr * float(best["pv_kwp"])
    pv_gen_kwh_total = float(pv_gen_kw_arr.sum() * dt_hours)
    pv_monthly_kwh = monthly_kwh(df[time_col], pv_gen_kw_arr, dt_hours)

    return WindPVBESSResult(
        status="ok",
        pv_kwp=best["pv_kwp"],
        bess_kwh=best["bess_kwh"],
        self_use_ratio=best["self_use_ratio"],
        load_cover_ratio=best["load_cover_ratio"],
        pv_gen_kwh_annual=pv_gen_kwh_total,
        pv_gen_kwh_monthly=pv_monthly_kwh,
        wind_gen_kwh_annual=wind_kwh_total,
        wind_gen_kwh_monthly=wind_monthly_kwh,
        pv_capex_yuan=best["pv_capex_yuan"],
        bess_capex_yuan=best["bess_capex_yuan"],
        total_capex_yuan=best["total_capex_yuan"],
        engine=best["engine"],
        switch_gap_hours=cfg.switch_gap_hours,
        gate=gate_result,
        debug={
            "ren_gen_kwh": best["ren_gen_kwh"],
            "ren_used_kwh": best["ren_used_kwh"],
            "direct_used_kwh": best["direct_used_kwh"],
            "bess_discharge_kwh": best["bess_discharge_kwh"],
            "curtail_kwh": best.get("curtail_kwh", 0.0),
            "dt_hours": dt_hours,
            "pv_max_kwp_used": pv_max_kwp,
        },
    )


# ============================================================
# 固定 PV 评估入口（Mode 2: PV 不参与搜索）
# ============================================================
def evaluate_wind_pv_bess(
    df_load: pd.DataFrame,
    pv_kw: pd.DataFrame,
    df_wind: pd.DataFrame,
    *,
    load_col: str = "P_kw",
    pv_col: str = "pv_kw",
    wind_col: str = "WindPower_MW",
    time_col: str = "Time",
    cfg: WindPVBESSPlanConfig = WindPVBESSPlanConfig(),
) -> dict[str, Any]:
    """
    评估固定 PV + Wind + BESS 的方案（PV 不参与搜索）。

    Args:
        df_load: 负荷数据
        pv_kw: 光伏出力数据 (kW)
        df_wind: 风电数据 (MW)
        load_col: 负荷列名
        pv_col: 光伏列名
        wind_col: 风电列名
        time_col: 时间列名
        cfg: 规划配置

    Returns:
        dict: 评估结果，包含 status、gate、bess_result 等
    """
    # ---- 负荷 ----
    df = df_load[[time_col, load_col]].copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)
    load_kw_arr = pd.to_numeric(df[load_col], errors="coerce").fillna(0.0).to_numpy(dtype="float64")
    load_kw_arr = np.ascontiguousarray(load_kw_arr, dtype=np.float64)

    # ---- 风电 ----
    wind_s = as_time_series(
        df_wind,
        time_col=time_col,
        value_cols=("WindPower_MW", "wind_mw", "wind_kw", "WindPower_kW", "WindPower"),
        scale=1000.0,
    )
    wind_kw_arr = align_to_time(df[time_col], wind_s)

    # ---- PV ----
    pv_s = as_time_series(
        pv_kw,
        time_col=time_col,
        value_cols=("pv_kw", "pv_unit_kw", "u", "value"),
        scale=1.0,
    )
    pv_kw_arr = align_to_time(df[time_col], pv_s)

    # ---- 基础量 ----
    dt_hours = infer_dt_hours(df[time_col])
    switch_gap_steps = int(round(cfg.switch_gap_hours / dt_hours)) if cfg.switch_gap_hours > 0 else 0

    # ---- 能量门槛检查 ----
    gate = energy_gate_check(
        load_kw_arr, wind_kw_arr, pv_kw_arr, dt_hours,
        target_ratio=cfg.gate_target_ratio,
    )

    out: dict[str, Any] = {
        "gate": gate,
        "status": "gate_failed" if not gate["pass_gate"] else "gate_passed",
    }

    if not gate["pass_gate"]:
        out["message"] = (
            f"能量门槛未通过：风+光年发电量占比={gate['gen_ratio']:.3f}，"
            f"低于目标{cfg.gate_target_ratio:.2f}。"
        )
        return out

    # ---- BESS 二分搜索 ----
    other_kw_arr = np.zeros_like(load_kw_arr, dtype=np.float64)

    if cfg.enable_bess:
        found = _find_min_bess_kwh(
            load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr,
            dt_hours, cfg, switch_gap_steps,
        )
        if found is None:
            out["bess_result"] = {"status": "not_reachable"}
            return out
        bess_kwh, st = found
        out["bess_result"] = {
            "status": "ok",
            "bess_kwh": bess_kwh,
            "self_use_ratio": st["self_use_ratio"],
            "load_cover_ratio": st["load_cover_ratio"],
            "ren_gen_kwh": st["ren_gen_kwh"],
            "ren_used_kwh": st["ren_used_kwh"],
            "curtail_kwh": st.get("curtail_kwh", 0.0),
        }
    else:
        st = dispatch_annual(
            load_kw_arr, wind_kw_arr, pv_kw_arr, other_kw_arr, 0.0,
            dt_hours, cfg, switch_gap_steps,
        )
        gen = st["ren_gen_kwh"]
        used = st["ren_used_kwh"]
        load = st["load_kwh"]
        self_use = used / gen if gen > 1e-9 else 0.0
        cover = used / load if load > 1e-9 else 0.0
        out["bess_result"] = {
            "status": "ok",
            "bess_kwh": 0.0,
            "self_use_ratio": self_use,
            "load_cover_ratio": cover,
            "ren_gen_kwh": gen,
            "ren_used_kwh": used,
            "curtail_kwh": st.get("curtail_kwh", 0.0),
        }

    return out
