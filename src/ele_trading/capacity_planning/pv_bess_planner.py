"""PV+BESS 容量规划模块

基于共享调度内核 models/resource_bess_planner_core.py 实现。
支持两种调度模式：
- 纯弃电搬运模式 (enable_shift=False): 只用 surplus 充电，deficit 放电
- 平移充电模式 (enable_shift=True): 允许 PV 不足时主动抽取部分 PV 充电（lookahead 预判）

采用二分搜索算法，比线性搜索更快。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ele_trading.utils.data_alignment import align_and_merge, ensure_datetime_index

from .models.resource_bess_planner_core import (
    ResourceBESSConfig,
    ShiftPolicy,
    find_min_capacity_bisect,
    post_metrics,
    quick_feasibility_diagnose as _core_quick_feasibility_diagnose,
    simulate_dispatch as _core_simulate_dispatch,
)


# ============================================================
# PV 特定配置与结果（保留向后兼容的字段名）
# ============================================================
@dataclass(slots=True)
class PVBESSPlanConfig:
    """PV+BESS 容量规划配置。

    字段与旧版完全一致，向后兼容；内部转换为 ResourceBESSConfig 供共享内核使用。
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
    # 成本
    pv_capex_yuan_per_kwp: float = 2000.0
    bess_capex_yuan_per_kwh: float = 1000.0
    # 搜索参数
    cap_max_mwh: float = 5000.0
    tol_mwh: float = 0.1
    # 策略
    shift_policy: ShiftPolicy = field(default_factory=ShiftPolicy)


@dataclass(slots=True)
class PVBESSResult:
    """PV+BESS 容量规划结果。"""
    feasible: bool
    pv_kwp: float = 0.0
    bess_mwh: float = 0.0
    pv_cost_yuan: float = 0.0
    bess_cost_yuan: float = 0.0
    total_cost_yuan: float = 0.0
    self_consumption: float = 0.0
    load_coverage: float = 0.0
    equiv_cycles: float = 0.0
    energy_kwh: dict = field(default_factory=dict)
    schedule_df: pd.DataFrame | None = None
    diagnosis: dict | None = None


# ============================================================
# 配置转换
# ============================================================
def _to_core_config(cfg: PVBESSPlanConfig) -> ResourceBESSConfig:
    """将 PV 特定配置映射为共享内核配置（资源无关）。"""
    return ResourceBESSConfig(
        eta_charge=cfg.eta_charge,
        eta_discharge=cfg.eta_discharge,
        c_rate=cfg.c_rate,
        soc_init=cfg.soc_init,
        soc_min=cfg.soc_min,
        soc_max=cfg.soc_max,
        enforce_terminal_soc=cfg.enforce_terminal_soc,
        min_self_consumption=cfg.min_self_consumption,
        min_load_coverage=cfg.min_load_coverage,
        cap_max_mwh=cfg.cap_max_mwh,
        tol_mwh=cfg.tol_mwh,
        shift_policy=cfg.shift_policy,
    )


# ============================================================
# 向后兼容的公开接口（委托给共享内核）
# ============================================================
def simulate_dispatch(
    load_kw: np.ndarray,
    pv_kw: np.ndarray,
    dt_h: float,
    cap_kwh: float,
    cfg: PVBESSPlanConfig,
) -> dict[str, Any]:
    """单次调度仿真入口（委托共享内核）。"""
    return _core_simulate_dispatch(load_kw, pv_kw, dt_h, cap_kwh, _to_core_config(cfg))


def calc_monthly_pv_metrics(
    load_kw: np.ndarray,
    pv_kw: np.ndarray,
    dt_h: float,
    time_index: pd.DatetimeIndex,
) -> dict[str, Any]:
    """月度 PV 消纳统计（PV 特有，保留在此模块）。"""
    df = pd.DataFrame({"load": load_kw, "pv": pv_kw}, index=time_index)
    monthly = df.resample("ME").agg({"load": "sum", "pv": "sum"})
    load_sum = monthly["load"].to_numpy(dtype=float)
    pv_sum = monthly["pv"].to_numpy(dtype=float)
    safe_load = np.where(load_sum > 0, load_sum, np.nan)
    monthly["pv_ratio"] = pv_sum / safe_load
    return {str(k): v for k, v in monthly.to_dict("index").items()}


