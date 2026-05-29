# -*- coding: utf-8 -*-

# ***************************************************
# * File        : storage_optim_Wind_BESS_combine.py
# * Author      : Zhefeng Wang
# * Email       : zfwang7@gmail.com
# * Date        : 2026-05-28
# * Version     : 1.0.0
# * Description : 整合 Wind+BESS 容量规划算法
#                 - enable_shift=False: 纯弃电搬运模式 (原 BESS_3)
#                 - enable_shift=True:  平移充电模式   (原 BESS_2)
# ***************************************************

# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import warnings
warnings.filterwarnings("ignore")
from typing import Optional, Dict, Any, Union

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from wind_pv_es_calc.storage_optim_common import (
    BESSConfig,
    Targets,
    Investment,
    ShiftPolicy,
    read_timeseries,
    align_and_merge,
)
from utils.plot_ts import plot_ele_series


# ============================================================
# 1) 快速"必要可行性"诊断
# ============================================================
def quick_feasibility_diagnose(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    bess: BESSConfig,
) -> Dict[str, float]:
    """
    给出几个关键上界/必要条件：
        - 能量比 wind/load
        - 可用于充电的"富余能量"比例 surplus/load
        - 理论最大 served 上界
    """
    e_load = float(load_kw.sum() * dt_h)
    e_wind = float(wind_kw.sum() * dt_h)
    direct = np.minimum(load_kw, wind_kw)
    surplus = np.maximum(wind_kw - load_kw, 0.0)
    e_direct = float(direct.sum() * dt_h)
    e_surplus = float(surplus.sum() * dt_h)
    eta_rt = bess.eta_charge * bess.eta_discharge

    e_served_upper = e_direct + eta_rt * e_surplus

    return {
        "wind_load_ratio": (e_wind / e_load) if e_load > 0 else 0.0,
        "surplus_load_ratio": (e_surplus / e_load) if e_load > 0 else 0.0,
        "served_upper_ratio": (e_served_upper / e_load) if e_load > 0 else 0.0,
        "green_self_upper": (e_served_upper / e_wind) if e_wind > 0 else 0.0,
    }


# ============================================================
# 2) 调度仿真 - 纯弃电搬运模式 (enable_shift=False)
# ============================================================
def _simulate_surplus_shift(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cap_mwh: float,
    bess: BESSConfig,
) -> Dict[str, Any]:
    """
    纯弃电搬运模式：
    - surplus (W > L): 充电
    - deficit (L > W): 放电
    - 无平移，无 lookahead
    """
    n = len(load_kw)
    cap_kwh = cap_mwh * 1000.0

    if cap_kwh <= 0:
        served = np.minimum(wind_kw, load_kw)
        curtail = np.maximum(wind_kw - served, 0.0)
        soc = np.full(n, bess.soc_init, dtype=float)
        charge = np.zeros(n, dtype=float)
        discharge = np.zeros(n, dtype=float)
        return _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_mwh)

    pmax = bess.c_rate * cap_kwh
    soc_min_e = bess.soc_min * cap_kwh
    soc_max_e = bess.soc_max * cap_kwh

    soc = np.zeros(n, dtype=float)
    charge = np.zeros(n, dtype=float)
    discharge = np.zeros(n, dtype=float)
    served = np.zeros(n, dtype=float)
    curtail = np.zeros(n, dtype=float)

    e = bess.soc_init * cap_kwh

    for t in range(n):
        L = float(max(0.0, load_kw[t]))
        W = float(max(0.0, wind_kw[t]))

        served_direct = min(L, W)
        surplus = max(W - L, 0.0)
        deficit = max(L - W, 0.0)

        room = max(soc_max_e - e, 0.0)
        avail = max(e - soc_min_e, 0.0)

        ch = min(surplus, pmax, room / (bess.eta_charge * dt_h))
        dis = min(deficit, pmax, avail * bess.eta_discharge / dt_h)

        e += (ch * bess.eta_charge - dis / bess.eta_discharge) * dt_h
        e = float(np.clip(e, soc_min_e, soc_max_e))

        charge[t] = ch
        discharge[t] = dis
        served[t] = served_direct + dis
        curtail[t] = max(surplus - ch, 0.0)
        soc[t] = e / cap_kwh if cap_kwh > 0 else bess.soc_init

    return _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_mwh)


