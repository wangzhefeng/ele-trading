from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from investment_estimation.utils.time_index import infer_dt_hours
from investment_estimation.utils.data_alignment import (
    as_time_series,
    normalize_time_and_load,
    align_to_time,
)

# Numba 兼容层
try:
    from numba import njit
    _NUMBA_OK = True
except Exception:
    _NUMBA_OK = False

    def njit(*args, **kwargs):
        def deco(f):
            return f
        return deco


# ============================================================
# 配置数据类
# ============================================================
@dataclass(slots=True)
class UnitsConfig:
    """声明输入数据的功率单位。"""
    load_power: str = "kW"
    pv_power: str = "kW"
    wind_power: str = "MW"


@dataclass(slots=True)
class BESSPlanConfig:
    """BESS 容量规划配置。"""
    # 成本
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
    # 搜索参数
    batt_hi_max_kwh: float = 1e7
    search_points: int = 40
    # 引擎
    use_numba: bool = True


@dataclass(slots=True)
class BESSCapacityResult:
    """BESS 容量规划结果。"""
    feasible: bool
    bess_kwh: float = 0.0
    cost_yuan: float = 0.0
    self_use_ratio: float = 0.0
    load_cover_ratio: float = 0.0
    gen_kwh: float = 0.0
    used_kwh: float = 0.0
    load_kwh: float = 0.0
    bess_discharge_kwh: float = 0.0
    engine: str = "python"
    warnings: list = field(default_factory=list)
    diagnosis: dict | None = None


# ============================================================
# 调度引擎
# ============================================================
@njit
def _dispatch_numba(
    load_kw: np.ndarray,
    gen_kw: np.ndarray,
    dt: float,
    batt_kwh: float,
    eta: float,
    c_rate: float,
    soc0: float,
    soc_min_f: float,
    soc_max_f: float,
) -> tuple[float, float, float, float]:
    """贪心调度（Numba JIT）。"""
    gen_e = used_e = load_e = bess_dis = 0.0

    if batt_kwh <= 0:
        for i in range(load_kw.shape[0]):
            L = max(load_kw[i], 0.0)
            G = max(gen_kw[i], 0.0)
            load_e += L * dt
            gen_e += G * dt
            used_e += (L if L < G else G) * dt
        return gen_e, used_e, load_e, bess_dis

    soc = soc0 * batt_kwh
    soc_min = soc_min_f * batt_kwh
    soc_max = soc_max_f * batt_kwh
    pmax = c_rate * batt_kwh
    eta_c = eta ** 0.5
    eta_d = eta ** 0.5

    for i in range(load_kw.shape[0]):
        L = max(load_kw[i], 0.0)
        G = max(gen_kw[i], 0.0)

        load_e += L * dt
        gen_e += G * dt

        direct = L if L < G else G
        used_e += direct * dt

        surplus = G - direct
        deficit = L - direct

        if surplus > 1e-9 and soc < soc_max:
            ch = surplus
            if ch > pmax:
                ch = pmax
            max_ch = (soc_max - soc) / dt
            if ch > max_ch:
                ch = max_ch
            soc += ch * dt * eta_c

        if deficit > 1e-9 and soc > soc_min:
            dis = deficit
            if dis > pmax:
                dis = pmax
            max_dis = (soc - soc_min) * eta_d / dt
            if dis > max_dis:
                dis = max_dis
            soc -= dis * dt / eta_d
            used_e += dis * dt
            bess_dis += dis * dt

    return gen_e, used_e, load_e, bess_dis


def _dispatch(
    load_kw: np.ndarray,
    gen_kw: np.ndarray,
    dt: float,
    batt_kwh: float,
    eta_roundtrip: float,
    c_rate: float,
    soc_init_frac: float,
    soc_min_frac: float,
    soc_max_frac: float,
    use_numba: bool = True,
) -> dict[str, float]:
    """调度仿真，含 Numba / fallback 切换。"""
    if use_numba and _NUMBA_OK:
        g, u, l, b = _dispatch_numba(
            load_kw, gen_kw, dt, float(batt_kwh),
            float(eta_roundtrip), float(c_rate),
            float(soc_init_frac), float(soc_min_frac), float(soc_max_frac),
        )
    else:
        g = float(np.maximum(gen_kw, 0.0).sum() * dt)
        l = float(np.maximum(load_kw, 0.0).sum() * dt)
        u = float(np.minimum(np.maximum(load_kw, 0.0), np.maximum(gen_kw, 0.0)).sum() * dt)
        b = 0.0

    return {
        "gen_kwh": float(g),
        "used_kwh": float(u),
        "load_kwh": float(l),
        "self_use_ratio": float(u / g) if g > 1e-9 else 0.0,
        "load_cover_ratio": float(u / l) if l > 1e-9 else 0.0,
        "bess_discharge_kwh": float(b),
    }


