from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from investment_estimation.utils.data_alignment import as_time_series, normalize_time_and_load
from investment_estimation.utils.time_index import infer_dt_hours
from .interfaces import UserSideBESSParams, CvxpBESSDispatchInput
from .models.cvxp_bess_dispatch import (
    CvxpBESSDispatcher,
    get_cvxp_profile,
)

from .models.simulation_model import BESSSimulationConfig, EssSimulationModel


@dataclass(slots=True)
class UnitsConfig:
    load_power: str = "kW"


@dataclass(slots=True)
class BESSPlanConfig:
    max_demand_price: float = 40.8
    transform_capacity: float = 0.0
    current_soc_kwh: float = 0.0
    batt_hi_max_kwh: float = 1e4
    search_points: int = 20
    version: str = "optim"
    time_splitting: str | None = None
    c_rate: float = 0.5
    usable_depth: float = 0.9
    charge_efficiency: float = 0.92
    discharge_efficiency: float = 0.95


@dataclass(slots=True)
class BESSCapacityResult:
    feasible: bool
    bess_kwh: float = 0.0
    power_kw: float = 0.0
    revenue: float = 0.0
    max_demand_rise_cost: float = 0.0
    total_energy: float = 0.0
    ori_cost: float = 0.0
    opt_cost: float = 0.0
    charge_energy: float = 0.0
    discharge_energy: float = 0.0
    charge_balance: float = 0.0
    discharge_balance: float = 0.0
    objective_value: float = 0.0
    profile_name: str = ""
    time_splitting: str = ""
    schedule_df: pd.DataFrame | None = None
    es_charge_df: pd.DataFrame | None = None
    es_soc_df: pd.DataFrame | None = None
    total_load_df: pd.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)
    diagnosis: dict[str, Any] | None = None


_PROFILE_TIME_SPLITTING = {
    "without_demand": "month",
    "basic": "day",
    "optim": "month",
}


def _normalize_ele_price(
    ele_price: pd.Series | pd.DataFrame | None,
    timestamps: pd.Series,
    *,
    time_col: str,
) -> tuple[np.ndarray, list[str], pd.DataFrame]:
    if ele_price is None:
        raise ValueError("ele_price is required for bess_capacity_operating_planner")

    price_series = as_time_series(
        ele_price,
        time_col,
        ("value", "ele_price", "ele_prices", "price", "price_yuan_per_kwh"),
        1.0,
    )
    target_index = pd.DatetimeIndex(pd.to_datetime(timestamps))
    price_series = price_series.copy()
    price_series.index = pd.to_datetime(price_series.index)
    aligned_price = price_series.reindex(target_index).interpolate("time").ffill().bfill()

    if isinstance(ele_price, pd.DataFrame):
        if time_col in ele_price.columns:
            type_index = pd.to_datetime(ele_price[time_col])
            type_df = ele_price.set_index(type_index)
        else:
            type_df = ele_price.copy()
            type_df.index = pd.to_datetime(type_df.index)
        type_col = next((col for col in ("type", "price_type", "ele_type") if col in type_df.columns), None)
        if type_col is None:
            aligned_type = pd.Series([""] * len(target_index), index=target_index)
        else:
            aligned_type = type_df[type_col].astype(str).reindex(target_index).ffill().bfill().fillna("")
    else:
        aligned_type = pd.Series([""] * len(target_index), index=target_index)

    price_df = pd.DataFrame({"value": aligned_price.to_numpy(), "type": aligned_type.to_list()}, index=target_index)
    return aligned_price.to_numpy(dtype=float), aligned_type.astype(str).to_list(), price_df