# ============================================================
# 3) 调度仿真 - 平移充电模式 (enable_shift=True)
# ============================================================
def _simulate_shift(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cap_mwh: float,
    bess: BESSConfig,
    policy: ShiftPolicy,
) -> Dict[str, Any]:
    """
    平移充电模式：
    - 允许 Wind < Load 时抽取部分风电充电（通过 lookahead 预判未来缺口）
    - 禁止电网充电
    """
    n = len(load_kw)
    cap_kwh = cap_mwh * 1000.0

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

    e = bess.soc_init * cap_kwh
    soc_min_e = bess.soc_min * cap_kwh
    soc_max_e = bess.soc_max * cap_kwh

    look = max(1, int(policy.lookahead_steps))
    net = load_kw - wind_kw

    for t in range(n):
        L = float(max(0.0, load_kw[t]))
        W = float(max(0.0, wind_kw[t]))

        room = max(0.0, soc_max_e - e)
        avail = max(0.0, e - soc_min_e)

        ch_max = min(pmax, room / (bess.eta_charge * dt_h)) if room > 0 else 0.0
        dis_max_out = min(pmax, (avail * bess.eta_discharge) / dt_h) if avail > 0 else 0.0

        # 1) 判断是否平移充电（即使 Wind < Load）
        ch_plan = 0.0
        if ch_max > 0 and W > 0:
            t2 = min(n, t + look)
            future_def = float(np.maximum(net[t:t2], 0.0).sum())
            soc_ratio = e / cap_kwh
            if future_def > 0.5 * L * (t2 - t) and soc_ratio < 0.7:
                ch_plan = min(ch_max, policy.shift_max_frac_of_wind * W)

        # 2) 风电分配：先预留 ch_plan，再供负荷
        W_after_ch = max(0.0, W - ch_plan)
        serve_from_wind = min(L, W_after_ch)

        # 3) 电池放电补缺口
        deficit = L - serve_from_wind
        dis_out = min(dis_max_out, max(0.0, deficit))
        served_t = serve_from_wind + dis_out

        # 4) 富余风电继续充电
        surplus = max(0.0, W - serve_from_wind - ch_plan)
        ch_extra = min(max(0.0, ch_max - ch_plan), surplus)
        ch_in = ch_plan + ch_extra

        # 5) 弃电
        curtail_t = max(0.0, W - serve_from_wind - ch_in)

        # 6) 更新能量
        e += (ch_in * bess.eta_charge - dis_out / bess.eta_discharge) * dt_h
        e = float(np.clip(e, soc_min_e, soc_max_e))

        charge[t] = ch_in
        discharge[t] = dis_out
        served[t] = served_t
        curtail[t] = curtail_t
        soc[t] = e / cap_kwh

    # 期末 SOC 约束
    if bess.enforce_terminal_soc:
        if abs(soc[-1] - bess.soc_init) > 0.02:
            res = _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_mwh)
            res["terminal_soc_ok"] = False
            return res

    res = _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_mwh)
    res["terminal_soc_ok"] = True
    return res


# ============================================================
# 4) 统一调度仿真入口
# ============================================================
def simulate_dispatch(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cap_mwh: float,
    bess: BESSConfig,
    policy: ShiftPolicy,
) -> Dict[str, Any]:
    """
    统一调度仿真：
    - policy.enable_shift=True:  平移充电模式
    - policy.enable_shift=False: 纯弃电搬运模式
    """
    if policy.enable_shift:
        return _simulate_shift(load_kw, wind_kw, dt_h, cap_mwh, bess, policy)
    else:
        return _simulate_surplus_shift(load_kw, wind_kw, dt_h, cap_mwh, bess)


