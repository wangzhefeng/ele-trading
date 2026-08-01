"""用户侧单节点调度场景适配层。

统一收录 PV / Wind / PV+BESS / Wind+BESS / Wind+PV+BESS 五类单节点场景的
薄包装。PV/Wind/±BESS 四类场景调用时只需把场景专属输入转成内核的
``renewable_forecast`` 字段,内核返回的 ``renewable_to_load`` 等字段直接透传
(无需重命名)。Wind+PV+BESS 因有 echo 字段,保留独立映射。
本模块不含求解逻辑,且不依赖 CVXPY,可无 CVXPY 直接导入。
"""

from __future__ import annotations

from typing import Any

from ele_trading.utils import clean_value

from ..interfaces import (
    UserSideBESSParams,
    UserSideDispatchPolicy,
    UserSidePVDispatchInput,
    UserSidePVDispatchResult,
    UserSidePVExportParams,
    UserSidePVBESSDispatchInput,
    UserSidePVBESSDispatchResult,
    UserSideRenewableBESSDispatchInput,
    UserSideRenewableDispatchInput,
    UserSideWindBESSDispatchInput,
    UserSideWindBESSDispatchResult,
    UserSideWindDispatchInput,
    UserSideWindDispatchResult,
    UserSideWindPVBESSDispatchInput,
    UserSideWindPVBESSDispatchResult,
)
from ..algorithms.user_side_renewable_bess_dispatch_class import run_user_side_renewable_bess_dispatch
from ..algorithms.user_side_renewable_dispatch_class import run_user_side_renewable_dispatch


# ---------------------------------------------------------------------------
# PV-only(无储能)
# ---------------------------------------------------------------------------
def run_user_side_pv_dispatch(dispatch_input: UserSidePVDispatchInput) -> UserSidePVDispatchResult:
    """Run deterministic user-side PV dispatch without storage."""
    _validate_renewable_input(
        dispatch_input.timestamps,
        dispatch_input.load_forecast,
        dispatch_input.renewable_forecast,
        dispatch_input.buy_price,
        dispatch_input.price_type,
        dispatch_input.step_hours,
        dispatch_input.demand_charge_rate,
        dispatch_input.export,
        "renewable_forecast",
    )
    return run_user_side_renewable_dispatch(dispatch_input)


# ---------------------------------------------------------------------------
# Wind-only(无储能)
# ---------------------------------------------------------------------------
def run_user_side_wind_dispatch(dispatch_input: UserSideWindDispatchInput) -> UserSideWindDispatchResult:
    """Run deterministic user-side wind dispatch without storage."""
    _validate_renewable_input(
        dispatch_input.timestamps,
        dispatch_input.load_forecast,
        dispatch_input.renewable_forecast,
        dispatch_input.buy_price,
        dispatch_input.price_type,
        dispatch_input.step_hours,
        dispatch_input.demand_charge_rate,
        dispatch_input.export,
        "renewable_forecast",
    )
    return run_user_side_renewable_dispatch(dispatch_input)


# ---------------------------------------------------------------------------
# PV + BESS
# ---------------------------------------------------------------------------
def run_user_side_pv_bess_dispatch(dispatch_input: UserSidePVBESSDispatchInput) -> UserSidePVBESSDispatchResult:
    """Run user-side PV + bess dispatch as a MILP."""
    _validate_renewable_input(
        dispatch_input.timestamps,
        dispatch_input.load_forecast,
        dispatch_input.renewable_forecast,
        dispatch_input.buy_price,
        dispatch_input.price_type,
        dispatch_input.step_hours,
        dispatch_input.demand_charge_rate,
        dispatch_input.export,
        "renewable_forecast",
    )
    _validate_bess_input(dispatch_input.bess, dispatch_input.initial_soc, dispatch_input.terminal_soc_target)
    _validate_policy(dispatch_input.policy)
    return run_user_side_renewable_bess_dispatch(dispatch_input)


# ---------------------------------------------------------------------------
# Wind + BESS
# ---------------------------------------------------------------------------
def run_user_side_wind_bess_dispatch(dispatch_input: UserSideWindBESSDispatchInput) -> UserSideWindBESSDispatchResult:
    """Run user-side wind + BESS dispatch as a MILP."""
    _validate_renewable_input(
        dispatch_input.timestamps,
        dispatch_input.load_forecast,
        dispatch_input.renewable_forecast,
        dispatch_input.buy_price,
        dispatch_input.price_type,
        dispatch_input.step_hours,
        dispatch_input.demand_charge_rate,
        dispatch_input.export,
        "renewable_forecast",
    )
    _validate_bess_input(dispatch_input.bess, dispatch_input.initial_soc, dispatch_input.terminal_soc_target)
    _validate_policy(dispatch_input.policy)
    return run_user_side_renewable_bess_dispatch(dispatch_input)


