from __future__ import annotations

import numpy as np

from ba_eva.eva_PV_optim_version.storage_optim_common import PlanConfigFast
from ba_eva.eva_PV_optim_version.storage_optim_PV_BESS import (
    _dispatch_annual,
    _dispatch_annual_numba,
)
from ba_eva.eva_PV_optim_version.storage_optim_Wind_PV_BESS_2 import (
    _dispatch_annual_fast_numba,
)


def _legacy_pv_dispatch_numba(
    load_kw,
    pv_kw,
    dt_hours,
    batt_kwh,
    eta_roundtrip,
    c_rate,
    soc_init_frac,
    soc_min_frac,
    soc_max_frac,
):
    pv_gen = pv_used = load_e = direct_e = bess_dis = 0.0

    if batt_kwh <= 0.0:
        for i in range(load_kw.shape[0]):
            load = max(load_kw[i], 0.0)
            pv = max(pv_kw[i], 0.0)
            load_e += load * dt_hours
            pv_gen += pv * dt_hours
            direct = pv if pv < load else load
            pv_used += direct * dt_hours
            direct_e += direct * dt_hours
        return pv_gen, pv_used, load_e, direct_e, bess_dis

    soc = soc_init_frac * batt_kwh
    soc_min = soc_min_frac * batt_kwh
    soc_max = soc_max_frac * batt_kwh
    pmax = c_rate * batt_kwh
    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5

    if soc < soc_min:
        soc = soc_min
    if soc > soc_max:
        soc = soc_max

    for i in range(load_kw.shape[0]):
        load = max(load_kw[i], 0.0)
        pv = max(pv_kw[i], 0.0)

        load_e += load * dt_hours
        pv_gen += pv * dt_hours

        direct = pv if pv < load else load
        pv_used += direct * dt_hours
        direct_e += direct * dt_hours

        surplus = pv - direct
        deficit = load - direct

        if surplus > 1e-12 and soc < soc_max:
            charge_p = min(surplus, pmax, (soc_max - soc) / dt_hours)
            soc += charge_p * dt_hours * eta_c

        if deficit > 1e-12 and soc > soc_min:
            discharge_p = min(deficit, pmax, (soc - soc_min) * eta_d / dt_hours)
            soc -= discharge_p * dt_hours / eta_d
            pv_used += discharge_p * dt_hours
            bess_dis += discharge_p * dt_hours

    return pv_gen, pv_used, load_e, direct_e, bess_dis


def _legacy_wind_pv_dispatch_numba(
    load_kw,
    wind_kw,
    pv_kw,
    other_kw,
    dt_hours,
    batt_kwh,
    eta_roundtrip,
    c_rate,
    soc_init_frac,
    soc_min_frac,
    soc_max_frac,
):
    gen_e = used_e = load_e = direct_e = bess_dis = 0.0

    eta_c = eta_roundtrip ** 0.5
    eta_d = eta_roundtrip ** 0.5
    energy = batt_kwh
    pmax = c_rate * energy

    soc_min = soc_min_frac * energy
    soc_max = soc_max_frac * energy
    soc = soc_init_frac * energy
    if soc < soc_min:
        soc = soc_min
    if soc > soc_max:
        soc = soc_max

    for i in range(load_kw.shape[0]):
        load = load_kw[i]
        if load < 0.0:
            load = 0.0

        gen = wind_kw[i] + pv_kw[i] + other_kw[i]
        if gen < 0.0:
            gen = 0.0

        load_e += load * dt_hours
        gen_e += gen * dt_hours

        direct = load if load < gen else gen
        used_e += direct * dt_hours
        direct_e += direct * dt_hours

        surplus = gen - direct
        deficit = load - direct

        if surplus > 1e-9 and soc < soc_max:
            charge_p = surplus
            if charge_p > pmax:
                charge_p = pmax
            max_ch = (soc_max - soc) / dt_hours
            if charge_p > max_ch:
                charge_p = max_ch
            soc += charge_p * dt_hours * eta_c

        if deficit > 1e-9 and soc > soc_min:
            discharge_p = deficit
            if discharge_p > pmax:
                discharge_p = pmax
            max_dis = (soc - soc_min) * eta_d / dt_hours
            if discharge_p > max_dis:
                discharge_p = max_dis
            soc -= discharge_p * dt_hours / eta_d
            used_e += discharge_p * dt_hours
            bess_dis += discharge_p * dt_hours

    return gen_e, used_e, load_e, direct_e, bess_dis


