from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ele_trading.utils.time_index import infer_dt_hours, monthly_kwh


@dataclass(slots=True)
class CapacityPlanResult:
    wind_mw: float
    pv_mw: float
    ess_mwh: float
    ess_mw: float
    total_cost_wan: float
    green_ratio: float
    self_use_ratio: float
    curtailment_ratio: float
    pv_monthly_kwh: pd.Series | None = None


_DEFAULT_SEARCH = {
    'coarse_step_mw': 10,
    'coarse_step_mwh': 20,
    'fine_step_mw': 1,
    'fine_step_mwh': 2,
    'fine_range_ratio': 0.3,
    'max_wind_mw': None,
    'max_pv_mw': None,
    'max_ess_mwh': None,
}


class CapacityOptimizer:
    def __init__(self, bess_params, cost_params, search_params=None):
        self.sp = bess_params
        self.cost = cost_params
        self.search = {**_DEFAULT_SEARCH, **(search_params or {})}

    def optimize(self, load_series, wind_unit_output, solar_unit_output,
                 green_ratio_min, self_use_ratio_min,
                 fixed_wind_mw=None, fixed_pv_mw=None):
        s = self.search
        max_wind = s['max_wind_mw'] or float(load_series.max()) * 2
        max_pv = s['max_pv_mw'] or float(load_series.max()) * 2
        max_ess = s['max_ess_mwh'] or float(load_series.max()) * 4

        wind_candidates = [fixed_wind_mw] if fixed_wind_mw is not None else _arange(0, max_wind, s['coarse_step_mw'])
        pv_candidates = [fixed_pv_mw] if fixed_pv_mw is not None else _arange(0, max_pv, s['coarse_step_mw'])
        ess_candidates = _arange(0, max_ess, s['coarse_step_mwh'])

        best = self._grid_search(
            load_series, wind_unit_output, solar_unit_output,
            wind_candidates, pv_candidates, ess_candidates,
            green_ratio_min, self_use_ratio_min,
        )
        if best is None:
            raise ValueError(
                f'No feasible capacity plan found. '
                f'Constraints: green_ratio>={green_ratio_min}, self_use_ratio>={self_use_ratio_min}.'
            )

        r = s['fine_range_ratio']
        fine_wind = [fixed_wind_mw] if fixed_wind_mw is not None else _arange(
            max(0, best.wind_mw * (1 - r)),
            best.wind_mw * (1 + r) + s['fine_step_mw'],
            s['fine_step_mw'],
        )
        fine_pv = [fixed_pv_mw] if fixed_pv_mw is not None else _arange(
            max(0, best.pv_mw * (1 - r)),
            best.pv_mw * (1 + r) + s['fine_step_mw'],
            s['fine_step_mw'],
        )
        fine_ess = _arange(
            max(0, best.ess_mwh * (1 - r)),
            best.ess_mwh * (1 + r) + s['fine_step_mwh'],
            s['fine_step_mwh'],
        )

        refined = self._grid_search(
            load_series, wind_unit_output, solar_unit_output,
            fine_wind, fine_pv, fine_ess,
            green_ratio_min, self_use_ratio_min,
        )
        return refined if refined is not None else best

    def _grid_search(self, load_series, wind_unit_output, solar_unit_output,
                     wind_candidates, pv_candidates, ess_candidates,
                     green_ratio_min, self_use_ratio_min):
        best = None
        # 预计算单位年度出力和负荷总量（dt_h 在比值中约掉，直接用功率和）
        load_sum = float(load_series.sum())
        wind_unit_sum = float(wind_unit_output.sum())
        solar_unit_sum = float(solar_unit_output.sum())
        green_threshold = green_ratio_min * load_sum

        for w in wind_candidates:
            for p in pv_candidates:
                # 快速剪枝：即使完美储能也无法满足绿电比例约束
                if wind_unit_sum * w + solar_unit_sum * p < green_threshold:
                    continue
                for e in ess_candidates:
                    metrics = simulate_operation(
                        load_series, wind_unit_output, solar_unit_output,
                        w, p, e, self.sp,
                    )
                    if (metrics['green_ratio'] >= green_ratio_min
                            and metrics['self_use_ratio'] >= self_use_ratio_min):
                        ess_mw = e * self.sp.get('c_rate', 0.5)
                        cost = _compute_cost(w, p, e, self.cost)
                        candidate = CapacityPlanResult(
                            wind_mw=w, pv_mw=p, ess_mwh=e, ess_mw=ess_mw,
                            total_cost_wan=cost,
                            green_ratio=metrics['green_ratio'],
                            self_use_ratio=metrics['self_use_ratio'],
                            curtailment_ratio=metrics['curtailment_ratio'],
                            pv_monthly_kwh=_compute_pv_monthly(load_series, solar_unit_output, p),
                        )
                        if best is None or cost < best.total_cost_wan:
                            best = candidate
        return best


def simulate_operation(load_series, wind_unit_output, solar_unit_output,
                       wind_mw, pv_mw, ess_mwh, bess_params):
    load = load_series.values
    wind_u = wind_unit_output.values
    solar_u = solar_unit_output.values
    return _simulate_op(load, wind_u, solar_u, wind_mw, pv_mw, ess_mwh, bess_params)