def simulate_bess_operation(
    load_kw: np.ndarray,
    gen_kw: np.ndarray,
    dt_hours: float,
    bess_kwh: float,
    cfg: BESSPlanConfig,
) -> dict[str, float]:
    """单次 BESS 调度仿真（公开入口）。"""
    return _dispatch(
        load_kw, gen_kw, dt_hours, bess_kwh,
        cfg.eta_roundtrip, cfg.c_rate,
        cfg.soc_init_frac, cfg.soc_min_frac, cfg.soc_max_frac,
        use_numba=cfg.use_numba,
    )


# ============================================================
# 主规划函数
# ============================================================
def plan_energy_system(
    df_load: pd.DataFrame,
    *,
    pv_power: pd.Series | pd.DataFrame | None = None,
    wind_input: pd.Series | pd.DataFrame | None = None,
    time_col: str = "Time",
    load_col: str = "P_kw",
    cfg: BESSPlanConfig = BESSPlanConfig(),
    units: UnitsConfig = UnitsConfig(),
) -> BESSCapacityResult:
    """离网风光储容量规划：搜索满足约束的最小储能容量。"""

    # ---------- 负荷 ----------
    try:
        t, load_kw, load_warn = normalize_time_and_load(df_load, time_col, load_col, units.load_power)
        dt = infer_dt_hours(t)
    except Exception as e:
        return BESSCapacityResult(feasible=False, diagnosis={"stage": "load", "msg": str(e)})

    # ---------- 风 ----------
    wind_kw = np.zeros_like(load_kw)
    if wind_input is not None:
        try:
            scale = 1000.0 if units.wind_power.lower() == "mw" else 1.0
            w = as_time_series(wind_input, time_col, ("WindPower_MW", "wind_mw", "wind_kw"), scale)
            wind_kw = align_to_time(t, w)
        except Exception as e:
            return BESSCapacityResult(feasible=False, diagnosis={"stage": "wind", "msg": str(e)})

    # ---------- PV ----------
    pv_kw = np.zeros_like(load_kw)
    if pv_power is not None:
        try:
            pv = as_time_series(pv_power, time_col, ("pv_unit_kw", "pv_kw", "value"), 1.0)
            pv_kw = align_to_time(t, pv)
        except Exception as e:
            return BESSCapacityResult(feasible=False, diagnosis={"stage": "pv", "msg": str(e)})

    # ---------- 总新能源 ----------
    gen_kw = wind_kw + pv_kw

    # ---------- 仅储能场景 ----------
    if pv_power is None and wind_input is None:
        return BESSCapacityResult(
            feasible=False,
            diagnosis={
                "reason": "NO_GENERATION",
                "msg": "无 PV / 无风，仅储能无法创造能量，仅可做移峰套利",
            },
        )

    # ---------- 储能搜索 ----------
    best = None
    for batt in np.linspace(0, cfg.batt_hi_max_kwh, cfg.search_points):
        stats = _dispatch(
            load_kw, gen_kw, dt, batt,
            cfg.eta_roundtrip, cfg.c_rate,
            cfg.soc_init_frac, cfg.soc_min_frac, cfg.soc_max_frac,
            use_numba=cfg.use_numba,
        )
        if stats["self_use_ratio"] >= cfg.self_use_ratio_min and stats["load_cover_ratio"] >= cfg.load_cover_ratio_min:
            cost = batt * cfg.bess_capex_yuan_per_kwh
            if best is None or cost < best["cost"]:
                best = {"bess_kwh": batt, "metrics": stats, "cost": cost}

    if best is None:
        return BESSCapacityResult(
            feasible=False,
            diagnosis={
                "reason": "NO_FEASIBLE_SOLUTION",
                "msg": "在当前约束和搜索上限下未找到可行储能容量。",
                "self_use_ratio_min": cfg.self_use_ratio_min,
                "load_cover_ratio_min": cfg.load_cover_ratio_min,
            },
        )

    m = best["metrics"]
    return BESSCapacityResult(
        feasible=True,
        bess_kwh=best["bess_kwh"],
        cost_yuan=best["cost"],
        self_use_ratio=m["self_use_ratio"],
        load_cover_ratio=m["load_cover_ratio"],
        gen_kwh=m["gen_kwh"],
        used_kwh=m["used_kwh"],
        load_kwh=m["load_kwh"],
        bess_discharge_kwh=m["bess_discharge_kwh"],
        engine="numba" if (cfg.use_numba and _NUMBA_OK) else "python",
        warnings=load_warn,
    )