# ============================================================
# 5) 指标计算
# ============================================================
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
# 6) 可行性判断
# ============================================================
def is_feasible(res: Dict[str, Any], targets: Targets) -> bool:
    m = res["metrics"]
    ok = (m["green_self_consumption"] >= targets.min_green_self_consumption and
          m["load_coverage"] >= targets.min_load_coverage)
    if "terminal_soc_ok" in res and (res["terminal_soc_ok"] is False):
        return False
    return ok


# ============================================================
# 7) 二分搜索最小容量
# ============================================================
def find_min_capacity_bisect(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    targets: Targets,
    bess: BESSConfig,
    policy: ShiftPolicy,
    inv: Optional[Investment] = None,
    cap_max_mwh: float = 5000.0,
    tol_mwh: float = 0.1,
) -> Dict[str, Any]:
    """
    二分搜索最小可行容量。
    容量越大，能搬运的能量越多，覆盖率与自用率不会变差。
    """
    # 可达性检查：用极大容量测试物理上是否可达
    r_inf = simulate_dispatch(load_kw, wind_kw, dt_h, cap_mwh=1e6, bess=bess)
    if r_inf["metrics"]["green_self_consumption"] < targets.min_green_self_consumption or \
       r_inf["metrics"]["load_coverage"] < targets.min_load_coverage:
        raise RuntimeError(
            f"目标在物理上不可达：\n"
            f"最大风电消纳率={r_inf['metrics']['green_self_consumption']:.3f}, "
            f"最大负荷覆盖率={r_inf['metrics']['load_coverage']:.3f}"
        )

    # 先快速找可行上界
    lo = 0.0
    hi = 1.0
    best = None

    while hi <= cap_max_mwh + 1e-9:
        res = simulate_dispatch(load_kw, wind_kw, dt_h, hi, bess, policy)
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
        res = simulate_dispatch(load_kw, wind_kw, dt_h, mid, bess, policy)
        if is_feasible(res, targets):
            best = res
            hi = mid
        else:
            lo = mid

    # 容量取整（向上取到 tol_mwh）
    cap_final = float(np.ceil(hi / tol_mwh) * tol_mwh)
    best = simulate_dispatch(load_kw, wind_kw, dt_h, cap_final, bess, policy)

    # 投资计算（可选）
    if inv is not None:
        cap_kwh = cap_final * 1000.0
        best["investment"] = {
            "capex_cny_per_kwh": float(inv.capex_cny_per_kwh),
            "capacity_kwh": float(cap_kwh),
            "total_cost_cny": float(cap_kwh * inv.capex_cny_per_kwh),
        }

    best["cap_mwh"] = cap_final
    return best


