"""公共调度算法模块

提供年度贪心调度引擎（Python + Numba 双版本），供各容量规划脚本共用。
"""
from __future__ import annotations

from typing import Dict

import numpy as np

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


@njit
def _dispatch_annual_numba(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    dt_hours: float,
    batt_kwh: float,
    eta_roundtrip: float,
    c_rate: float,
    soc_init_frac: float,
    soc_min_frac: float,
    soc_max_frac: float,
    switch_gap_steps: int,
) -> tuple[float, float, float, float, float, float]:
    """贪心调度（Numba JIT），支持充放切换间隔。

    按时间步遍历全年负荷/新能源序列，每个时步依次：
      1) 计算新能源直供负荷的部分 (direct)；
      2) 多余电 (surplus) 优先给电池充电；
      3) 缺额 (deficit) 优先由电池放电补充；
      4) 未能消纳的 surplus 计入弃电。

    通过 last_action / last_action_t 记录最近一次充放电方向与步号，
    实现充放电相邻动作之间的最小间隔控制 (switch_gap_steps)。

    参数:
        load_kw, wind_kw, pv_kw, other_kw: 各序列长度均为 n 的时序 (kW)
        dt_hours: 单个时间步时长 (h)
        batt_kwh: 电池额定容量 (kWh)
        eta_roundtrip: 电池往返效率
        c_rate: 电池最大充放电倍率 (C)
        soc_init_frac / soc_min_frac / soc_max_frac: SOC 初始/下限/上限 (占容量比)
        switch_gap_steps: 充放电方向切换的最小时间间隔 (步)

    返回 (单位均为 kWh):
        gen_e, used_e, load_e, direct_e, bess_dis, curtail_e
    """
    gen_e = 0.0      # 新能源总发电量 (kWh)
    used_e = 0.0     # 新能源被消纳的总电量：直供 + 充电 (kWh)
    load_e = 0.0     # 负荷总用电量 (kWh)
    direct_e = 0.0   # 新能源直接供给负荷的电量 (kWh，未经过电池)
    bess_dis = 0.0   # 电池放电量 (kWh)
    curtail_e = 0.0  # 弃电量 (kWh)

    # 充放电效率对称拆分：往返效率开方，便于 SOC 增减计算
    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5

    # 电池额定容量 (kWh)
    E = batt_kwh

    # 电池最大充放电功率 (kW)，由容量与 C 率决定
    Pmax = c_rate * E

    # SOC 下限 / 上限 / 初始值 (kWh)；若初始值越界则裁剪到 [soc_min, soc_max]
    soc_min = soc_min_frac * E
    soc_max = soc_max_frac * E
    soc = soc_init_frac * E
    if soc < soc_min:
        soc = soc_min
    if soc > soc_max:
        soc = soc_max

    # 充放电方向状态机（+1=充电，-1=放电，0=空闲），
    # 配合 last_action_t 实现充放电切换的最小时间间隔
    last_action = 0  # +1=charge, -1=discharge, 0=idle
    last_action_t = -10**9

    # 时间步总数
    n = load_kw.shape[0]
    for i in range(n):
        # 负荷 (kW)
        L = load_kw[i]
        if L < 0.0:
            L = 0.0
        # 新能源发电 (kW)
        G = wind_kw[i] + pv_kw[i] + other_kw[i]
        if G < 0.0:
            G = 0.0

        # 负荷电能量
        load_e += L * dt_hours
        # 新能源发电电能量
        gen_e += G * dt_hours

        # 直供部分 (kW)
        direct = L if L < G else G
        used_e += direct * dt_hours
        direct_e += direct * dt_hours

        # surplus: 新能源超出直供负荷的多余电功率 (kW)，可尝试充电
        surplus = G - direct
        # deficit: 直供后仍未能覆盖的负荷缺额 (kW)，可尝试放电
        deficit = L - direct

        # 判断是否可以充放电（考虑切换间隔）
        can_charge = not (last_action == -1 and (i - last_action_t) < switch_gap_steps)
        can_discharge = not (last_action == 1 and (i - last_action_t) < switch_gap_steps)

        # 充电：surplus 多余且未到 SOC 上限
        ch_actual = 0.0
        if surplus > 1e-9 and E > 0.0 and soc < soc_max and can_charge:
            p_ch = surplus
            if p_ch > Pmax:
                p_ch = Pmax
            max_ch = (soc_max - soc) / dt_hours
            if p_ch > max_ch:
                p_ch = max_ch
            if p_ch > 1e-9:
                soc += p_ch * dt_hours * eta_c
                ch_actual = p_ch
                last_action = 1
                last_action_t = i
        # 放电：deficit 缺额且未到 SOC 下限
        if deficit > 1e-9 and E > 0.0 and soc > soc_min and can_discharge:
            p_dis = deficit
            if p_dis > Pmax:
                p_dis = Pmax
            max_dis = (soc - soc_min) * eta_d / dt_hours
            if p_dis > max_dis:
                p_dis = max_dis
            if p_dis > 1e-9:
                soc -= p_dis * dt_hours / eta_d
                used_e += p_dis * dt_hours
                bess_dis += p_dis * dt_hours
                last_action = -1
                last_action_t = i
        # 弃电 = surplus - 实际充电量 (surplus 实际未能存入电池的部分)
        curtail_e += max(surplus - ch_actual, 0.0) * dt_hours

    return gen_e, used_e, load_e, direct_e, bess_dis, curtail_e


