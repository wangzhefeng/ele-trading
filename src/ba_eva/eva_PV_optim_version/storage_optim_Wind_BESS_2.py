# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim3.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-04-20
# * Version     : 1.0.042018
# * Description : description
# * Link        : link
# * Requirement : 相关模块版本需求(例如: numpy >= 2.1.0)
# ***************************************************

# python libraries
import os
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, Union

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from ba_eva.eva_PV_optim_version.storage_optim_common import (
    njit, NUMBA_OK,
    BESSConfig, Targets, UnitsConfig, PlanConfigFast,
    infer_dt_hours, normalize_time_and_load, as_time_series, align_to_time,
    dispatch_numba, evaluate,
    read_timeseries, align_and_merge, dbg
)

# global variable
LOGGING_LABEL = Path(__file__).name[:-3]
os.environ['LOG_NAME'] = LOGGING_LABEL
# from utils.log_util import logger


# ============================================================
# 主入口
# ============================================================
def plan_energy_system(
    df_load: pd.DataFrame,
    *,
    pv_unit_kw: Optional[Union[pd.Series, pd.DataFrame]] = None,
    wind_input: Optional[Union[pd.Series, pd.DataFrame]] = None,
    time_col: str = "Time",
    load_col: str = "P_kw",
    cfg: PlanConfigFast = PlanConfigFast(),
    units: UnitsConfig = UnitsConfig(),
) -> Dict[str, Any]:
    try:
        dbg("ENTER plan_energy_system df_load", df_load)
        t, load_kw, warn = normalize_time_and_load(df_load, time_col, load_col, units)
        dt = infer_dt_hours(t)
    except Exception as e:
        return {
            "feasible": False,
            "diagnosis": {"stage": "load", "msg": str(e)}
        }

    wind_kw = np.zeros_like(load_kw)
    if wind_input is not None:
        try:
            w = as_time_series(
                wind_input, time_col,
                ("WindPower_MW", "wind_mw", "wind_kw"),
                1000.0 if units.wind_power.lower() == "mw" else 1.0
            )
            wind_kw = align_to_time(t, w)
        except Exception as e:
            return {"feasible": False, "diagnosis": {"stage": "wind", "msg": str(e)}}

    pv_kw = np.zeros_like(load_kw)
    if pv_unit_kw is not None:
        try:
            p = as_time_series(
                pv_unit_kw, time_col,
                ("pv_unit_kw", "pv_kw", "value"),
                1.0
            )
            pv_kw = align_to_time(t, p)
        except Exception as e:
            return {"feasible": False, "diagnosis": {"stage": "pv", "msg": str(e)}}

    gen_kw = wind_kw + pv_kw

    if pv_unit_kw is None and wind_input is None:
        return {
            "feasible": False,
            "diagnosis": {
                "reason": "NO_GENERATION",
                "msg": "无 PV / 无风，仅储能不能创造能量"
            }
        }

    best = None
    for batt in np.linspace(0, cfg.batt_hi_max_kwh, 40):
        stats = evaluate(load_kw, gen_kw, dt, batt, cfg)
        if (
            stats["self_use_ratio"] >= cfg.self_use_ratio_min and
            stats["load_cover_ratio"] >= cfg.load_cover_ratio_min
        ):
            cost = batt * cfg.bess_capex_yuan_per_kwh
            if best is None or cost < best["cost"]:
                best = {"bess_kwh": batt, "metrics": stats, "cost": cost}

    if best is None:
        return {
            "feasible": False,
            "diagnosis": {
                "reason": "NO_FEASIBLE_SOLUTION",
                "self_use_ratio_min": cfg.self_use_ratio_min,
                "load_cover_ratio_min": cfg.load_cover_ratio_min,
            }
        }

    return {
        "feasible": True,
        "solution": best,
        "warnings": warn,
        "context": {
            "dt_hours": dt,
            "engine": "numba" if (cfg.use_numba and NUMBA_OK) else "python"
        }
    }