# ============================================================
# 8) 主入口
# ============================================================
def run_wind_bess_planning(
    load_file: Union[str, pd.DataFrame],
    wind_file: Union[str, pd.DataFrame],
    out_schedule_csv: Optional[str] = None,
    freq: Optional[str] = None,
    load_col_kw: str = "P_kw",
    wind_col_mw: str = "WindPower_MW",
    enable_shift: bool = False,
    cap_max_mwh: float = 5000.0,
    tol_mwh: float = 0.1,
) -> Dict[str, Any]:
    """
    Wind+BESS 容量规划主入口。

    Args:
        load_file: 负荷数据文件或 DataFrame
        wind_file: 风电数据文件或 DataFrame
        out_schedule_csv: 调度策略输出 CSV 路径，None 则不输出
        freq: 指定频率，None 则自动推断
        load_col_kw: 负荷列名 (kW)
        wind_col_mw: 风电列名 (MW)
        enable_shift: 调度模式
            - False: 纯弃电搬运模式 (默认)
            - True:  平移充电模式
        cap_max_mwh: 最大搜索容量 (MWh)
        tol_mwh: 二分搜索精度 (MWh)

    Returns:
        dict: 包含以下字段:
            - dt_h: 时间步长 (h)
            - diagnosis: 可行性诊断
            - recommended_capacity_mwh: 推荐容量 (MWh)
            - investment: 投资信息 (仅 enable_shift=True 时)
            - metrics: 性能指标
            - energy_kwh: 能量统计
            - schedule_df: 调度策略 DataFrame
    """
    # 数据读取与对齐
    df_load = read_timeseries(load_file)
    df_wind = read_timeseries(wind_file)
    df, dt_h = align_and_merge(df_load, df_wind, load_col_kw, wind_col_mw, freq=freq)

    # 储能物理参数
    bess = BESSConfig(
        eta_charge=0.92,
        eta_discharge=0.92,
        soc_min=0.10,
        soc_max=1.00,
        soc_init=0.50,
        c_rate=1.0,
        enforce_terminal_soc=False,
    )

    # 优化约束目标
    targets = Targets(min_green_self_consumption=0.60, min_load_coverage=0.30)

    # 平移策略
    policy = ShiftPolicy(
        enable_shift=enable_shift,
        lookahead_steps=8,
        shift_max_frac_of_wind=0.30,
    )

    # 投资参数（仅平移模式使用）
    inv = Investment(capex_cny_per_kwh=1000.0) if enable_shift else None

    # 转 numpy
    load_kw = df["Load_kW"].to_numpy(dtype=float)
    wind_kw = df["Wind_kW"].to_numpy(dtype=float)

    # 快速诊断
    diag = quick_feasibility_diagnose(load_kw, wind_kw, dt_h, bess)

    # 二分求最小容量
    result = find_min_capacity_bisect(
        load_kw=load_kw,
        wind_kw=wind_kw,
        dt_h=dt_h,
        targets=targets,
        bess=bess,
        policy=policy,
        inv=inv,
        cap_max_mwh=cap_max_mwh,
        tol_mwh=tol_mwh,
    )

    # 输出策略时间序列
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
        index=df.index,
    )
    schedule.index.name = "Time"

    if out_schedule_csv:
        schedule.to_csv(out_schedule_csv, encoding="utf-8-sig")

    return {
        "dt_h": dt_h,
        "diagnosis": diag,
        "recommended_capacity_mwh": result["cap_mwh"],
        "investment": result.get("investment"),
        "metrics": result["metrics"],
        "energy_kwh": result["energy_kwh"],
        "schedule_df": schedule,
    }


# ============================================================
# 9) 月度统计
# ============================================================
def calc_monthly_wind_metrics(df, load_col="Load_kW", wind_col="Wind_kW"):
    """
    df: DatetimeIndex, 15min / 30min / 1h 均可
    返回：月度统计 DataFrame
    """
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("df.index 必须是 DatetimeIndex")

    df = df[[load_col, wind_col]].copy()

    dt_hours = (
        df.index.to_series().diff().dt.total_seconds().median() / 3600
    )

    df["used_kW"] = np.minimum(df[wind_col], df[load_col])
    df["wind_kWh"] = df[wind_col] * dt_hours
    df["load_kWh"] = df[load_col] * dt_hours
    df["used_kWh"] = df["used_kW"] * dt_hours

    monthly = df.resample("M").sum()

    monthly["curtail_kWh"] = monthly["wind_kWh"] - monthly["used_kWh"]
    monthly["wind_consumption_rate"] = monthly["used_kWh"] / monthly["wind_kWh"]
    monthly["load_coverage_rate"] = monthly["used_kWh"] / monthly["load_kWh"]

    result = monthly[[
        "wind_kWh", "used_kWh", "curtail_kWh", "load_kWh",
        "wind_consumption_rate", "load_coverage_rate",
    ]].copy()

    result.columns = [
        "风电发电量(kWh)", "风电消纳电量(kWh)", "弃电电量(kWh)",
        "用电量(kWh)", "风电消纳率", "负荷覆盖率",
    ]

    return result