# ---------------------------------------------------------------------------
# Wind + PV + BESS(有 echo 字段,保留字段映射)
# ---------------------------------------------------------------------------
def run_user_side_wind_pv_bess_dispatch(dispatch_input: UserSideWindPVBESSDispatchInput) -> UserSideWindPVBESSDispatchResult:
    """Run user-side wind + PV + BESS dispatch as a MILP."""
    _validate_wind_pv_input(dispatch_input)
    renewable_forecast = [
        clean_value(pv + wind)
        for pv, wind in zip(dispatch_input.pv_forecast, dispatch_input.wind_forecast)
    ]
    renewable_result = run_user_side_renewable_bess_dispatch(
        UserSideRenewableBESSDispatchInput(
            timestamps=dispatch_input.timestamps,
            load_forecast=dispatch_input.load_forecast,
            renewable_forecast=renewable_forecast,
            buy_price=dispatch_input.buy_price,
            price_type=dispatch_input.price_type,
            export=dispatch_input.export,
            demand_charge_rate=dispatch_input.demand_charge_rate,
            step_hours=dispatch_input.step_hours,
            bess=dispatch_input.bess,
            initial_soc=dispatch_input.initial_soc,
            terminal_soc_target=dispatch_input.terminal_soc_target,
            cycle_cost_rate=dispatch_input.cycle_cost_rate,
            policy=dispatch_input.policy,
        )
    )

    return UserSideWindPVBESSDispatchResult(
        pv_forecast=dispatch_input.pv_forecast,
        wind_forecast=dispatch_input.wind_forecast,
        renewable_forecast=renewable_forecast,
        renewable_to_load=renewable_result.renewable_to_load,
        renewable_to_bess=renewable_result.renewable_to_bess,
        renewable_to_grid=renewable_result.renewable_to_grid,
        renewable_curtailment=renewable_result.renewable_curtailment,
        grid_to_load=renewable_result.grid_to_load,
        grid_to_bess=renewable_result.grid_to_bess,
        charge_power=renewable_result.charge_power,
        discharge_power=renewable_result.discharge_power,
        net_bess_power=renewable_result.net_bess_power,
        soc=renewable_result.soc,
        grid_import=renewable_result.grid_import,
        max_grid_import=renewable_result.max_grid_import,
        energy_cost=renewable_result.energy_cost,
        demand_cost=renewable_result.demand_cost,
        sell_revenue=renewable_result.sell_revenue,
        curtailment_cost=renewable_result.curtailment_cost,
        cycle_cost=renewable_result.cycle_cost,
        total_cost=renewable_result.total_cost,
        constraint_violations=renewable_result.constraint_violations,
    )


# ---------------------------------------------------------------------------
# 共享校验逻辑
# ---------------------------------------------------------------------------
def _validate_renewable_input(
    timestamps: list[Any],
    load_forecast: list[float],
    renewable_forecast: list[float],
    buy_price: list[float],
    price_type: list[str],
    step_hours: float,
    demand_charge_rate: float,
    export: UserSidePVExportParams,
    renewable_name: str,
) -> None:
    length = len(timestamps)
    if length == 0:
        raise ValueError("dispatch horizon must not be empty")
    if not (
        len(load_forecast)
        == len(renewable_forecast)
        == len(buy_price)
        == len(price_type)
        == length
    ):
        raise ValueError(
            f"timestamps, load_forecast, {renewable_name}, buy_price, and price_type "
            "must have the same length"
        )
    if step_hours <= 0:
        raise ValueError("step_hours must be positive")
    if demand_charge_rate < 0:
        raise ValueError("demand_charge_rate must be non-negative")
    if any(load < 0 for load in load_forecast):
        raise ValueError("load_forecast must be non-negative")
    if any(value < 0 for value in renewable_forecast):
        raise ValueError(f"{renewable_name} must be non-negative")
    if export.curtailment_cost_rate < 0:
        raise ValueError("export.curtailment_cost_rate must be non-negative")
    if export.export_limit is not None and export.export_limit < 0:
        raise ValueError("export.export_limit must be non-negative")


def _validate_bess_input(
    bess: UserSideBESSParams,
    initial_soc: float,
    terminal_soc_target: float | None,
) -> None:
    if bess.capacity <= 0:
        raise ValueError("bess.capacity must be positive")
    if bess.soc_min < 0 or bess.soc_max > bess.capacity:
        raise ValueError("bess SOC bounds must be within bess capacity")
    if bess.soc_min > bess.soc_max:
        raise ValueError("bess.soc_min must be less than or equal to bess.soc_max")
    if not bess.soc_min <= initial_soc <= bess.soc_max:
        raise ValueError("initial_soc must be within bess SOC bounds")
    if terminal_soc_target is not None and not (
        bess.soc_min <= terminal_soc_target <= bess.soc_max
    ):
        raise ValueError("terminal_soc_target must be within bess SOC bounds")
    if bess.p_ch_max < 0 or bess.p_dis_max < 0:
        raise ValueError("bess power limits must be non-negative")
    if bess.eta_ch <= 0 or bess.eta_dis <= 0:
        raise ValueError("bess efficiencies must be positive")


def _validate_wind_pv_input(dispatch_input: UserSideWindPVBESSDispatchInput) -> None:
    length = len(dispatch_input.timestamps)
    if not (len(dispatch_input.pv_forecast) == len(dispatch_input.wind_forecast) == length):
        raise ValueError("timestamps, pv_forecast, and wind_forecast must have the same length")
    if any(pv < 0 for pv in dispatch_input.pv_forecast):
        raise ValueError("pv_forecast must be non-negative")
    if any(wind < 0 for wind in dispatch_input.wind_forecast):
        raise ValueError("wind_forecast must be non-negative")


def _validate_policy(policy: UserSideDispatchPolicy | None) -> None:
    if policy is None:
        return
    for name, hours in (
        ("charge_allowed_hours", policy.charge_allowed_hours),
        ("discharge_allowed_hours", policy.discharge_allowed_hours),
    ):
        if hours is None:
            continue
        if any(hour < 0 or hour > 23 for hour in hours):
            raise ValueError(f"policy.{name} must contain hours in [0, 23]")
    if policy.pv_to_bess_reward_rate < 0:
        raise ValueError("policy.pv_to_bess_reward_rate must be non-negative")
    if policy.pv_to_load_reward_rate < 0:
        raise ValueError("policy.pv_to_load_reward_rate must be non-negative")
    if policy.pv_export_penalty_rate < 0:
        raise ValueError("policy.pv_export_penalty_rate must be non-negative")