def _get_time_ranges(start_time: pd.Timestamp, end_time: pd.Timestamp, strategy: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    if strategy == "day":
        ranges = []
        current = pd.Timestamp(start_time)
        while current < end_time:
            next_time = current + pd.Timedelta(days=1)
            ranges.append((current, min(next_time, end_time)))
            current = next_time
        return ranges

    if strategy == "month":
        ranges = []
        current = pd.Timestamp(start_time).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        while current < end_time:
            if current.month == 12:
                next_time = current.replace(year=current.year + 1, month=1, day=1)
            else:
                next_time = current.replace(month=current.month + 1, day=1)
            ranges.append((current, min(next_time, end_time)))
            current = next_time
        return ranges

    raise ValueError(f"Unknown time splitting strategy: {strategy}")


def _strategy_from_dispatch_result(
    timestamps: list[pd.Timestamp],
    net_power: list[float],
) -> pd.DataFrame:
    return pd.DataFrame({"value": np.asarray(net_power, dtype=float)}, index=pd.to_datetime(timestamps))


def _monthly_max_load(df: pd.DataFrame) -> tuple[list[float], list[float]]:
    if "total_load" not in df.columns:
        raise KeyError("DataFrame must have a 'total_load' column.")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame index must be a DatetimeIndex.")
    monthly_total_load_max = df["total_load"].resample("ME").max()
    monthly_demand_load_max = df["demand_load"].resample("ME").max()
    return monthly_total_load_max.tolist(), monthly_demand_load_max.tolist()


def _zero_schedule(timestamps: pd.Series) -> pd.DataFrame:
    index = pd.DatetimeIndex(pd.to_datetime(timestamps))
    return pd.DataFrame({"value": np.zeros(len(index), dtype=float)}, index=index)


def _simulate_single_capacity(
    df_load: pd.DataFrame,
    *,
    ele_price: pd.Series | pd.DataFrame,
    bess_kwh: float,
    time_col: str,
    load_col: str,
    cfg: BESSPlanConfig,
    units: UnitsConfig,
) -> dict[str, Any]:
    profile = get_cvxp_profile(cfg.version)
    time_splitting = cfg.time_splitting or _PROFILE_TIME_SPLITTING[cfg.version]

    t, load_kw, warnings = normalize_time_and_load(df_load, time_col, load_col, units.load_power)
    if len(t) < 2:
        raise ValueError("dispatch horizon must contain at least two time points")
    dt_hours = infer_dt_hours(t)
    freq_minutes = int(round(dt_hours * 60))
    aligned_price, ele_types, ele_price_df = _normalize_ele_price(ele_price, t, time_col=time_col)

    load_df = pd.DataFrame({"time": pd.to_datetime(t), "value": load_kw})
    power_kw = bess_kwh * cfg.c_rate
    soc_max = bess_kwh * cfg.usable_depth
    current_soc = min(max(cfg.current_soc_kwh, 0.0), soc_max)

    schedule_list: list[pd.DataFrame] = []
    objective_value = 0.0
    time_ranges = _get_time_ranges(pd.Timestamp(t.iloc[0]), pd.Timestamp(t.iloc[-1]) + pd.Timedelta(minutes=freq_minutes), time_splitting)
    for start_time, end_time in time_ranges:
        segment_mask = (load_df["time"] >= start_time) & (load_df["time"] < end_time)
        step_load_df = load_df.loc[segment_mask]
        if step_load_df.empty:
            continue
        price_mask = (ele_price_df.index >= start_time) & (ele_price_df.index < end_time)
        step_price_df = ele_price_df.loc[price_mask]

        if bess_kwh <= 0 or power_kw <= 0 or soc_max <= 0:
            schedule_list.append(_zero_schedule(step_load_df["time"]))
            continue

        dispatch_input = CvxpBESSDispatchInput(
            timestamps=step_load_df["time"].to_list(),
            demand_load=step_load_df["value"].astype(float).to_list(),
            ele_prices=step_price_df["value"].astype(float).to_list(),
            ele_types=step_price_df["type"].astype(str).to_list(),
            bess=UserSideBESSParams(
                capacity=float(bess_kwh),
                soc_min=0.0,
                soc_max=float(soc_max),
                p_ch_max=float(power_kw),
                p_dis_max=float(power_kw),
                eta_ch=float(cfg.charge_efficiency),
                eta_dis=float(cfg.discharge_efficiency),
            ),
            initial_soc=float(current_soc),
            max_demand_price=float(cfg.max_demand_price),
            freq_minutes=freq_minutes,
            profile=profile,
            transform_capacity=float(cfg.transform_capacity),
        )
        dispatch_result = CvxpBESSDispatcher(dispatch_input).solve()
        objective_value += float(dispatch_result.objective_value)
        schedule_list.append(_strategy_from_dispatch_result(dispatch_input.timestamps, dispatch_result.net_power))
        if dispatch_result.soc:
            current_soc = float(dispatch_result.soc[-1])

    if not schedule_list:
        raise ValueError("no dispatch windows were generated")

    schedule_df = pd.concat(schedule_list).sort_index()
    demand_load_df = pd.DataFrame({"value": load_kw}, index=pd.to_datetime(t))

    transform_capacity = float(cfg.transform_capacity) if cfg.transform_capacity > 0 else float("inf")
    simulation_model = EssSimulationModel(
        BESSSimulationConfig(
            transform_capacity=transform_capacity,
            battery_capacity_kwh=float(bess_kwh),
            max_charge_power_kw=float(power_kw),
            max_discharge_power_kw=float(power_kw),
            charge_efficiency=float(cfg.charge_efficiency),
            discharge_efficiency=float(cfg.discharge_efficiency),
            usable_depth=float(cfg.usable_depth),
            soc_redundant_ratio=0.0,
            invert_band_kw=0.0,
        )
    )
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(
        demand_load_df, schedule_df, cfg.current_soc_kwh
    )
    origin_balance, opt_balance = simulation_model.revenue_calculation(
        demand_load_df, es_charge_df, ele_price_df[["value"]], cfg.max_demand_price
    )
    opt_max_demand_load_list, ori_max_demand_load_list = _monthly_max_load(total_load_df)
    opt_max_demand_cost = cfg.max_demand_price * sum(opt_max_demand_load_list)
    ori_max_demand_cost = cfg.max_demand_price * sum(ori_max_demand_load_list)

    max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost
    revenue = origin_balance - opt_balance - max_demand_rise_cost
    total_energy = float(demand_load_df["value"].sum())
    ori_cost = origin_balance + ori_max_demand_cost
    opt_cost = opt_balance + opt_max_demand_cost

    es_charge_df = es_charge_df.copy()
    es_charge_df["price"] = ele_price_df["value"]
    es_charge_df["balance"] = es_charge_df["value"] * es_charge_df["price"]
    charge_energy = -es_charge_df.loc[es_charge_df["value"] < 0, "value"].sum()
    discharge_energy = es_charge_df.loc[es_charge_df["value"] > 0, "value"].sum()
    charge_balance = -es_charge_df.loc[es_charge_df["balance"] < 0, "balance"].sum()
    discharge_balance = es_charge_df.loc[es_charge_df["balance"] > 0, "balance"].sum()

    return {
        "bess_kwh": float(bess_kwh),
        "power_kw": float(power_kw),
        "revenue": float(revenue),
        "max_demand_rise_cost": float(max_demand_rise_cost),
        "total_energy": total_energy,
        "ori_cost": float(ori_cost),
        "opt_cost": float(opt_cost),
        "charge_energy": float(charge_energy),
        "discharge_energy": float(discharge_energy),
        "charge_balance": float(charge_balance),
        "discharge_balance": float(discharge_balance),
        "objective_value": float(objective_value),
        "profile_name": cfg.version,
        "time_splitting": time_splitting,
        "schedule_df": schedule_df,
        "es_charge_df": es_charge_df,
        "es_soc_df": es_soc_df,
        "total_load_df": total_load_df,
        "warnings": warnings,
    }


def simulate_bess_operation(
    df_load: pd.DataFrame,
    *,
    ele_price: pd.Series | pd.DataFrame | None,
    bess_kwh: float,
    pv_power: pd.Series | pd.DataFrame | None = None,
    wind_input: pd.Series | pd.DataFrame | None = None,
    time_col: str = "Time",
    load_col: str = "P_kw",
    cfg: BESSPlanConfig = BESSPlanConfig(),
    units: UnitsConfig = UnitsConfig(),
) -> dict[str, Any]:
    _ = pv_power, wind_input
    return _simulate_single_capacity(
        df_load,
        ele_price=ele_price,
        bess_kwh=bess_kwh,
        time_col=time_col,
        load_col=load_col,
        cfg=cfg,
        units=units,
    )


def plan_energy_system(
    df_load: pd.DataFrame,
    *,
    ele_price: pd.Series | pd.DataFrame | None = None,
    pv_power: pd.Series | pd.DataFrame | None = None,
    wind_input: pd.Series | pd.DataFrame | None = None,
    time_col: str = "Time",
    load_col: str = "P_kw",
    cfg: BESSPlanConfig = BESSPlanConfig(),
    units: UnitsConfig = UnitsConfig(),
) -> BESSCapacityResult:
    _ = pv_power, wind_input
    if ele_price is None:
        raise ValueError("ele_price is required for bess_capacity_operating_planner")

    best: dict[str, Any] | None = None
    for bess_kwh in np.linspace(0.0, cfg.batt_hi_max_kwh, cfg.search_points):
        stats = _simulate_single_capacity(
            df_load,
            ele_price=ele_price,
            bess_kwh=float(bess_kwh),
            time_col=time_col,
            load_col=load_col,
            cfg=cfg,
            units=units,
        )
        if best is None or stats["revenue"] > best["revenue"] or (
            np.isclose(stats["revenue"], best["revenue"]) and stats["bess_kwh"] < best["bess_kwh"]
        ):
            best = stats

    if best is None:
        return BESSCapacityResult(
            feasible=False,
            diagnosis={"reason": "NO_FEASIBLE_SOLUTION", "msg": "No capacity candidates were evaluated."},
        )

    return BESSCapacityResult(
        feasible=True,
        bess_kwh=best["bess_kwh"],
        power_kw=best["power_kw"],
        revenue=best["revenue"],
        max_demand_rise_cost=best["max_demand_rise_cost"],
        total_energy=best["total_energy"],
        ori_cost=best["ori_cost"],
        opt_cost=best["opt_cost"],
        charge_energy=best["charge_energy"],
        discharge_energy=best["discharge_energy"],
        charge_balance=best["charge_balance"],
        discharge_balance=best["discharge_balance"],
        objective_value=best["objective_value"],
        profile_name=best["profile_name"],
        time_splitting=best["time_splitting"],
        schedule_df=best["schedule_df"],
        es_charge_df=best["es_charge_df"],
        es_soc_df=best["es_soc_df"],
        total_load_df=best["total_load_df"],
        warnings=best["warnings"],
    )