# ============================================================
# 1) 配置（BESSConfig / Targets 已提取至 storage_optim_common）
# ============================================================
@dataclass
class Investment:
    capex_cny_per_kwh: float = 1000.0


@dataclass
class ShiftPolicy:
    """
    允许在 Wind < Load 时也能充电平移（通过牺牲当期供电来换未来供电）。
    - enable_shift: 是否开启
    - lookahead_steps: 未来窗口长度（用于判断是否“值得”储能平移）
    - shift_max_frac_of_wind: 当 Wind<Load 时，最多拿走 wind 的多少比例去充电
    说明：
      这会减少当期 served，但可能提升未来某些时段的 served（更符合“平移”直觉）。
      若你只关心总覆盖率/自用率，理论上它不一定更优，但你明确要求支持该行为。
    """
    enable_shift: bool = True
    lookahead_steps: int = 8
    shift_max_frac_of_wind: float = 0.30


# ============================================================
# 3) 快速”必要可行性”诊断（避免无解时慢扫容量）
# ============================================================
def quick_feasibility_diagnose(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    targets: Targets,
    bess: BESSConfig,
) -> Dict[str, float]:
    """
    给出几个关键上界/必要条件：
    - 能量比 wind/load
    - 可用于充电的“富余能量”比例 surplus/load
    - 在“只能用富余充电”的前提下，理论最大 served 上界：direct + eta_rt*surplus
    """
    e_load = float(load_kw.sum() * dt_h)
    e_wind = float(wind_kw.sum() * dt_h)

    direct = np.minimum(load_kw, wind_kw)
    surplus = np.maximum(wind_kw - load_kw, 0.0)

    e_direct = float(direct.sum() * dt_h)
    e_surplus = float(surplus.sum() * dt_h)

    eta_rt = bess.eta_charge * bess.eta_discharge

    # “只用富余充电”的理论 served 上界
    e_served_upper_surplus_only = e_direct + eta_rt * e_surplus

    return {
        "wind_load_ratio": (e_wind / e_load) if e_load > 0 else 0.0,
        "surplus_load_ratio": (e_surplus / e_load) if e_load > 0 else 0.0,
        "served_upper_surplus_only_ratio": (e_served_upper_surplus_only / e_load) if e_load > 0 else 0.0,
        "green_self_upper_surplus_only": (e_served_upper_surplus_only / e_wind) if e_wind > 0 else 0.0,
    }