def _simulate_op(load, wind_u, solar_u, wind_mw, pv_mw, ess_mwh, sp):
    eta_roundtrip = sp.get('eta_roundtrip')
    if eta_roundtrip is not None:
        eta_sqrt = float(eta_roundtrip) ** 0.5
        eta_ch = eta_sqrt
        eta_dis = eta_sqrt
    else:
        eta_ch = sp.get('eta_charge', 0.95)
        eta_dis = sp.get('eta_discharge', 0.95)
    soc_min_frac = sp.get('soc_min', 0.10)
    soc_max_frac = sp.get('soc_max', sp.get('dod', 0.90) + soc_min_frac)
    c_rate = sp.get('c_rate', 0.5)
    soc_init_frac = sp.get('soc_init_frac', 0.5)

    ess_mw_power = ess_mwh * c_rate
    soc_hi = ess_mwh * soc_max_frac
    soc_lo = ess_mwh * soc_min_frac
    soc = ess_mwh * soc_init_frac

    n = len(load)
    total_curtailment = 0.0
    total_grid_buy = 0.0
    total_green_gen = 0.0

    for i in range(n):
        gen = wind_u[i] * wind_mw + solar_u[i] * pv_mw
        total_green_gen += gen
        surplus = gen - load[i]

        if surplus >= 0:
            # charge battery with surplus, curtail remainder
            charge_dc = min(surplus * eta_ch, soc_hi - soc, ess_mw_power * eta_ch)
            charge_dc = max(0.0, charge_dc)
            soc += charge_dc
            curtailed = surplus - charge_dc / eta_ch
            total_curtailment += max(0.0, curtailed)
        else:
            # discharge battery to cover deficit, buy rest from grid
            deficit = -surplus
            dis_ac = min(deficit, (soc - soc_lo) * eta_dis, ess_mw_power)
            dis_ac = max(0.0, dis_ac)
            soc -= dis_ac / eta_dis
            grid = deficit - dis_ac
            total_grid_buy += grid

    total_load = float(np.sum(load))
    green_consumed = total_green_gen - total_curtailment
    green_ratio = green_consumed / total_load if total_load > 0 else 0.0
    self_use_ratio = green_consumed / total_green_gen if total_green_gen > 0 else 1.0
    curtailment_ratio = total_curtailment / total_green_gen if total_green_gen > 0 else 0.0

    return {
        'green_ratio': float(np.clip(green_ratio, 0, 1)),
        'self_use_ratio': float(np.clip(self_use_ratio, 0, 1)),
        'curtailment_ratio': float(np.clip(curtailment_ratio, 0, 1)),
        'total_green_gen_mwh': float(total_green_gen),
        'total_grid_buy_mwh': float(total_grid_buy),
        'total_curtailment_mwh': float(total_curtailment),
        'total_load_mwh': float(total_load),
        'green_consumed_mwh': float(green_consumed),
    }


def _compute_cost(wind_mw, pv_mw, ess_mwh, cost):
    wind_cost = wind_mw * 1000 * cost.get('wind_yuan_per_kw', 5000) / 10000
    pv_cost = pv_mw * 1000 * cost.get('pv_yuan_per_kw', 3500) / 10000
    ess_cost = ess_mwh * 1000 * cost.get('ess_yuan_per_kwh', 1500) / 10000
    return wind_cost + pv_cost + ess_cost


def _compute_pv_monthly(load_series, solar_unit_output, pv_mw):
    if pv_mw <= 0:
        return None
    try:
        dt_h = infer_dt_hours(load_series.index if isinstance(load_series.index, pd.DatetimeIndex) else load_series)
        solar_vals = solar_unit_output.values * pv_mw
        time_idx = load_series.index if isinstance(load_series.index, pd.DatetimeIndex) else pd.to_datetime(load_series)
        return monthly_kwh(time_idx, solar_vals, dt_h)
    except Exception:
        return None


def simple_energy_sanity_check(load_series, green_ratio_min=0.30, self_use_min=0.60,
                               yield_kwh_per_kwp=(1000, 1100, 1200, 1300)):
    """用固定年利用小时估算所需 PV MWp 下界。"""
    dt_h = infer_dt_hours(load_series.index)
    load_kwh_year = float(load_series.sum()) * dt_h
    gen_required = green_ratio_min * load_kwh_year / self_use_min
    return {
        'load_gwh_year': load_kwh_year / 1e6,
        'gen_required_gwh': gen_required / 1e6,
        'pv_required_table': pd.DataFrame([
            {'yield_kWh_per_kWp_yr': y, 'pv_required_MWp': gen_required / y / 1000}
            for y in yield_kwh_per_kwp
        ]),
    }


def curve_based_energy_check(load_series, solar_unit_output, green_ratio_min=0.30, self_use_min=0.60):
    """用实际单位 PV 曲线年发电量估算所需 PV MWp。"""
    dt_h = infer_dt_hours(load_series.index)
    load_kwh_year = float(load_series.sum()) * dt_h
    yield_per_kwp = float(solar_unit_output.sum()) * dt_h
    gen_required = green_ratio_min * load_kwh_year / self_use_min
    return {
        'load_gwh_year': load_kwh_year / 1e6,
        'yield_curve_kWh_per_kWp': yield_per_kwp,
        'pv_required_MWp': gen_required / yield_per_kwp / 1000,
    }


def _arange(lo, hi, step):
    if step <= 0 or lo > hi:
        return [lo]
    result = []
    v = lo
    while v <= hi + 1e-9:
        result.append(round(v, 6))
        v += step
    return result