def dispatch_annual(
    load_kw: np.ndarray,
    wind_kw: np.ndarray,
    pv_kw: np.ndarray,
    other_kw: np.ndarray,
    batt_kwh: float,
    dt_hours: float,
    cfg: Dict,
    switch_gap_steps: int = 0,
) -> dict[str, float]:
    if cfg.use_numba and _NUMBA_OK:
        gen_e, used_e, load_e, direct_e, bess_dis, curtail_e = _dispatch_annual_numba(
            load_kw = load_kw,
            wind_kw = wind_kw,
            pv_kw = pv_kw,
            other_kw = other_kw,
            batt_kwh = float(batt_kwh),
            dt_hours = dt_hours,
            eta_roundtrip = float(cfg.eta_roundtrip),
            c_rate = float(cfg.c_rate),
            soc_init_frac = float(cfg.soc_init_frac),
            soc_min_frac = float(cfg.soc_min_frac),
            soc_max_frac = float(cfg.soc_max_frac),
            switch_gap_steps = switch_gap_steps,
        )
    else:
        # 简化版调度：不模拟电池充放电，按直供/弃电两类分别累计
        # 新能源总出力 (kW)
        gen_kw = wind_kw + pv_kw + other_kw
        # 新能源直接供给负荷的部分 (kW)，取负荷与发电的较小者
        direct_kw = np.minimum(load_kw, np.maximum(gen_kw, 0.0))
        # 年新能源发电量 (kWh)
        gen_e = float(np.maximum(gen_kw, 0.0).sum() * dt_hours)
        # 年总负荷用电量 (kWh)
        load_e = float(np.maximum(load_kw, 0.0).sum() * dt_hours)
        # 新能源被直供消纳的电能 (kWh)
        used_e = float(direct_kw.sum() * dt_hours)
        direct_e = used_e
        # 简化路径不模拟电池充放电，故放电量记为 0
        bess_dis = 0.0
        # 弃电量（简化：surplus 部分减去充电）
        surplus_e = float(np.maximum(gen_kw - load_kw, 0.0).sum() * dt_hours)
        curtail_e = max(surplus_e - (gen_e - used_e - bess_dis), 0.0)

    return {
        "ren_gen_kwh": float(gen_e),
        "ren_used_kwh": float(used_e),
        "load_kwh": float(load_e),
        "direct_used_kwh": float(direct_e),
        "bess_discharge_kwh": float(bess_dis),
        "curtail_kwh": float(curtail_e),
    }
