"""V4 容量规划投资测算的 canonical 逐时调度核。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from .physics_contract import BESSPhysicsContract

try:
    from numba import njit

    _NUMBA_OK = True
except Exception:
    _NUMBA_OK = False

    def njit(*args, **kwargs):
        def deco(func):
            return func

        return deco


@dataclass(slots=True)
class DispatchSimulationResult:
    """canonical dispatch 输出的完整可追溯时序结果。

    字段口径：
    - `*_kwh` 数组均为每个时间步的电量，不是功率；
    - `net_load_kw` 保持功率口径，用于 settlement 层计算需量峰值；
    - `monthly_summary` 和 `annual_summary` 只能由逐时数组汇总得到。
    """

    timestamps: pd.DatetimeIndex
    generation_kwh: np.ndarray
    direct_used_kwh: np.ndarray
    charge_kwh: np.ndarray
    discharge_kwh: np.ndarray
    soc_kwh: np.ndarray
    grid_buy_kwh: np.ndarray
    curtail_kwh: np.ndarray
    load_kwh: np.ndarray
    net_load_kw: np.ndarray
    grid_charge_kwh: np.ndarray
    monthly_summary: dict[str, dict[str, float]]
    annual_summary: dict[str, float]
    metadata: dict[str, Any]


def _as_array(values: Any, *, name: str, length: int | None = None) -> np.ndarray:
    """把输入序列归一成一维 float 数组，并做最小数据质量检查。"""
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if length is not None and len(arr) != length:
        raise ValueError(f"{name} length must match load_kw length")
    if np.isnan(arr).any():
        raise ValueError(f"{name} must not contain NaN")
    return arr


def _monthly_summary(timestamps: pd.DatetimeIndex, result: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    """按自然月汇总物理电量，给 settlement 层提供审计基线。"""
    months: dict[str, dict[str, float]] = {}
    period_keys = timestamps.to_period("M").astype(str)
    for month in sorted(set(period_keys)):
        mask = period_keys == month
        months[month] = {
            "generation_kwh": float(result["generation_kwh"][mask].sum()),
            "direct_used_kwh": float(result["direct_used_kwh"][mask].sum()),
            "charge_kwh": float(result["charge_kwh"][mask].sum()),
            "discharge_kwh": float(result["discharge_kwh"][mask].sum()),
            "green_used_kwh": float(
                result["direct_used_kwh"][mask].sum() + result["discharge_kwh"][mask].sum()
            ),
            "grid_buy_kwh": float(result["grid_buy_kwh"][mask].sum()),
            "curtail_kwh": float(result["curtail_kwh"][mask].sum()),
            "load_kwh": float(result["load_kwh"][mask].sum()),
            "net_load_peak_kw": float(result["net_load_kw"][mask].max()) if mask.any() else 0.0,
        }
    return months


def _annual_summary(months: Mapping[str, Mapping[str, float]]) -> dict[str, float]:
    """年度物理汇总必须来自月度汇总，避免另起年度散字段。"""
    keys = (
        "generation_kwh",
        "direct_used_kwh",
        "charge_kwh",
        "discharge_kwh",
        "green_used_kwh",
        "grid_buy_kwh",
        "curtail_kwh",
        "load_kwh",
    )
    return {key: float(sum(month[key] for month in months.values())) for key in keys}


@njit
def _canonical_dispatch_numba(
    load: np.ndarray,
    gen_total: np.ndarray,
    dt_hours: float,
    capacity: float,
    eta_charge: float,
    eta_discharge: float,
    c_rate: float,
    soc_init_frac: float,
    soc_min_frac: float,
    soc_max_frac: float,
    switch_gap_steps: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Numba 快路径：只做逐时调度，不处理 pandas 月度汇总。grid_charge 恒为 0（canonical 不向网充电）。"""
    n = load.shape[0]
    direct_used_kwh = np.zeros(n, dtype=np.float64)
    charge_kwh = np.zeros(n, dtype=np.float64)
    discharge_kwh = np.zeros(n, dtype=np.float64)
    soc_kwh = np.zeros(n, dtype=np.float64)
    grid_buy_kwh = np.zeros(n, dtype=np.float64)
    curtail_kwh = np.zeros(n, dtype=np.float64)
    net_load_kw = np.zeros(n, dtype=np.float64)
    load_kwh = np.zeros(n, dtype=np.float64)
    grid_charge_kwh = np.zeros(n, dtype=np.float64)

    soc_min = soc_min_frac * capacity
    soc_max = soc_max_frac * capacity
    soc = soc_init_frac * capacity
    if soc < soc_min:
        soc = soc_min
    if soc > soc_max:
        soc = soc_max
    pmax = c_rate * capacity
    last_action = 0
    last_action_t = -10**9

    for i in range(n):
        load_i = load[i]
        if load_i < 0.0:
            load_i = 0.0
        gen_i = gen_total[i]
        if gen_i < 0.0:
            gen_i = 0.0

        load_kwh[i] = load_i * dt_hours
        direct_kw = load_i if load_i < gen_i else gen_i
        surplus_kw = gen_i - direct_kw
        deficit_kw = load_i - direct_kw

        can_charge = not (last_action == -1 and (i - last_action_t) < switch_gap_steps)
        can_discharge = not (last_action == 1 and (i - last_action_t) < switch_gap_steps)

        charge_kw = 0.0
        if surplus_kw > 1e-9 and capacity > 0.0 and soc < soc_max and can_charge:
            room_input_kw = (soc_max - soc) / (eta_charge * dt_hours)
            charge_kw = surplus_kw
            if charge_kw > pmax:
                charge_kw = pmax
            if charge_kw > room_input_kw:
                charge_kw = room_input_kw
            if charge_kw > 1e-9:
                soc += charge_kw * eta_charge * dt_hours
                last_action = 1
                last_action_t = i

        discharge_kw = 0.0
        if deficit_kw > 1e-9 and capacity > 0.0 and soc > soc_min and can_discharge:
            available_output_kw = (soc - soc_min) * eta_discharge / dt_hours
            discharge_kw = deficit_kw
            if discharge_kw > pmax:
                discharge_kw = pmax
            if discharge_kw > available_output_kw:
                discharge_kw = available_output_kw
            if discharge_kw > 1e-9:
                soc -= discharge_kw * dt_hours / eta_discharge
                last_action = -1
                last_action_t = i

        direct_used_kwh[i] = direct_kw * dt_hours
        charge_kwh[i] = charge_kw * dt_hours
        discharge_kwh[i] = discharge_kw * dt_hours
        grid_buy_kwh[i] = (deficit_kw - discharge_kw) * dt_hours
        if grid_buy_kwh[i] < 0.0:
            grid_buy_kwh[i] = 0.0
        curtail_kwh[i] = (surplus_kw - charge_kw) * dt_hours
        if curtail_kwh[i] < 0.0:
            curtail_kwh[i] = 0.0
        soc_kwh[i] = soc
        net_load_kw[i] = load_i - direct_kw - discharge_kw
        if net_load_kw[i] < 0.0:
            net_load_kw[i] = 0.0

    return direct_used_kwh, charge_kwh, discharge_kwh, soc_kwh, grid_buy_kwh, curtail_kwh, load_kwh, net_load_kw, grid_charge_kwh