# ============================================================
# 10) 绘制容量曲线
# ============================================================
def plot_capacity_curve(df, dt_h, bess, policy, cap_max_mwh=None, n_points=30):
    """
    绘制容量响应曲线：容量 vs 覆盖率/自用率
    """
    load_kw = df["Load_kW"].to_numpy(float)
    wind_kw = df["Wind_kW"].to_numpy(float)

    if cap_max_mwh is None:
        cap_max_mwh = 1.3 * 1400

    caps = np.unique(np.round(np.geomspace(1, cap_max_mwh, n_points), 1))
    caps = np.insert(caps, 0, 0.0)

    covs = []
    selfs = []
    for c in caps:
        r = simulate_dispatch(load_kw, wind_kw, dt_h, float(c), bess, policy)
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


# ============================================================
# 测试代码
# ============================================================
def main():
    # ------------------------------
    # 负荷数据
    # ------------------------------
    from wind_pv_es_calc.eva_PV_optim_version.data_loader import load_data
    energy_data_path = Path("data/wind_pv_es_calc/temp/df_2025.csv")
    df_2025 = load_data(energy_data_path=energy_data_path)
    df_2025["P_kw"] = df_2025["P_kw"] / 704234268 * 685436401
    print("负荷数据:")
    print(df_2025)

    # ------------------------------
    # 风电数据
    # ------------------------------
    from wind_pv_es_calc.eva_PV_optim_version.data_wind_simu import generate_wind_data
    wind_data_path = Path("data/wind_pv_es_calc/temp/df_wind_2025.csv")
    df_wind = generate_wind_data(
        farm_capacity_mw=110.0,
        mean_wind_speed_140m=5.5,
        eq_full_load_hours=1920.7,
        lat=28.42,
        lon=117.88,
        wind_data_path=wind_data_path,
    )
    print("\n风电数据:")
    print(df_wind)

    # ------------------------------
    # 模式 1: 纯弃电搬运 (默认)
    # ------------------------------
    print("\n" + "=" * 50)
    print("模式 1: 纯弃电搬运 (enable_shift=False)")
    print("=" * 50)
    res1 = run_wind_bess_planning(
        load_file=df_2025,
        wind_file=df_wind,
        out_schedule_csv="data/wind_pv_es_calc/temp/bess_schedule_shift_off.csv",
        enable_shift=False,
        cap_max_mwh=5000.0,
        tol_mwh=0.1,
    )
    print(f"dt_h: {res1['dt_h']}")
    print(f"Diagnosis: {res1['diagnosis']}")
    print(f"Capacity (MWh): {res1['recommended_capacity_mwh']}")
    print(f"Green self-consumption: {res1['metrics']['green_self_consumption']}")
    print(f"Load coverage: {res1['metrics']['load_coverage']}")

    # ------------------------------
    # 模式 2: 平移充电
    # ------------------------------
    print("\n" + "=" * 50)
    print("模式 2: 平移充电 (enable_shift=True)")
    print("=" * 50)
    res2 = run_wind_bess_planning(
        load_file=df_2025,
        wind_file=df_wind,
        out_schedule_csv="data/wind_pv_es_calc/temp/bess_schedule_shift_on.csv",
        enable_shift=True,
        cap_max_mwh=5000.0,
        tol_mwh=0.1,
    )
    print(f"dt_h: {res2['dt_h']}")
    print(f"Diagnosis: {res2['diagnosis']}")
    print(f"Capacity (MWh): {res2['recommended_capacity_mwh']}")
    print(f"Investment (CNY): {res2['investment']['total_cost_cny']}")
    print(f"Green self-consumption: {res2['metrics']['green_self_consumption']}")
    print(f"Load coverage: {res2['metrics']['load_coverage']}")


if __name__ == "__main__":
    main()
