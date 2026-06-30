"""Shared project-finance helpers for capacity planning IRR workflows."""
from __future__ import annotations

from dataclasses import dataclass
import math

from ele_trading.evaluation.metrics import compute_irr


@dataclass(slots=True)
class LevelizedIRRResult:
    total_capex_yuan: float
    annual_revenue_yuan: float
    annual_opex_yuan: float
    annual_cashflow_yuan: float
    irr: float
    cashflows: list[float]


@dataclass(slots=True)
class OwnerPriceBacksolveResult:
    green_price_yuan_per_kwh: float
    ppa_price_yuan_per_kwh: float
    owner_avg_price_yuan_per_kwh: float
    annual_grid_buy_kwh: float


@dataclass(slots=True)
class TargetIRRGapMetrics:
    target_irr: float
    required_annual_cashflow_yuan: float
    actual_annual_cashflow_yuan: float
    annual_cashflow_gap_yuan: float
    required_green_price_yuan_per_kwh: float
    required_ppa_price_yuan_per_kwh: float
    required_owner_avg_price_yuan_per_kwh: float
    owner_avg_price_delta_yuan_per_kwh: float
    max_capex_for_target_irr_yuan: float
    capex_reduction_needed_yuan: float
    capex_reduction_needed_ratio: float


@dataclass(slots=True)
class DegradedIRRResult:
    capex_yuan: float
    annual_revenues_yuan: list[float]
    annual_opexes_yuan: list[float]
    cashflows: list[float]
    irr: float
    life_revenue_yuan: float
    life_net_yuan: float


def evaluate_levelized_irr(
    *,
    total_capex_yuan: float,
    annual_revenue_yuan: float,
    annual_opex_yuan: float,
    life_years: int,
) -> LevelizedIRRResult:
    """Evaluate IRR for an initial investment plus equal annual cashflow."""
    annual_cashflow = float(annual_revenue_yuan) - float(annual_opex_yuan)
    cashflows = [-float(total_capex_yuan)] + [annual_cashflow] * int(life_years)
    irr = compute_irr(cashflows)
    return LevelizedIRRResult(
        total_capex_yuan=float(total_capex_yuan),
        annual_revenue_yuan=float(annual_revenue_yuan),
        annual_opex_yuan=float(annual_opex_yuan),
        annual_cashflow_yuan=float(annual_cashflow),
        irr=float(irr),
        cashflows=cashflows,
    )


def backsolve_green_ppa_price(
    *,
    load_kwh: float,
    green_used_kwh: float,
    target_owner_price_yuan_per_kwh: float,
    grid_buy_price_yuan_per_kwh: float,
    green_price_adder_yuan_per_kwh: float,
) -> OwnerPriceBacksolveResult:
    """Back-solve green settlement and PPA prices from target owner average price."""
    if green_used_kwh <= 0:
        raise ValueError("green_used_kwh must be positive")
    if load_kwh <= 0:
        raise ValueError("load_kwh must be positive")

    grid_buy_kwh = max(float(load_kwh) - float(green_used_kwh), 0.0)
    green_price = (
        float(target_owner_price_yuan_per_kwh) * float(load_kwh)
        - float(grid_buy_price_yuan_per_kwh) * grid_buy_kwh
    ) / float(green_used_kwh)
    ppa_price = green_price - float(green_price_adder_yuan_per_kwh)
    owner_avg_price = (
        green_price * float(green_used_kwh)
        + float(grid_buy_price_yuan_per_kwh) * grid_buy_kwh
    ) / float(load_kwh)
    return OwnerPriceBacksolveResult(
        green_price_yuan_per_kwh=float(green_price),
        ppa_price_yuan_per_kwh=float(ppa_price),
        owner_avg_price_yuan_per_kwh=float(owner_avg_price),
        annual_grid_buy_kwh=float(grid_buy_kwh),
    )


def required_levelized_cashflow(
    *,
    total_capex_yuan: float,
    target_irr: float,
    life_years: int,
) -> float:
    """Back-solve equal annual cashflow required to hit a target IRR."""
    if total_capex_yuan <= 0 or life_years <= 0:
        return 0.0
    if abs(target_irr) <= 1e-12:
        return float(total_capex_yuan) / int(life_years)
    factor = float(target_irr) / (1.0 - (1.0 + float(target_irr)) ** (-int(life_years)))
    return float(total_capex_yuan) * factor