def canonical_dispatch(
    *,
    load_kw: np.ndarray,
    generation_kw: Mapping[str, np.ndarray],
    bess: BESSPhysicsContract,
    bess_capacity_kwh: float,
    timestamps: pd.DatetimeIndex | list[Any],
    dt_hours: float,
    switch_gap_steps: int = 0,
) -> DispatchSimulationResult:
    """运行 V4 canonical 贪心调度。

    调度原则：
    1. 新能源先直供负荷；
    2. surplus 优先充电，受 C-rate、SOC 上限和切换间隔约束；
    3. deficit 优先由电池放电，受 C-rate、SOC 下限和切换间隔约束；
    4. 仍无法消纳的 surplus 计为弃电，仍无法覆盖的 deficit 计为购网。
    """
    # TODO 补充注释
    bess.validate()
    
    if dt_hours <= 0:
        raise ValueError("dt_hours must be positive")
    if bess_capacity_kwh < 0:
        raise ValueError("bess_capacity_kwh must be non-negative")

    ts = pd.DatetimeIndex(pd.to_datetime(timestamps))
    load = _as_array(load_kw, name="load_kw")
    if len(ts) != len(load):
        raise ValueError("timestamps length must match load_kw length")
    if len(load) == 0:
        raise ValueError("dispatch horizon must not be empty")
    if (load < 0).any():
        raise ValueError("load_kw must be non-negative")

    gen_total = np.zeros(len(load), dtype=float)
    for source, values in generation_kw.items():
        arr = _as_array(values, name=f"generation_kw[{source!r}]", length=len(load))
        gen_total += np.maximum(arr, 0.0)

    # 以下数组全部按时间步存储，方便后续做月度/年度复算和手算 oracle。
    generation_kwh = gen_total * dt_hours
    load_kwh = load * dt_hours
    if _NUMBA_OK:
        (
            direct_used_kwh,
            charge_kwh,
            discharge_kwh,
            soc_kwh,
            grid_buy_kwh,
            curtail_kwh,
            load_kwh,
            net_load_kw,
            grid_charge_kwh,
        ) = _canonical_dispatch_numba(
            load,
            gen_total,
            float(dt_hours),
            float(bess_capacity_kwh),
            float(bess.eta_charge),
            float(bess.eta_discharge),
            float(bess.c_rate),
            float(bess.soc_init_frac),
            float(bess.soc_min_frac),
            float(bess.soc_max_frac),
            int(switch_gap_steps),
        )
    else:
        direct_used_kwh = np.zeros(len(load), dtype=float)
        charge_kwh = np.zeros(len(load), dtype=float)
        discharge_kwh = np.zeros(len(load), dtype=float)
        soc_kwh = np.zeros(len(load), dtype=float)
        grid_buy_kwh = np.zeros(len(load), dtype=float)
        curtail_kwh = np.zeros(len(load), dtype=float)
        net_load_kw = np.zeros(len(load), dtype=float)
        grid_charge_kwh = np.zeros(len(load), dtype=float)

        # SOC 用 kWh 表达；capacity=0 时所有充放电自然退化为 0。
        capacity = float(bess_capacity_kwh)
        soc_min = bess.soc_min_frac * capacity
        soc_max = bess.soc_max_frac * capacity
        soc = bess.soc_init_frac * capacity
        pmax = bess.c_rate * capacity
        last_action = 0
        last_action_t = -10**9

        for i, (load_i, gen_i) in enumerate(zip(load, gen_total)):
            # direct_kw 是本时步新能源直接覆盖负荷的功率，不经过电池。
            direct_kw = min(load_i, gen_i)
            surplus_kw = max(gen_i - direct_kw, 0.0)
            deficit_kw = max(load_i - direct_kw, 0.0)

            # 切换间隔约束用于避免刚放电后立即充电，或刚充电后立即放电。
            can_charge = not (last_action == -1 and (i - last_action_t) < switch_gap_steps)
            can_discharge = not (last_action == 1 and (i - last_action_t) < switch_gap_steps)

            charge_kw = 0.0
            if surplus_kw > 1e-9 and capacity > 0 and soc < soc_max and can_charge:
                # room_input_kw 是考虑充电效率后，SOC 剩余空间允许的 AC 侧充电功率。
                room_input_kw = (soc_max - soc) / (bess.eta_charge * dt_hours)
                charge_kw = min(surplus_kw, pmax, room_input_kw)
                if charge_kw > 1e-9:
                    soc += charge_kw * bess.eta_charge * dt_hours
                    last_action = 1
                    last_action_t = i

            discharge_kw = 0.0
            if deficit_kw > 1e-9 and capacity > 0 and soc > soc_min and can_discharge:
                # available_output_kw 是考虑放电效率后，电池 SOC 可支持的 AC 侧放电功率。
                available_output_kw = (soc - soc_min) * bess.eta_discharge / dt_hours
                discharge_kw = min(deficit_kw, pmax, available_output_kw)
                if discharge_kw > 1e-9:
                    soc -= discharge_kw * dt_hours / bess.eta_discharge
                    last_action = -1
                    last_action_t = i

            direct_used_kwh[i] = direct_kw * dt_hours
            charge_kwh[i] = charge_kw * dt_hours
            discharge_kwh[i] = discharge_kw * dt_hours
            grid_buy_kwh[i] = max(deficit_kw - discharge_kw, 0.0) * dt_hours
            curtail_kwh[i] = max(surplus_kw - charge_kw, 0.0) * dt_hours
            soc_kwh[i] = soc
            # net_load_kw 保留功率口径，需量电费必须从该序列的月内峰值计算。
            net_load_kw[i] = max(load_i - direct_kw - discharge_kw, 0.0)

    arrays = {
        "generation_kwh": generation_kwh,
        "direct_used_kwh": direct_used_kwh,
        "charge_kwh": charge_kwh,
        "discharge_kwh": discharge_kwh,
        "grid_buy_kwh": grid_buy_kwh,
        "curtail_kwh": curtail_kwh,
        "load_kwh": load_kwh,
        "net_load_kw": net_load_kw,
        "grid_charge_kwh": grid_charge_kwh,
    }
    months = _monthly_summary(ts, arrays)
    annual = _annual_summary(months)
    # metadata 记录物理口径，便于后续 golden/oracle 比对。
    metadata = {
        "dt_hours": float(dt_hours),
        "eta_charge": float(bess.eta_charge),
        "eta_discharge": float(bess.eta_discharge),
        "soc_unit": bess.soc_unit,
        "switch_gap_steps": int(switch_gap_steps),
        "grid_charge_kwh_total": float(grid_charge_kwh.sum()),
    }
    return DispatchSimulationResult(
        timestamps=ts,
        generation_kwh=generation_kwh,
        direct_used_kwh=direct_used_kwh,
        charge_kwh=charge_kwh,
        discharge_kwh=discharge_kwh,
        soc_kwh=soc_kwh,
        grid_buy_kwh=grid_buy_kwh,
        curtail_kwh=curtail_kwh,
        load_kwh=load_kwh,
        net_load_kw=net_load_kw,
        grid_charge_kwh=grid_charge_kwh,
        monthly_summary=months,
        annual_summary=annual,
        metadata=metadata,
    )