def plot_capacity_curve(
    load_kw: np.ndarray,
    pv_kw: np.ndarray,
    dt_h: float,
    cfg: PVBESSPlanConfig,
    output_path: str | None = None,
) -> None:
    """容量响应曲线可视化（PV 特有，保留在此模块）。"""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    caps = np.linspace(0, cfg.cap_max_mwh * 1000, 50)
    self_cons = []
    coverage = []
    core_cfg = _to_core_config(cfg)
    for c in caps:
        res = _core_simulate_dispatch(load_kw, pv_kw, dt_h, c, core_cfg)
        self_cons.append(res["metrics"]["self_consumption"])
        coverage.append(res["metrics"]["load_coverage"])

    fig, ax = plt.subplots(figsize=(10, 6))
    ax.plot(caps / 1000, self_cons, label="self_consumption", color="orange")
    ax.plot(caps / 1000, coverage, label="load_coverage", color="blue")
    ax.set_xlabel("BESS Capacity (MWh)")
    ax.set_ylabel("Ratio")
    ax.set_title("PV+BESS Capacity Response Curve")
    ax.legend()
    ax.grid(True, alpha=0.3)
    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ============================================================
# 主规划函数
# ============================================================
def plan_pv_bess_system(
    df_load: pd.DataFrame,
    pv_input: pd.DataFrame,
    cfg: PVBESSPlanConfig = PVBESSPlanConfig(),
    time_col: str = "Time",
    load_col: str = "P_kw",
    pv_col: str = "PV_kW",
    pv_kwp: float = 1.0,
    out_schedule_csv: str | None = None,
) -> PVBESSResult:
    """PV+BESS 容量规划主入口。

    Args:
        df_load: 负荷数据 DataFrame
        pv_input: 光伏出力数据 DataFrame（单位 kW/kWp）
        cfg: 规划配置
        time_col: 时间列名
        load_col: 负荷列名 (kW)
        pv_col: 光伏列名
        pv_kwp: 光伏装机容量 (kWp)，用于缩放 pv_input
        out_schedule_csv: 调度策略输出 CSV 路径，None 则不输出

    Returns:
        PVBESSResult
    """
    try:
        # 数据读取与对齐
        df_load_ts = _read_timeseries(df_load, time_col)
        df_pv_ts = _read_timeseries(pv_input, time_col)
        df, dt_h = align_and_merge(df_load_ts, df_pv_ts, load_col, pv_col)

        # 转 numpy 并缩放 PV
        load_kw = df["Load_kW"].to_numpy(dtype=float)
        pv_kw = df["PV_kW"].to_numpy(dtype=float) * pv_kwp

        # 快速诊断
        core_cfg = _to_core_config(cfg)
        diag = _core_quick_feasibility_diagnose(load_kw, pv_kw, dt_h, core_cfg)

        # 二分求最小容量
        result = find_min_capacity_bisect(load_kw, pv_kw, dt_h, core_cfg)

        # 输出策略时间序列
        s = result["series"]
        schedule = pd.DataFrame(
            {
                "Load_kW": load_kw,
                "PV_kW": pv_kw,
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
        bess_cost = cap_kwh * cfg.bess_capex_yuan_per_kwh
        pv_cost = pv_kwp * cfg.pv_capex_yuan_per_kwp

        return PVBESSResult(
            feasible=True,
            pv_kwp=pv_kwp,
            bess_mwh=cap_kwh / 1000.0,
            pv_cost_yuan=pv_cost,
            bess_cost_yuan=bess_cost,
            total_cost_yuan=pv_cost + bess_cost,
            self_consumption=result["metrics"]["self_consumption"],
            load_coverage=result["metrics"]["load_coverage"],
            equiv_cycles=result["metrics"]["equiv_cycles"],
            energy_kwh=result["energy_kwh"],
            schedule_df=schedule,
            diagnosis=diag,
        )

    except RuntimeError as e:
        return PVBESSResult(feasible=False, diagnosis={"reason": str(e)})
    except Exception as e:
        return PVBESSResult(feasible=False, diagnosis={"reason": f"{type(e).__name__}: {e}"})


# ============================================================
# 数据读取辅助（保持与 wind_bess_planner 一致的旧接口）
# ============================================================
def _read_timeseries(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    """读取时间序列，确保时间列已解析。"""
    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    return df.sort_values(time_col).reset_index(drop=True)
