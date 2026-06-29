"""单源新能源 + BESS 容量规划的共享调度与搜索内核。

pv_bess_planner 和 wind_bess_planner 的调度逻辑完全同构，仅资源类型不同
（PV / Wind）。本模块把调度仿真、可行性检查、二分搜索提取为资源无关的
通用实现，两个 planner 只需传入 resource_kw 数组和对应的配置即可复用。

核心抽象：
    - ResourceBESSConfig：物理参数 + 约束阈值 + 搜索参数（不含资源特定字段）
    - simulate_dispatch()：纯弃电搬运 / 平移充电两种模式的统一仿真
    - find_min_capacity_bisect()：二分搜索最小可行容量

效率模型：充放分离 eta_charge / eta_discharge（与调用方配置一致）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


# ============================================================
# 配置与结果数据类
# ============================================================
@dataclass(slots=True)
class ShiftPolicy:
    """平移充电策略配置（资源无关）。"""
    enable_shift: bool = False
    lookahead_steps: int = 8
    shift_max_frac_of_resource: float = 0.30


@dataclass(slots=True)
class ResourceBESSConfig:
    """单源（PV 或 Wind）+ BESS 容量规划的共享配置。

    资源特定的成本字段（如 pv_capex_yuan_per_kwp、capex_cny_per_kwh）
    由调用方 planner 自行维护，此处只含调度与搜索所需参数。
    """
    # 储能物理参数
    eta_charge: float = 0.92
    eta_discharge: float = 0.92
    c_rate: float = 1.0
    soc_init: float = 0.50
    soc_min: float = 0.10
    soc_max: float = 1.00
    enforce_terminal_soc: bool = False
    # 约束阈值
    min_self_consumption: float = 0.60
    min_load_coverage: float = 0.30
    # 搜索参数
    cap_max_mwh: float = 5000.0
    tol_mwh: float = 0.1
    # 策略
    shift_policy: ShiftPolicy = field(default_factory=ShiftPolicy)


# ============================================================
# 调度仿真 - 纯弃电搬运模式
# ============================================================
def simulate_surplus_shift(
    load_kw: np.ndarray,
    resource_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
    cfg: ResourceBESSConfig,
) -> dict[str, Any]:
    """纯弃电搬运模式：surplus 充电，deficit 放电，无平移。"""
    n = len(load_kw)

    if cap_kwh <= 0:
        served = np.minimum(resource_kw, load_kw)
        curtail = np.maximum(resource_kw - served, 0.0)
        soc = np.full(n, cfg.soc_init, dtype=float)
        charge = np.zeros(n, dtype=float)
        discharge = np.zeros(n, dtype=float)
        return post_metrics(served, load_kw, resource_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)

    pmax = cfg.c_rate * cap_kwh
    soc_min_e = cfg.soc_min * cap_kwh
    soc_max_e = cfg.soc_max * cap_kwh

    soc = np.zeros(n, dtype=float)
    charge = np.zeros(n, dtype=float)
    discharge = np.zeros(n, dtype=float)
    served = np.zeros(n, dtype=float)
    curtail = np.zeros(n, dtype=float)

    e = cfg.soc_init * cap_kwh

    for t in range(n):
        L = float(max(0.0, load_kw[t]))
        R = float(max(0.0, resource_kw[t]))

        served_direct = min(L, R)
        surplus = max(R - L, 0.0)
        deficit = max(L - R, 0.0)

        room = max(soc_max_e - e, 0.0)
        avail = max(e - soc_min_e, 0.0)

        ch = min(surplus, pmax, room / (cfg.eta_charge * dt_h))
        dis = min(deficit, pmax, avail * cfg.eta_discharge / dt_h)

        e += (ch * cfg.eta_charge - dis / cfg.eta_discharge) * dt_h
        e = float(np.clip(e, soc_min_e, soc_max_e))

        charge[t] = ch
        discharge[t] = dis
        served[t] = served_direct + dis
        curtail[t] = max(surplus - ch, 0.0)
        soc[t] = e / cap_kwh if cap_kwh > 0 else cfg.soc_init

    return post_metrics(served, load_kw, resource_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)


# ============================================================
# 调度仿真 - 平移充电模式
# ============================================================
def simulate_shift(
    load_kw: np.ndarray,
    resource_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
    cfg: ResourceBESSConfig,
    policy: ShiftPolicy,
) -> dict[str, Any]:
    """平移充电模式：允许 Resource < Load 时主动充电（lookahead 预判）。"""
    n = len(load_kw)

    if cap_kwh <= 0:
        served = np.minimum(resource_kw, load_kw)
        curtail = np.maximum(resource_kw - served, 0.0)
        soc = np.full(n, cfg.soc/WESS, dtype=float) if False else np.full(n, cfg.soc_init, dtype=float)
        charge = np.zeros(n, dtype=float)
        discharge = np.zeros(n, dtype=float)
        return post_metrics(served, load_kw, resource_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)

    pmax = cfg.c_rate * cap_kwh

    soc = np.zeros(n, dtype=float)
    charge = np.zeros(n, dtype=float)
    discharge = np.zeros(n, dtype=float)
    served = np.zeros(n, dtype=float)
    curtail = np.zeros(n, dtype=float)

    e = cfg.soc_init * cap_kwh
    soc_min_e = cfg.soc_min * cap_kwh
    soc_max_e = cfg.soc_max * cap_kwh

    look = max(1, int(policy.lookahead_steps))
    net = load_kw - resource_kw

    for t in range(n):
        L = float(max(0.0, load_kw[t]))
        R = float(max(0.0, resource_kw[t]))

        room = max(0.0, soc_max_e - e)
        avail = max(0.0, e - soc_min_e)

        ch_max = min(pmax, room / (cfg.eta_charge * dt_h)) if room > 0 else 0.0
        dis_max_out = min(pmax, (avail * cfg.eta_discharge) / dt_h) if avail > 0 else 0.0

        # 1) 判断是否平移充电（即使 R < L）
        ch_plan = 0.0
        if ch_max > 0 and R > 0:
            t2 = min(n, t + look)
            future_def = float(np.maximum(net[t:t2], 0.0).sum())
            soc_ratio = e / cap_kwh
            if future_def > 0.5 * L * (t2 - t) and soc_ratio < 0.7:
                ch_plan = min(ch_max, policy.shift_max_frac_of_resource * R)

        # 2) 资源分配：先预留 ch_plan，再供负荷
        R_after_ch = max(0.0, R - ch_plan)
        serve_from_r = min(L, R_after_ch)

        # 3) 电池放电补缺口
        deficit = L - serve_from_r
        dis_out = min(dis_max_out, max(0.0, deficit))
        served_t = serve_from_r + dis_out

        # 4) 富余资源继续充电
        surplus = max(0.0, R - serve_from_r - ch_plan)
        ch_extra = min(max(0.0, ch_max - ch_plan), surplus)
        ch_in = ch_plan + ch_extra

        # 5) 弃电
        curtail_t = max(0.0, R - serve_from_r - ch_in)

        # 6) 更新能量
        e += (ch_in * cfg.eta_charge - dis_out / cfg.eta_discharge) * dt_h
        e = float(np.clip(e, soc_min_e, soc_max_e))

        charge[t] = ch_in
        discharge[t] = dis_out
        served[t] = served_t
        curtail[t] = curtail_t
        soc[t] = e / cap_kwh

    # 期末 SOC 约束
    if cfg.enforce_terminal_soc:
        if abs(soc[-1] - cfg.soc_init) > 0.02:
            res = post_metrics(served, load_kw, resource_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)
            res["terminal_soc_ok"] = False
            return res

    res = post_metrics(served, load_kw, resource_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)
    res["terminal_soc_ok"] = True
    return res


# ============================================================
# 统一调度仿真入口
# ============================================================
def simulate_dispatch(
    load_kw: np.ndarray,
    resource_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
    cfg: ResourceBESSConfig,
) -> dict[str, Any]:
    """统一调度仿真：根据 cfg.shift_policy.enable_shift 选择模式。"""
    if cfg.shift_policy.enable_shift:
        return simulate_shift(load_kw, resource_kw, dt_h, cap_kwh, cfg, cfg.shift_policy)
    else:
        return simulate_surplus_shift(load_kw, resource_kw, dt_h, cap_kwh, cfg)


# ============================================================
# 指标计算
# ============================================================
def post_metrics(
    served_kw: np.ndarray,
    load_kw: np.ndarray,
    resource_kw: np.ndarray,
    charge_kw: np.ndarray,
    discharge_kw: np.ndarray,
    soc: np.ndarray,
    curtail_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
) -> dict[str, Any]:
    """从时序仿真结果计算年度指标。"""
    e_load = float(load_kw.sum() * dt_h)
    e_resource = float(resource_kw.sum() * dt_h)
    e_served = float(served_kw.sum() * dt_h)
    e_curtail = float(curtail_kw.sum() * dt_h)

    self_consumption = (e_served / e_resource) if e_resource > 0 else 0.0
    coverage = (e_served / e_load) if e_load > 0 else 0.0

    e_dis = float(discharge_kw.sum() * dt_h)
    equiv_cycles = (e_dis / cap_kwh) if cap_kwh > 0 else 0.0

    return {
        "energy_kwh": {
            "load": e_load,
            "resource": e_resource,
            "served": e_served,
            "curtail": e_curtail,
            "charge_in": float(charge_kw.sum() * dt_h),
            "discharge_out": float(discharge_kw.sum() * dt_h),
        },
        "metrics": {
            "self_consumption": float(self_consumption),
            "load_coverage": float(coverage),
            "equiv_cycles": float(equiv_cycles),
        },
        "series": {
            "served_kw": served_kw,
            "charge_kw": charge_kw,
            "discharge_kw": discharge_kw,
            "soc": soc,
            "curtail_kw": curtail_kw,
        }
    }


# ============================================================
# 快速可行性诊断
# ============================================================
def quick_feasibility_diagnose(
    load_kw: np.ndarray,
    resource_kw: np.ndarray,
    dt_h: float,
    cfg: ResourceBESSConfig,
) -> dict[str, float]:
    """给出几个关键上界/必要条件：能量比、富余能量比例、理论最大 served 上界。"""
    e_load = float(load_kw.sum() * dt_h)
    e_resource = float(resource_kw.sum() * dt_h)
    direct = np.minimum(load_kw, resource_kw)
    surplus = np.maximum(resource_kw - load_kw, 0.0)
    e_direct = float(direct.sum() * dt_h)
    e_surplus = float(surplus.sum() * dt_h)
    eta_rt = cfg.eta_charge * cfg.eta_discharge

    e_served_upper = e_direct + eta_rt * e_surplus

    return {
        "resource_load_ratio": (e_resource / e_load) if e_load > 0 else 0.0,
        "surplus_load_ratio": (e_surplus / e_load) if e_load > 0 else 0.0,
        "served_upper_ratio": (e_served_upper / e_load) if e_load > 0 else 0.0,
        "self_consumption_upper": (e_served_upper / e_resource) if e_resource > 0 else 0.0,
    }


# ============================================================
# 可达性检查
# ============================================================
def check_feasibility_upper_bound(
    load_kw: np.ndarray,
    resource_kw: np.ndarray,
    dt_h: float,
    cfg: ResourceBESSConfig,
) -> dict[str, float]:
    """用极大容量测试物理上是否可达，返回最大消纳率和覆盖率。"""
    r_inf = simulate_dispatch(load_kw, resource_kw, dt_h, cap_kwh=1e9, cfg=cfg)
    return {
        "max_self_consumption": r_inf["metrics"]["self_consumption"],
        "max_load_coverage": r_inf["metrics"]["load_coverage"],
    }


# ============================================================
# 可行性判断
# ============================================================
def is_feasible(res: dict[str, Any], cfg: ResourceBESSConfig) -> bool:
    """检查仿真结果是否满足自用率和覆盖率阈值，以及期末 SOC 约束。"""
    m = res["metrics"]
    ok = (m["self_consumption"] >= cfg.min_self_consumption and
          m["load_coverage"] >= cfg.min_load_coverage)
    if "terminal_soc_ok" in res and (res["terminal_soc_ok"] is False):
        return False
    return ok


# ============================================================
# 二分搜索最小容量
# ============================================================
def find_min_capacity_bisect(
    load_kw: np.ndarray,
    resource_kw: np.ndarray,
    dt_h: float,
    cfg: ResourceBESSConfig,
) -> dict[str, Any]:
    """二分搜索最小可行容量。

    容量越大，能搬运的能量越多，覆盖率与自用率单调不减，因此可用二分。
    流程：
        1. 极大容量测物理可达性 → 不可达直接报错
        2. 倍增找可行上界
        3. 二分到 tol_mwh 精度
        4. 向上取整到 tol 网格
    """
    # 1. 可达性检查
    upper = check_feasibility_upper_bound(load_kw, resource_kw, dt_h, cfg)
    if upper["max_self_consumption"] < cfg.min_self_consumption or \
       upper["max_load_coverage"] < cfg.min_load_coverage:
        raise RuntimeError(
            f"目标在物理上不可达：\n"
            f"最大消纳率={upper['max_self_consumption']:.3f}, "
            f"最大负荷覆盖率={upper['max_load_coverage']:.3f}"
        )

    # 2. 倍增找可行上界
    lo = 0.0
    hi = 1.0
    best = None

    while hi <= cfg.cap_max_mwh * 1000.0 + 1e-9:
        res = simulate_dispatch(load_kw, resource_kw, dt_h, hi, cfg)
        if is_feasible(res, cfg):
            best = res
            break
        hi *= 2.0

    if best is None:
        raise RuntimeError(
            f"No feasible solution up to cap_max_mwh={cfg.cap_max_mwh}. "
            f"Try increasing cap_max_mwh or relaxing targets."
        )

    # 3. 二分搜索
    tol_kwh = cfg.tol_mwh * 1000.0
    while (hi - lo) > tol_kwh:
        mid = (lo + hi) / 2.0
        res = simulate_dispatch(load_kw, resource_kw, dt_h, mid, cfg)
        if is_feasible(res, cfg):
            best = res
            hi = mid
        else:
            lo = mid

    # 4. 容量取整（向上取到 tol_kwh）
    cap_final_kwh = float(np.ceil(hi / tol_kwh) * tol_kwh)
    best = simulate_dispatch(load_kw, resource_kw, dt_h, cap_final_kwh, cfg)
    best["cap_kwh"] = cap_final_kwh

    return best
