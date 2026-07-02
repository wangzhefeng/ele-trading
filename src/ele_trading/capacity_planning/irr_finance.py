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


def compute_npv(cashflows: list[float], discount_rate: float) -> float:
    """净现值：``Σ cf_t / (1+discount_rate)^t``。"""
    return sum(cf / ((1.0 + discount_rate) ** t) for t, cf in enumerate(cashflows))


def compute_payback_year(cashflows: list[float]) -> float | None:
    """静态/动态回收期（线性插值 cum cashflow 跨零点）；不跨零返回 None。"""
    cum = 0.0
    for t, cf in enumerate(cashflows):
        prev = cum
        cum += cf
        if prev < 0 <= cum:
            return (t - 1) + (-prev) / cf if cf != 0 else float(t - 1)
    return None


@dataclass
class ReplacementEvent:
    """储能更换事件（第 year 年注入 cost_yuan 负现金流）。"""

    year: int
    cost_yuan: float


@dataclass(slots=True)
class ProjectCashflowResult:
    """逐年项目现金流结果（项目 IRR，无杠杆；权益层字段预留）。"""

    capex_yuan: float
    annual_revenues_yuan: list[float]
    annual_opexes_yuan: list[float]
    annual_taxes_yuan: list[float]
    replacement_events_yuan: list[float]
    salvage_yuan: float
    cashflows: list[float]
    irr: float | None
    npv_yuan: float | None = None
    payback_year: float | None = None
    # 权益层预留（Q3），本轮不填实现。
    debt_service_yuan: list[float] | None = None
    equity_irr: float | None = None


def build_project_cashflows(
    *,
    capex_yuan: float,
    annual_revenue_y1_yuan: float,
    annual_opex_y1_yuan: float,
    life_years: int,
    capacity_degradation: list[float] | None = None,
    tax_rate: float = 0.0,
    depreciation_years: int | None = None,
    replacements: list[ReplacementEvent] | None = None,
    salvage_ratio: float = 0.0,
    discount_rate: float | None = None,
) -> ProjectCashflowResult:
    """构造逐年项目现金流：CAPEX + (收入-运维-税-更换)×N + 残值。

    - 收入/运维按 ``capacity_degradation`` 曲线逐年衰减（默认不衰减，退化模型见 ``evaluate_degraded_irr``）；
    - 税 = ``max(0, (收入-运维-折旧) × tax_rate)``，直线折旧（默认 = life_years）；
    - 储能更换在第 year 年注入 cost_yuan；残值 = ``salvage_ratio × capex`` 期末计入；
    - ``discount_rate`` 给定时计算 NPV/回收期，否则两者为 None。
    无税/无更换/无残值/无衰减时，IRR 与 ``evaluate_levelized_irr`` 一致（退化基准）。
    """
    N = int(life_years)
    deg = capacity_degradation if capacity_degradation is not None else [1.0] * N
    if len(deg) != N:
        raise ValueError("capacity_degradation length must equal life_years")
    dep_years = int(depreciation_years) if depreciation_years is not None else N
    dep_y = float(capex_yuan) / dep_years
    repl_by_year = {int(e.year): float(e.cost_yuan) for e in (replacements or [])}

    revs: list[float] = []
    opexes: list[float] = []
    taxes: list[float] = []
    repls: list[float] = []
    for y in range(1, N + 1):
        ratio = deg[y - 1]
        revenue = float(annual_revenue_y1_yuan) * ratio
        opex = float(annual_opex_y1_yuan) * ratio
        depreciation = dep_y if y <= dep_years else 0.0
        tax = max(0.0, (revenue - opex - depreciation) * float(tax_rate))
        revs.append(revenue)
        opexes.append(opex)
        taxes.append(tax)
        repls.append(repl_by_year.get(y, 0.0))

    salvage = float(salvage_ratio) * float(capex_yuan)
    annual_net = [revs[i] - opexes[i] - taxes[i] - repls[i] for i in range(N)]
    cashflows = [-float(capex_yuan)] + annual_net + [salvage]
    irr = compute_irr(cashflows)
    npv = compute_npv(cashflows, discount_rate) if discount_rate is not None else None
    payback = compute_payback_year(cashflows) if discount_rate is not None else None
    return ProjectCashflowResult(
        capex_yuan=float(capex_yuan),
        annual_revenues_yuan=revs,
        annual_opexes_yuan=opexes,
        annual_taxes_yuan=taxes,
        replacement_events_yuan=repls,
        salvage_yuan=salvage,
        cashflows=cashflows,
        irr=irr,
        npv_yuan=npv,
        payback_year=payback,
    )