# ============================================================
# 4) 调度仿真（核心：支持“Wind<Load 也可充电平移”，且禁止电网充电）
# ============================================================
def simulate_dispatch_offgrid_shiftable(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cap_mwh: float,
    bess: BESSConfig,
    policy: ShiftPolicy,
) -> Dict[str, Any]:
    """
    决策逻辑（快仿真）：
      - 每步风电 W：
          分为：供负荷 + 充电 + 弃电
        电池放电仅用于供负荷，不允许外部电网充电
      - 允许 Wind < Load 时拿走一部分 wind 去充电（平移），导致当期供电下降
        触发条件：未来 lookahead 窗口内存在明显“缺口压力”，且 SOC 有空间
    """
    n = len(load_kw)
    cap_kwh = cap_mwh * 1000.0

    # 无储能：直接用风供负荷
    if cap_kwh <= 0:
        served = np.minimum(wind_kw, load_kw)
        curtail = np.maximum(wind_kw - served, 0.0)
        soc = np.full(n, bess.soc_init, dtype=float)
        charge = np.zeros(n, dtype=float)
        discharge = np.zeros(n, dtype=float)
        return _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_mwh)

    pmax = bess.c_rate * cap_kwh

    soc = np.zeros(n, dtype=float)
    charge = np.zeros(n, dtype=float)
    discharge = np.zeros(n, dtype=float)
    served = np.zeros(n, dtype=float)
    curtail = np.zeros(n, dtype=float)

    e = bess.soc_init * cap_kwh  # 用 kWh 表示能量状态

    soc_min_e = bess.soc_min * cap_kwh
    soc_max_e = bess.soc_max * cap_kwh

    look = max(1, int(policy.lookahead_steps))

    # 预计算“净负荷压力”用于触发平移：net = load - wind
    net = load_kw - wind_kw

    for t in range(n):
        L = float(max(0.0, load_kw[t]))
        W = float(max(0.0, wind_kw[t]))

        # 可充电空间、可放电能量
        room = max(0.0, soc_max_e - e)
        avail = max(0.0, e - soc_min_e)

        # 功率上限（kW）
        ch_max = min(pmax, room / (bess.eta_charge * dt_h)) if room > 0 else 0.0
        dis_max_out = min(pmax, (avail * bess.eta_discharge) / dt_h) if avail > 0 else 0.0

        # ===== 1) 先决定是否“平移充电”（即使 Wind<Load） =====
        ch_plan = 0.0
        if policy.enable_shift and ch_max > 0 and W > 0:
            # 未来缺口压力（只看未来 net>0 的总量）
            t2 = min(n, t + look)
            future_def = float(np.maximum(net[t:t2], 0.0).sum())

            # 若未来缺口明显，且当前 SOC 低于中位水平，则允许抽取部分风去充电
            # 这里用一个简洁触发：future_def 大于当前负荷的若干倍
            soc_ratio = e / cap_kwh
            if future_def > 0.5 * L * (t2 - t) and soc_ratio < 0.7:
                # 抽取 wind 的一部分去充电（但不能超过 ch_max）
                ch_plan = min(ch_max, policy.shift_max_frac_of_wind * W)

        # ===== 2) 风电分配：先预留 ch_plan，然后用剩余 wind 供负荷 =====
        W_after_ch = max(0.0, W - ch_plan)
        serve_from_wind = min(L, W_after_ch)

        # ===== 3) 若风电仍不足，电池放电补缺口 =====
        deficit = L - serve_from_wind
        dis_out = min(dis_max_out, max(0.0, deficit))

        served_t = serve_from_wind + dis_out

        # ===== 4) 充电：除了 ch_plan，若有富余 wind，也可继续充电 =====
        # 富余 wind = W - serve_from_wind - ch_plan （注意 serve_from_wind 用的是 W_after_ch）
        # 实际剩余 = W - serve_from_wind - ch_plan
        surplus = max(0.0, W - serve_from_wind - ch_plan)

        ch_extra = min(max(0.0, ch_max - ch_plan), surplus)
        ch_in = ch_plan + ch_extra  # 总充电功率（来自风电，不允许电网）

        # ===== 5) 弃电 =====
        curtail_t = max(0.0, W - serve_from_wind - ch_in)

        # ===== 6) 更新能量 e（kWh） =====
        e += (ch_in * bess.eta_charge - dis_out / bess.eta_discharge) * dt_h
        e = float(np.clip(e, soc_min_e, soc_max_e))

        charge[t] = ch_in
        discharge[t] = dis_out
        served[t] = served_t
        curtail[t] = curtail_t
        soc[t] = e / cap_kwh

    # ===== 期末 SOC 约束（快速近似修正：不满足则判为更严格的“不可行”）=====
    if bess.enforce_terminal_soc:
        if abs(soc[-1] - bess.soc_init) > 0.02:  # 允许小偏差
            # 这种情况下，当前容量在“闭环运行”意义下偏乐观
            # 直接标记为不满足（便于二分搜索收敛到更大容量）
            res = _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_mwh)
            res["terminal_soc_ok"] = False
            return res

    res = _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_mwh)
    res["terminal_soc_ok"] = True
    return res


