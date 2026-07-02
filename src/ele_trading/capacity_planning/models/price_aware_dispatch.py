"""价格感知调度：canonical 物理核的价值最大化兄弟。

设计原则（见 docs/superpowers/specs/2026-07-02-root-layer-design.md §3）：
canonical_dispatch 作为物理守恒 oracle 不动；本模块产出同一 ``DispatchSimulationResult``
合同，但调度策略是**价格感知**的——放电优先供给高价时段，套利模式下还可向网充电。

实现为**单次前向扫描 + 价格阈值**启发式：
- ``mid = (price.min()+price.max())/2`` 作为充/放电分界；
- RE 富余永远优先充电（免费能量）；
- ``SELF_CONSUMPTION``：仅在 ``price >= mid`` 的 deficit 小时放电（削峰，不向网充电）；
- ``ARBITRAGE``：另在 ``price < mid`` 的谷时向网充电，``price >= mid`` 时放电（套利）。

阈值启发式为近似解（spec 明示）；sizing 定型后的精确调度由 Task 9 的 MILP 单点精修覆盖。
``charge_kwh`` 只计 RE 充电（保证 ``generation = direct_used + charge + curtail`` 守恒），
向网充电单独记入 ``grid_charge_kwh``；``grid_buy_kwh`` 仅计负荷侧购网（不含向网充电），
因此 ``load = direct_used + discharge + grid_buy`` 在两模式下都成立。
"""
from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd

from .canonical_dispatch import (
    DispatchSimulationResult,
    _annual_summary,
    _as_array,
    _monthly_summary,
)
from .physics_contract import BESSPhysicsContract


class DispatchMode(str, Enum):
    """价格感知调度的充电边界模式。"""

    SELF_CONSUMPTION = "self_consumption"
    ARBITRAGE = "arbitrage"


def price_aware_dispatch(
    *,
    load_kw: np.ndarray,
    generation_kw: dict[str, np.ndarray],
    bess: BESSPhysicsContract,
    bess_capacity_kwh: float,
    price_yuan_per_kwh: np.ndarray,
    timestamps,
    dt_hours: float,
    mode: DispatchMode = DispatchMode.SELF_CONSUMPTION,
    switch_gap_steps: int = 0,
) -> DispatchSimulationResult:
    """运行价格感知调度，产出与 canonical 同构的 ``DispatchSimulationResult``。"""
    bess.validate()
    if dt_hours <= 0:
        raise ValueError("dt_hours must be positive")
    if bess_capacity_kwh < 0:
        raise ValueError("bess_capacity_kwh must be non-negative")

    ts = pd.DatetimeIndex(pd.to_datetime(timestamps))
    load = _as_array(load_kw, name="load_kw")
    n = len(load)
    if len(ts) != n:
        raise ValueError("timestamps length must match load_kw length")
    if n == 0:
        raise ValueError("dispatch horizon must not be empty")
    if (load < 0).any():
        raise ValueError("load_kw must be non-negative")
    price = _as_array(price_yuan_per_kwh, name="price_yuan_per_kwh", length=n)
    if (price < 0).any():
        raise ValueError("price_yuan_per_kwh must be non-negative")

    gen_total = np.zeros(n, dtype=float)
    for source, values in generation_kw.items():
        gen_total += np.maximum(
            _as_array(values, name=f"generation_kw[{source!r}]", length=n), 0.0
        )

    cap = float(bess_capacity_kwh)
    dt = float(dt_hours)
    eta_c = float(bess.eta_charge)
    eta_d = float(bess.eta_discharge)
    soc_min = bess.soc_min_frac * cap
    soc_max = bess.soc_max_frac * cap
    pmax = bess.c_rate * cap

    direct_kw = np.minimum(load, gen_total)
    surplus_kw = np.maximum(gen_total - direct_kw, 0.0)
    deficit_kw = np.maximum(load - direct_kw, 0.0)

    charge_kwh = np.zeros(n)  # 仅 RE 充电
    discharge_kwh = np.zeros(n)
    grid_charge_kwh = np.zeros(n)  # ARBITRAGE 向网充电
    curtail_kwh = np.zeros(n)
    soc_kwh = np.zeros(n)
    grid_buy_kwh = np.zeros(n)
    net_load_kw = np.zeros(n)

    # 价格阈值：扁平电价时 mid==price，放电门槛退化为“所有 deficit 小时都放”（同 canonical）。
    price_mid = (float(price.min()) + float(price.max())) / 2.0
    arbitrage = mode == DispatchMode.ARBITRAGE

    soc = max(bess.soc_init_frac * cap, soc_min)
    if soc > soc_max:
        soc = soc_max
    for t in range(n):
        # 1) RE 富余优先充电（两模式共用，免费能量）。
        c_power = 0.0
        if surplus_kw[t] > 1e-9 and cap > 0.0 and soc < soc_max:
            room_ac_kw = (soc_max - soc) / (eta_c * dt)
            c_power = min(surplus_kw[t], pmax, room_ac_kw)
            if c_power > 1e-9:
                soc += c_power * eta_c * dt
                charge_kwh[t] = c_power * dt
        curtail_kwh[t] = max(surplus_kw[t] - c_power, 0.0) * dt

        # 2) ARBITRAGE：谷时（price < mid）向网充电。
        if arbitrage and price[t] < price_mid and cap > 0.0 and soc < soc_max:
            room_ac_kw = (soc_max - soc) / (eta_c * dt)
            gc_power = min(pmax, room_ac_kw)
            if gc_power > 1e-9:
                soc += gc_power * eta_c * dt
                grid_charge_kwh[t] = gc_power * dt

        # 3) 价格感知放电：仅在 price >= mid 的 deficit 小时放电（峰时优先）。
        d_power = 0.0
        if (
            deficit_kw[t] > 1e-9
            and price[t] >= price_mid
            and cap > 0.0
            and soc > soc_min
        ):
            avail_ac_kw = (soc - soc_min) * eta_d / dt
            d_power = min(deficit_kw[t], pmax, avail_ac_kw)
            if d_power > 1e-9:
                soc -= d_power * dt / eta_d
                discharge_kwh[t] = d_power * dt

        soc_kwh[t] = soc
        # grid_buy 仅计负荷侧购网（deficit 未被放电覆盖部分），不含向网充电。
        grid_buy_kwh[t] = max(deficit_kw[t] - d_power, 0.0) * dt
        net_load_kw[t] = max(load[t] - direct_kw[t] - d_power, 0.0)

    generation_kwh = gen_total * dt
    direct_used_kwh = direct_kw * dt
    load_kwh = load * dt

    arrays = {
        "generation_kwh": generation_kwh,
        "direct_used_kwh": direct_used_kwh,
        "charge_kwh": charge_kwh,
        "discharge_kwh": discharge_kwh,
        "grid_buy_kwh": grid_buy_kwh,
        "curtail_kwh": curtail_kwh,
        "load_kwh": load_kwh,
        "net_load_kw": net_load_kw,
    }
    months = _monthly_summary(ts, arrays)
    annual = _annual_summary(months)
    metadata = {
        "dispatch_mode": mode.value,
        "dt_hours": dt,
        "eta_charge": eta_c,
        "eta_discharge": eta_d,
        "soc_unit": bess.soc_unit,
        "switch_gap_steps": int(switch_gap_steps),
        "price_mid": price_mid,
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
