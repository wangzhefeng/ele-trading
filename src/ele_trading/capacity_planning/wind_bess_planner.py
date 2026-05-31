"""Wind+BESS 容量规划模块

支持两种调度模式：
- 纯弃电搬运模式 (enable_shift=False): 只用 surplus 充电，deficit 放电
- 平移充电模式 (enable_shift=True): 允许 Wind<Load 时平移充电

采用二分搜索算法，比线性搜索更快。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ele_trading.utils.data_alignment import align_and_merge, ensure_datetime_index


# ============================================================
# 配置数据类
# ============================================================
@dataclass(slots=True)
class ShiftPolicy:
    """平移充电策略配置。"""
    enable_shift: bool = False
    lookahead_steps: int = 8
    shift_max_frac_of_wind: float = 0.30


@dataclass(slots=True)
class WindBESSPlanConfig:
    """Wind+BESS 容量规划配置。"""
    # 储能物理参数
    eta_charge: float = 0.92
    eta_discharge: float = 0.92
    c_rate: float = 1.0
    soc_init: float = 0.50
    soc_min: float = 0.10
    soc_max: float = 1.00
    enforce_terminal_soc: bool = False
    # 约束阈值
    min_green_self_consumption: float = 0.60
    min_load_coverage: float = 0.30
    # 成本
    capex_cny_per_kwh: float = 1000.0
    # 搜索参数
    cap_max_mwh: float = 5000.0
    tol_mwh: float = 0.1
    # 策略
    shift_policy: ShiftPolicy = field(default_factory=ShiftPolicy)


@dataclass(slots=True)
class WindBESSResult:
    """Wind+BESS 容量规划结果。"""
    feasible: bool
    capacity_mwh: float = 0.0
    cost_cny: float = 0.0
    green_self_consumption: float = 0.0
    load_coverage: float = 0.0
    equiv_cycles: float = 0.0
    energy_kwh: dict = field(default_factory=dict)
    schedule_df: pd.DataFrame | None = None
    diagnosis: dict | None = None


# ============================================================
# 调度仿真 - 纯弃电搬运模式
# ============================================================
def _simulate_surplus_shift(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
    cfg: WindBESSPlanConfig,
) -> dict[str, Any]:
    """
    纯弃电搬运模式：
    - surplus (W > L): 充电
    - deficit (L > W): 放电
    - 无平移，无 lookahead
    """
    n = len(load_kw)

    if cap_kwh <= 0:
        served = np.minimum(wind_kw, load_kw)
        curtail = np.maximum(wind_kw - served, 0.0)
        soc = np.full(n, cfg.soc_init, dtype=float)
        charge = np.zeros(n, dtype=float)
        discharge = np.zeros(n, dtype=float)
        return _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)

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
        W = float(max(0.0, wind_kw[t]))

        served_direct = min(L, W)
        surplus = max(W - L, 0.0)
        deficit = max(L - W, 0.0)

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

    return _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)


# ============================================================
# 调度仿真 - 平移充电模式
# ============================================================
def _simulate_shift(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
    cfg: WindBESSPlanConfig,
    policy: ShiftPolicy,
) -> dict[str, Any]:
    """
    平移充电模式：
    - 允许 Wind < Load 时抽取部分风电充电（通过 lookahead 预判未来缺口）
    - 禁止电网充电
    """
    n = len(load_kw)

    if cap_kwh <= 0:
        served = np.minimum(wind_kw, load_kw)
        curtail = np.maximum(wind_kw - served, 0.0)
        soc = np.full(n, cfg.soc_init, dtype=float)
        charge = np.zeros(n, dtype=float)
        discharge = np.zeros(n, dtype=float)
        return _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)

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
    net = load_kw - wind_kw

    for t in range(n):
        L = float(max(0.0, load_kw[t]))
        W = float(max(0.0, wind_kw[t]))

        room = max(0.0, soc_max_e - e)
        avail = max(0.0, e - soc_min_e)

        ch_max = min(pmax, room / (cfg.eta_charge * dt_h)) if room > 0 else 0.0
        dis_max_out = min(pmax, (avail * cfg.eta_discharge) / dt_h) if avail > 0 else 0.0

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
            res = _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)
            res["terminal_soc_ok"] = False
            return res

    res = _post_metrics(served, load_kw, wind_kw, charge, discharge, soc, curtail, dt_h, cap_kwh)
    res["terminal_soc_ok"] = True
    return res


# ============================================================
# 统一调度仿真入口
# ============================================================
def simulate_dispatch(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
    cfg: WindBESSPlanConfig,
) -> dict[str, Any]:
    """
    统一调度仿真：
    - cfg.shift_policy.enable_shift=True:  平移充电模式
    - cfg.shift_policy.enable_shift=False: 纯弃电搬运模式
    """
    if cfg.shift_policy.enable_shift:
        return _simulate_shift(load_kw, wind_kw, dt_h, cap_kwh, cfg, cfg.shift_policy)
    else:
        return _simulate_surplus_shift(load_kw, wind_kw, dt_h, cap_kwh, cfg)


# ============================================================
# 指标计算
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
    cap_kwh: float,
) -> dict[str, Any]:
    e_load = float(load_kw.sum() * dt_h)
    e_wind = float(wind_kw.sum() * dt_h)
    e_served = float(served_kw.sum() * dt_h)
    e_curtail = float(curtail_kw.sum() * dt_h)

    green_self = (e_served / e_wind) if e_wind > 0 else 0.0
    coverage = (e_served / e_load) if e_load > 0 else 0.0

    e_dis = float(discharge_kw.sum() * dt_h)
    equiv_cycles = (e_dis / cap_kwh) if cap_kwh > 0 else 0.0

    return {
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
# 快速可行性诊断
# ============================================================
def quick_feasibility_diagnose(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cfg: WindBESSPlanConfig,
) -> dict[str, float]:
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
    eta_rt = cfg.eta_charge * cfg.eta_discharge

    e_served_upper = e_direct + eta_rt * e_surplus

    return {
        "wind_load_ratio": (e_wind / e_load) if e_load > 0 else 0.0,
        "surplus_load_ratio": (e_surplus / e_load) if e_load > 0 else 0.0,
        "served_upper_ratio": (e_served_upper / e_load) if e_load > 0 else 0.0,
        "green_self_upper": (e_served_upper / e_wind) if e_wind > 0 else 0.0,
    }


# ============================================================
# 可达性检查
# ============================================================
def check_feasibility_upper_bound(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cfg: WindBESSPlanConfig,
) -> dict[str, float]:
    """用极大容量测试物理上是否可达，返回最大消纳率和覆盖率。"""
    r_inf = simulate_dispatch(load_kw, wind_kw, dt_h, cap_kwh=1e9, cfg=cfg)
    return {
        "max_green_self_consumption": r_inf["metrics"]["green_self_consumption"],
        "max_load_coverage": r_inf["metrics"]["load_coverage"],
    }


# ============================================================
# 可行性判断
# ============================================================
def is_feasible(res: dict[str, Any], cfg: WindBESSPlanConfig) -> bool:
    m = res["metrics"]
    ok = (m["green_self_consumption"] >= cfg.min_green_self_consumption and
          m["load_coverage"] >= cfg.min_load_coverage)
    if "terminal_soc_ok" in res and (res["terminal_soc_ok"] is False):
        return False
    return ok


# ============================================================
# 二分搜索最小容量
# ============================================================
def find_min_capacity_bisect(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    dt_h: float,
    cfg: WindBESSPlanConfig,
) -> dict[str, Any]:
    """
    二分搜索最小可行容量。
    容量越大，能搬运的能量越多，覆盖率与自用率不会变差。
    """
    # 可达性检查：用极大容量测试物理上是否可达
    upper = check_feasibility_upper_bound(load_kw, wind_kw, dt_h, cfg)
    if upper["max_green_self_consumption"] < cfg.min_green_self_consumption or \
       upper["max_load_coverage"] < cfg.min_load_coverage:
        raise RuntimeError(
            f"目标在物理上不可达：\n"
            f"最大风电消纳率={upper['max_green_self_consumption']:.3f}, "
            f"最大负荷覆盖率={upper['max_load_coverage']:.3f}"
        )

    # 先快速找可行上界
    lo = 0.0
    hi = 1.0
    best = None

    while hi <= cfg.cap_max_mwh * 1000.0 + 1e-9:
        res = simulate_dispatch(load_kw, wind_kw, dt_h, hi, cfg)
        if is_feasible(res, cfg):
            best = res
            break
        hi *= 2.0

    if best is None:
        raise RuntimeError(
            f"No feasible solution up to cap_max_mwh={cfg.cap_max_mwh}. "
            f"Try increasing cap_max_mwh or relaxing targets."
        )

    # 二分搜索
    tol_kwh = cfg.tol_mwh * 1000.0
    while (hi - lo) > tol_kwh:
        mid = (lo + hi) / 2.0
        res = simulate_dispatch(load_kw, wind_kw, dt_h, mid, cfg)
        if is_feasible(res, cfg):
            best = res
            hi = mid
        else:
            lo = mid

    # 容量取整（向上取到 tol_kwh）
    cap_final_kwh = float(np.ceil(hi / tol_kwh) * tol_kwh)
    best = simulate_dispatch(load_kw, wind_kw, dt_h, cap_final_kwh, cfg)
    best["cap_kwh"] = cap_final_kwh

    return best


# ============================================================
# 主规划函数
# ============================================================
def plan_wind_bess_system(
    df_load: pd.DataFrame,
    wind_input: pd.DataFrame,
    cfg: WindBESSPlanConfig = WindBESSPlanConfig(),
    time_col: str = "Time",
    load_col: str = "P_kw",
    wind_col: str = "WindPower_MW",
    out_schedule_csv: str | None = None,
) -> WindBESSResult:
    """
    Wind+BESS 容量规划主入口。

    Args:
        df_load: 负荷数据 DataFrame
        wind_input: 风电数据 DataFrame
        cfg: 规划配置
        time_col: 时间列名
        load_col: 负荷列名 (kW)
        wind_col: 风电列名 (MW)
        out_schedule_csv: 调度策略输出 CSV 路径，None 则不输出

    Returns:
        WindBESSResult: 规划结果
    """
    try:
        # 数据读取与对齐
        df_load_ts = _read_timeseries(df_load, time_col)
        df_wind_ts = _read_timeseries(wind_input, time_col)
        df, dt_h = align_and_merge(df_load_ts, df_wind_ts, load_col, wind_col)

        # 转 numpy
        load_kw = df["Load_kW"].to_numpy(dtype=float)
        wind_kw = df["Wind_kW"].to_numpy(dtype=float)

        # 快速诊断
        diag = quick_feasibility_diagnose(load_kw, wind_kw, dt_h, cfg)

        # 二分求最小容量
        result = find_min_capacity_bisect(load_kw, wind_kw, dt_h, cfg)

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

        # 计算成本
        cap_kwh = result["cap_kwh"]
        cost_cny = cap_kwh * cfg.capex_cny_per_kwh

        return WindBESSResult(
            feasible=True,
            capacity_mwh=cap_kwh / 1000.0,
            cost_cny=cost_cny,
            green_self_consumption=result["metrics"]["green_self_consumption"],
            load_coverage=result["metrics"]["load_coverage"],
            equiv_cycles=result["metrics"]["equiv_cycles"],
            energy_kwh=result["energy_kwh"],
            schedule_df=schedule,
            diagnosis=diag,
        )

    except Exception as e:
        return WindBESSResult(
            feasible=False,
            diagnosis={"error": str(e)},
        )


def _read_timeseries(obj, time_col: str = "Time") -> pd.DataFrame:
    """读取时间序列数据，返回 DatetimeIndex 的 DataFrame。"""
    if isinstance(obj, pd.DataFrame):
        return ensure_datetime_index(obj, time_col)
    raise ValueError(f"Unsupported input type: {type(obj)}")


# ============================================================
# 月度统计
# ============================================================
def calc_monthly_wind_metrics(
    df: pd.DataFrame,
    load_col: str = "Load_kW",
    wind_col: str = "Wind_kW",
) -> pd.DataFrame:
    """计算月度风电消纳统计。"""
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
# 容量曲线绘制
# ============================================================
def plot_capacity_curve(
    df: pd.DataFrame,
    dt_h: float,
    cfg: WindBESSPlanConfig,
    cap_max_mwh: float | None = None,
    n_points: int = 30,
) -> None:
    """
    绘制容量响应曲线：容量 vs 覆盖率/自用率。

    Args:
        df: 包含 Load_kW 和 Wind_kW 列的 DataFrame
        dt_h: 时间步长 (h)
        cfg: Wind+BESS 规划配置
        cap_max_mwh: 最大容量 (MWh)，默认为 cap_max_mwh 的 1.3 倍
        n_points: 采样点数
    """
    import matplotlib.pyplot as plt

    load_kw = df["Load_kW"].to_numpy(float)
    wind_kw = df["Wind_kW"].to_numpy(float)

    if cap_max_mwh is None:
        cap_max_mwh = 1.3 * cfg.cap_max_mwh

    # 采样点（对数更密集，减少计算量但更能看陡峭区）
    caps = np.unique(np.round(np.geomspace(1, cap_max_mwh, n_points), 1))
    caps = np.insert(caps, 0, 0.0)

    covs = []
    selfs = []
    for c in caps:
        r = simulate_dispatch(load_kw, wind_kw, dt_h, float(c) * 1000.0, cfg)
        covs.append(r["metrics"]["load_coverage"])
        selfs.append(r["metrics"]["green_self_consumption"])

    plt.figure()
    plt.plot(caps, covs, marker="o", label="Load coverage")
    plt.plot(caps, selfs, marker="o", label="Green self-consumption")
    plt.axhline(cfg.min_load_coverage, linestyle="--", alpha=0.5, label=f"Coverage target ({cfg.min_load_coverage:.0%})")
    plt.axhline(cfg.min_green_self_consumption, linestyle="--", alpha=0.5, label=f"Self-consumption target ({cfg.min_green_self_consumption:.0%})")
    plt.xlabel("Capacity (MWh)")
    plt.ylabel("Ratio")
    plt.title("Capacity vs Coverage / Self-consumption")
    plt.legend()
    plt.grid(True)
    plt.show()