def _post_metrics(
    served_kw: np.ndarray,
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    charge_kw: np.ndarray,
    discharge_kw: np.ndarray,
    soc: np.ndarray,
    curtail_kw: np.ndarray,
    dt_h: float,
    cap_mwh: float,
) -> Dict[str, Any]:
    e_load = float(load_kw.sum() * dt_h)
    e_wind = float(wind_kw.sum() * dt_h)
    e_served = float(served_kw.sum() * dt_h)
    e_curtail = float(curtail_kw.sum() * dt_h)

    green_self = (e_served / e_wind) if e_wind > 0 else 0.0
    coverage = (e_served / e_load) if e_load > 0 else 0.0

    cap_kwh = cap_mwh * 1000.0
    e_dis = float(discharge_kw.sum() * dt_h)
    equiv_cycles = (e_dis / cap_kwh) if cap_kwh > 0 else 0.0

    return {
        "cap_mwh": float(cap_mwh),
        "energy_kwh": {
            "load": e_load,
            "wind": e_wind,
            "served": e_served,
            "curtail": e_curtail,
            "charge_in": float(charge_kw.sum() * dt_h),
            "discharge_out": float(discharge_kw.sum() * dt_h),
        },
        "metrics": {
            "green_self_consumption": float(green_self),
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
# 5) 最小投资（最小容量）求解：二分搜索（速度提升关键）
# ============================================================
def is_feasible(res: Dict[str, Any], targets: Targets) -> bool:
    m = res["metrics"]
    ok = (m["green_self_consumption"] >= targets.min_green_self_consumption and
          m["load_coverage"] >= targets.min_load_coverage)
    # 若启用期末 SOC 闭环要求，则必须 ok
    if "terminal_soc_ok" in res and (res["terminal_soc_ok"] is False):
        return False
    return ok


def find_min_capacity_bisect(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    targets: Targets,
    bess: BESSConfig,
    inv: Investment,
    policy: ShiftPolicy,
    cap_max_mwh: float = 2000.0,
    tol_mwh: float = 0.1,
) -> Dict[str, Any]:
    """
    单调性说明（工程上成立）：
      容量越大，能搬运的能量越多，覆盖率与自用率不会变差（在我们这个策略下基本单调）。
    因此可用二分搜索最小可行容量。
    """

    # 先快速找一个可行上界
    lo = 0.0
    hi = 1.0
    best = None

    while hi <= cap_max_mwh + 1e-9:
        res = simulate_dispatch_offgrid_shiftable(load_kw, wind_kw, dt_h, hi, bess, policy)
        if is_feasible(res, targets):
            best = res
            break
        hi *= 2.0

    if best is None:
        raise RuntimeError(
            f"No feasible solution up to cap_max_mwh={cap_max_mwh}. "
            f"Try increasing cap_max_mwh or relaxing targets."
        )

    # 二分搜索
    while (hi - lo) > tol_mwh:
        mid = (lo + hi) / 2.0
        res = simulate_dispatch_offgrid_shiftable(load_kw, wind_kw, dt_h, mid, bess, policy)
        if is_feasible(res, targets):
            best = res
            hi = mid
        else:
            lo = mid

    # 容量取整（向上取到 tol_mwh）
    cap_final = float(np.ceil(hi / tol_mwh) * tol_mwh)
    best = simulate_dispatch_offgrid_shiftable(load_kw, wind_kw, dt_h, cap_final, bess, policy)

    # 投资
    cap_kwh = cap_final * 1000.0
    best["investment"] = {
        "capex_cny_per_kwh": float(inv.capex_cny_per_kwh),
        "capacity_kwh": float(cap_kwh),
        "total_cost_cny": float(cap_kwh * inv.capex_cny_per_kwh),
    }
    best["cap_mwh"] = cap_final
    return best


# ============================================================
# 6) 主入口：DataFrame/文件 -> 对齐 -> 可行性诊断 -> 二分求最小投资 -> 输出策略
# ============================================================
def run_planning_min_investment(
    load_file: Union[str, pd.DataFrame],
    wind_file: Union[str, pd.DataFrame],
    out_schedule_csv: Optional[str] = "bess_schedule.csv",
    freq: Optional[str] = None,
    load_col_kw: str = "P_kw",
    wind_col_mw: str = "WindPower_MW",
    cap_max_mwh: float = 2000.0,
    tol_mwh: float = 0.1,
) -> Dict[str, Any]:
    df_load = read_timeseries(load_file)
    df_wind = read_timeseries(wind_file)

    df, dt_h = align_and_merge(df_load, df_wind, load_col_kw, wind_col_mw, freq=freq)

    bess = BESSConfig(
        eta_charge=0.92,
        eta_discharge=0.92,
        soc_min=0.10,
        soc_max=1.00,
        soc_init=0.50,
        c_rate=1.0,
        enforce_terminal_soc=False,
    )
    targets = Targets(min_green_self_consumption=0.60, min_load_coverage=0.30)
    inv = Investment(capex_cny_per_kwh=1000.0)
    policy = ShiftPolicy(enable_shift=True, lookahead_steps=8, shift_max_frac_of_wind=0.30)

    load_kw = df["Load_kW"].to_numpy(dtype=float)
    wind_kw = df["Wind_kW"].to_numpy(dtype=float)

    # ---------- 快速诊断：避免无解时慢算 ----------
    diag = quick_feasibility_diagnose(load_kw, wind_kw, dt_h, targets, bess)

    # 这里不直接判死刑，因为你要求“Wind<Load 也能平移充电”，它可能放宽“只靠富余充电”的限制
    # 但我们至少把诊断结果给出，便于你判断“为什么难/慢”
    # 如果你希望严格提前判无解，可加更强的必要条件判断。

    # ---------- 二分求最小容量 ----------
    result = find_min_capacity_bisect(
        load_kw=load_kw,
        wind_kw=wind_kw,
        dt_h=dt_h,
        targets=targets,
        bess=bess,
        inv=inv,
        policy=policy,
        cap_max_mwh=cap_max_mwh,
        tol_mwh=tol_mwh,
    )

    # ---------- 输出策略时间序列 ----------
    s = result["series"]
    schedule = pd.DataFrame(
        {
            "Load_kW": load_kw,
            "Wind_kW": wind_kw,
            "Served_kW": s["served_kw"],
            "Charge_kW": s["charge_kw"],
            "Discharge_kW": s["discharge_kw"],
            "SOC": s["soc"],
            "Curtail_kW": s["curtail_kw"],
        },
        index=df.index
    )
    schedule.index.name = "Time"

    if out_schedule_csv:
        schedule.to_csv(out_schedule_csv, encoding="utf-8-sig")

    return {
        "dt_h": dt_h,
        "diagnosis": diag,
        "recommended_capacity_mwh": result["cap_mwh"],
        "investment": result["investment"],
        "metrics": result["metrics"],
        "energy_kwh": result["energy_kwh"],
        "schedule_df": schedule,
    }


# ------------------------------
#
# ------------------------------
def calc_monthly_wind_metrics(
    df,
    load_col="Load_kW",
    wind_col="Wind_kW",
):
    """
    df: DatetimeIndex, 15min / 30min / 1h 均可
    返回：月度统计 DataFrame
    """

    # ===============================
    # 1. 基础检查
    # ===============================
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df.index 必须是 DatetimeIndex")

    df = df[[load_col, wind_col]].copy()

    # ===============================
    # 2. 时间步长（小时）
    # ===============================
    dt_hours = (
        df.index.to_series().diff().dt.total_seconds().median() / 3600
    )

    # ===============================
    # 3. 逐时刻消纳功率
    # ===============================
    df["used_kW"] = np.minimum(df[wind_col], df[load_col])

    # ===============================
    # 4. 转为电量（kWh）
    # ===============================
    df["wind_kWh"] = df[wind_col] * dt_hours
    df["load_kWh"] = df[load_col] * dt_hours
    df["used_kWh"] = df["used_kW"] * dt_hours

    # ===============================
    # 5. 按月汇总
    # ===============================
    monthly = df.resample("M").sum()

    monthly["curtail_kWh"] = (
        monthly["wind_kWh"] - monthly["used_kWh"]
    )

    monthly["wind_consumption_rate"] = (
        monthly["used_kWh"] / monthly["wind_kWh"]
    )

    monthly["load_coverage_rate"] = (
        monthly["used_kWh"] / monthly["load_kWh"]
    )

    # ===============================
    # 6. 输出整理
    # ===============================
    result = monthly[[
        "wind_kWh",
        "used_kWh",
        "curtail_kWh",
        "load_kWh",
        "wind_consumption_rate",
        "load_coverage_rate",
    ]].copy()

    result.columns = [
        "风电发电量(kWh)",
        "风电消纳电量(kWh)",
        "弃电电量(kWh)",
        "用电量(kWh)",
        "风电消纳率",
        "负荷覆盖率",
    ]

    return result


# ------------------------------
# 
# ------------------------------
# 用你现有的仿真函数：simulate_dispatch_offgrid_shiftable
# 如果你用的是我给你的那版代码，函数名就是 simulate_dispatch_offgrid_shiftable
def plot_capacity_curve(df, dt_h, bess, policy,
                       cap_max_mwh=None, n_points=30):
    load_kw = df["Load_kW"].to_numpy(float)
    wind_kw = df["Wind_kW"].to_numpy(float)

    # 上限默认用你求出来的容量的 1.3 倍
    if cap_max_mwh is None:
        cap_max_mwh = 1.3 * 1400  # 你这里大约 1377，可自行改

    # 取“对数更密集”的采样点，减少计算量但更能看陡峭区
    caps = np.unique(np.round(np.geomspace(1, cap_max_mwh, n_points), 1))
    caps = np.insert(caps, 0, 0.0)

    covs = []
    selfs = []
    for c in caps:
        r = simulate_dispatch_offgrid_shiftable(load_kw, wind_kw, dt_h, float(c), bess, policy)
        covs.append(r["metrics"]["load_coverage"])
        selfs.append(r["metrics"]["green_self_consumption"])

    plt.figure()
    plt.plot(caps, covs, marker="o", label="Load coverage")
    plt.plot(caps, selfs, marker="o", label="Green self-consumption")
    plt.axhline(0.30, linestyle="--")
    plt.axhline(0.60, linestyle="--")
    plt.xlabel("Capacity (MWh)")
    plt.ylabel("Ratio")
    plt.title("Capacity vs Coverage / Self-consumption")
    plt.legend()
    plt.grid(True)
    plt.show()

# 示例调用：
# plot_capacity_curve(df, dt_h, bess, policy, cap_max_mwh=2000, n_points=30)


# 测试代码 main 函数
def main():
    # ##############################
    # 风光储最佳组合测算
    # ##############################
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("src/ba_eva/dataset/temp/df_2025.csv")
    df_2025 = load_data(energy_data_path=energy_data_path)
    df_2025["P_kw"] = df_2025["P_kw"] / 704234268 * 685436401
    print(df_2025)
    # ------------------------------
    # wind power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_wind_simu import generate_wind_data
    wind_data_path = Path("src/ba_eva/dataset/temp/df_wind_2026.csv")
    df_wind = generate_wind_data(
        farm_capacity_mw=110.0, 
        mean_wind_speed_140m=5.5, 
        eq_full_load_hours=1920.7, 
        lat=28.42, 
        lon=117.88, 
        wind_data_path=wind_data_path
    )
    print(df_wind)
    # ------------------------------
    # PV(Photo Voltaics) power data
    # ------------------------------
    from ba_eva.eva_PV_optim_version.data_pv_simu import generate_pv_data
    pv_data_path = Path("src/ba_eva/dataset/temp/df_pv_2025.csv")
    pv_kw_28 = generate_pv_data(
        df=df_2025, 
        lat=28.42, 
        lon=117.88, 
        capacity_kwp=28250, 
        pv_data_path=pv_data_path, 
        plot_img=False
    )
    print(pv_kw_28)
    # ------------------------------
    # TODO
    # ------------------------------
    cfg = PlanConfigFast(
        batt_hi_max_kwh=20000.0,
    )
    res = plan_energy_system(
        df_load=df_2025,
        wind_input=df_wind,
        time_col="Time",
        load_col="P_kw",
        cfg=cfg,
    )
    # ------------------------------
    # TODO
    # ------------------------------
    res = run_planning_min_investment(
        load_file=df_2025,   # DataFrame: Time + P_kw
        wind_file=df_wind,   # DataFrame: Time(index或列) + WindPower_MW
        out_schedule_csv="src/ba_eva/dataset/temp/bess_schedule.csv",
        freq=None,           # 或指定 "15min"
        cap_max_mwh=5000.0,  # 如果确实需要更大再加
        tol_mwh=0.1,
    )
    print("dt_h:", res["dt_h"])
    print("Diagnosis:", res["diagnosis"])
    print("Capacity (MWh):", res["recommended_capacity_mwh"])
    print("Investment (CNY):", res["investment"]["total_cost_cny"])
    print("Green self-consumption:", res["metrics"]["green_self_consumption"])
    print("Load coverage:", res["metrics"]["load_coverage"])
    # ------------------------------
    # 
    # ------------------------------
    df, dt_h = align_and_merge(df_2025, df_wind)
    df.to_csv("src/ba_eva/dataset/temp/bess_load_wind.csv", encoding="utf-8-sig")
    print(df)
    # ------------------------------
    # 
    # ------------------------------
    monthly_metrics = calc_monthly_wind_metrics(df)
    print(monthly_metrics.round(4))
    monthly_metrics.to_csv("src/ba_eva/dataset/temp/dataset/bess_monthly_metrics.csv", encoding="utf-8-sig")

    # ===============================
    # 1. 确保 Time 是 DatetimeIndex
    # ===============================
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    # ===============================
    # 2. 提取 2025 年 10 月数据
    # ===============================
    df_oct = df.loc["2025-10"]
    print("10 月数据形状:", df_oct.shape)
    print(df_oct.head())
    # ===============================
    # 3. 画图
    # ===============================
    plt.figure(figsize=(16, 6))
    plt.plot(
        df_oct.index,
        df_oct["Load_kW"],
        label="负荷 Load (kW)",
        linewidth=1.5,
    )
    plt.plot(
        df_oct.index,
        df_oct["Wind_kW"],
        label="风电 Wind (kW)",
        linewidth=1.2,
        alpha=0.85,
    )
    plt.title("2025 年 10 月负荷与风电出力对比（15min）")
    plt.xlabel("时间")
    plt.ylabel("功率 (kW)")
    plt.legend(loc="upper right")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.show()
    
    
    # ------------------------------
    # 
    # ------------------------------
    df_2025_ = df_2025.copy()
    df_2025_['P_kw'] = df_2025_['P_kw'] * 6.85 / 7.04
    df, dt_h = align_and_merge(df_2025, df_wind)
    print(df_2025)
    # ------------------------------
    # 
    # ------------------------------
    # 2. 固定电池与策略参数
    bess = BESSConfig(
        eta_charge=0.92,
        eta_discharge=0.92,
        soc_min=0.10,
        soc_max=1.00,
        soc_init=0.50,
        c_rate=1.0,
        enforce_terminal_soc=False,
    )
    policy = ShiftPolicy(
        enable_shift=True,
        lookahead_steps=8,
        shift_max_frac_of_wind=0.30,
    )
    # 3. 画“容量响应曲线”
    plot_capacity_curve(
        df=df,
        dt_h=dt_h,
        bess=bess,
        policy=policy,
        cap_max_mwh=1600,
        n_points=30,
    )

if __name__ == "__main__":
    main()