def test_dispatch_annual_numba_matches_legacy_logic():
    load_kw = np.array([10.0, -2.0, 5.0, 12.0], dtype=np.float64)
    pv_kw = np.array([3.0, 6.0, -1.0, 14.0], dtype=np.float64)

    expected = _legacy_pv_dispatch_numba(
        load_kw, pv_kw, 1.0, 8.0, 0.92, 0.5, 1.2, 0.1, 0.9
    )
    actual = _dispatch_annual_numba(
        load_kw, pv_kw, 1.0, 8.0, 0.92, 0.5, 1.2, 0.1, 0.9
    )

    np.testing.assert_allclose(actual, expected)


def test_dispatch_annual_numba_matches_legacy_logic_without_battery():
    load_kw = np.array([10.0, 0.0, 5.0, 12.0], dtype=np.float64)
    pv_kw = np.array([3.0, 6.0, 0.0, 14.0], dtype=np.float64)

    expected = _legacy_pv_dispatch_numba(
        load_kw, pv_kw, 1.0, 0.0, 0.92, 0.5, 0.5, 0.1, 0.9
    )
    actual = _dispatch_annual_numba(
        load_kw, pv_kw, 1.0, 0.0, 0.92, 0.5, 0.5, 0.1, 0.9
    )

    np.testing.assert_allclose(actual, expected)


def test_dispatch_annual_wrapper_preserves_fields_without_numba():
    load_kw = np.array([10.0, -2.0, 5.0, 12.0], dtype=np.float64)
    pv_kw = np.array([3.0, 6.0, -1.0, 14.0], dtype=np.float64)
    cfg = PlanConfigFast(
        use_numba=False,
        eta_roundtrip=0.92,
        c_rate=0.5,
        soc_init_frac=1.2,
        soc_min_frac=0.1,
        soc_max_frac=0.9,
    )

    stats = _dispatch_annual(load_kw, pv_kw, 1.0, 8.0, cfg)

    assert set(stats) == {
        "pv_gen_kwh",
        "pv_used_kwh",
        "load_kwh",
        "direct_used_kwh",
        "bess_discharge_kwh",
    }
    assert stats["direct_used_kwh"] == stats["pv_used_kwh"] - stats["bess_discharge_kwh"]
    assert stats["bess_discharge_kwh"] == 0.0


def test_dispatch_annual_fast_numba_matches_legacy_logic():
    load_kw = np.array([8.0, -1.0, 6.0, 3.0], dtype=np.float64)
    wind_kw = np.array([2.0, 1.0, 0.0, 4.0], dtype=np.float64)
    pv_kw = np.array([1.0, -2.0, 9.0, 0.5], dtype=np.float64)
    other_kw = np.array([0.0, 0.0, 1.0, -1.0], dtype=np.float64)

    expected = _legacy_wind_pv_dispatch_numba(
        load_kw, wind_kw, pv_kw, other_kw, 0.5, 6.0, 0.92, 0.5, -0.2, 0.1, 0.8
    )
    actual = _dispatch_annual_fast_numba(
        load_kw, wind_kw, pv_kw, other_kw, 0.5, 6.0, 0.92, 0.5, -0.2, 0.1, 0.8
    )

    np.testing.assert_allclose(actual, expected)


def test_dispatch_annual_fast_numba_matches_legacy_logic_without_battery():
    load_kw = np.array([8.0, 4.0, 6.0, 3.0], dtype=np.float64)
    wind_kw = np.zeros(4, dtype=np.float64)
    pv_kw = np.array([1.0, 2.0, 9.0, 0.5], dtype=np.float64)
    other_kw = np.zeros(4, dtype=np.float64)

    expected = _legacy_wind_pv_dispatch_numba(
        load_kw, wind_kw, pv_kw, other_kw, 0.5, 0.0, 0.92, 0.5, 0.5, 0.1, 0.8
    )
    actual = _dispatch_annual_fast_numba(
        load_kw, wind_kw, pv_kw, other_kw, 0.5, 0.0, 0.92, 0.5, 0.5, 0.1, 0.8
    )

    np.testing.assert_allclose(actual, expected)