def compute_target_irr_gap_metrics(
    *,
    total_capex_yuan: float,
    annual_cashflow_yuan: float,
    annual_opex_yuan: float,
    green_used_kwh: float,
    grid_buy_kwh: float,
    target_irr: float,
    life_years: int,
    grid_buy_price_yuan_per_kwh: float,
    green_price_adder_yuan_per_kwh: float,
    target_owner_price_yuan_per_kwh: float,
) -> TargetIRRGapMetrics:
    """Compute target-IRR cashflow and price gap diagnostics."""
    load_kwh = float(green_used_kwh) + float(grid_buy_kwh)
    required_cf = required_levelized_cashflow(
        total_capex_yuan=float(total_capex_yuan),
        target_irr=float(target_irr),
        life_years=int(life_years),
    )
    if green_used_kwh > 1e-9:
        required_green_price = (required_cf + float(annual_opex_yuan)) / float(green_used_kwh)
        required_ppa_price = required_green_price - float(green_price_adder_yuan_per_kwh)
        required_owner_avg_price = (
            required_green_price * float(green_used_kwh)
            + float(grid_buy_price_yuan_per_kwh) * float(grid_buy_kwh)
        ) / load_kwh if load_kwh > 1e-9 else math.nan
    else:
        required_green_price = math.nan
        required_ppa_price = math.nan
        required_owner_avg_price = math.nan

    required_cf_per_capex = required_levelized_cashflow(
        total_capex_yuan=1.0,
        target_irr=float(target_irr),
        life_years=int(life_years),
    )
    max_capex_for_target = (
        float(annual_cashflow_yuan) / required_cf_per_capex
        if annual_cashflow_yuan > 0 and required_cf_per_capex > 0
        else 0.0
    )
    capex_reduction = max(float(total_capex_yuan) - max_capex_for_target, 0.0)

    return TargetIRRGapMetrics(
        target_irr=float(target_irr),
        required_annual_cashflow_yuan=float(required_cf),
        actual_annual_cashflow_yuan=float(annual_cashflow_yuan),
        annual_cashflow_gap_yuan=float(required_cf - float(annual_cashflow_yuan)),
        required_green_price_yuan_per_kwh=float(required_green_price),
        required_ppa_price_yuan_per_kwh=float(required_ppa_price),
        required_owner_avg_price_yuan_per_kwh=float(required_owner_avg_price),
        owner_avg_price_delta_yuan_per_kwh=float(required_owner_avg_price - float(target_owner_price_yuan_per_kwh)),
        max_capex_for_target_irr_yuan=float(max_capex_for_target),
        capex_reduction_needed_yuan=float(capex_reduction),
        capex_reduction_needed_ratio=float(capex_reduction / float(total_capex_yuan)) if total_capex_yuan > 1e-9 else 0.0,
    )


def evaluate_degraded_irr(
    *,
    capex_yuan: float,
    annual_revenue_y1_yuan: float,
    annual_opex_y1_yuan: float,
    life_years: int,
    capacity_end_ratio: float,
) -> DegradedIRRResult:
    """Evaluate IRR when annual revenue and OPEX decay with capacity."""
    if life_years > 1:
        step = (1.0 - float(capacity_end_ratio)) / (int(life_years) - 1)
    else:
        step = 0.0
    year_ratios = [max(float(capacity_end_ratio), 1.0 - step * y) for y in range(int(life_years))]
    revenues = [float(annual_revenue_y1_yuan) * r for r in year_ratios]
    opexes = [float(annual_opex_y1_yuan) * r for r in year_ratios]
    annual_net = [revenues[y] - opexes[y] for y in range(int(life_years))]
    cashflows = [-float(capex_yuan)] + annual_net
    return DegradedIRRResult(
        capex_yuan=float(capex_yuan),
        annual_revenues_yuan=revenues,
        annual_opexes_yuan=opexes,
        cashflows=cashflows,
        irr=float(compute_irr(cashflows)),
        life_revenue_yuan=float(sum(revenues)),
        life_net_yuan=float(sum(annual_net)),
    )
