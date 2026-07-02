"""容量规划投资测算的月度结算层。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models.canonical_dispatch import DispatchSimulationResult
from .tariff import Tariff


@dataclass(slots=True)
class MonthlySettlementResult:
    """单个结算月的结算结果。

    月度对象是财务评价的来源：年度收入、业主综合电价和节费率都必须从这些
    月度结果汇总得到，不能再由 planner 临时拼年度散字段。
    """

    month: str
    green_used_kwh: float
    grid_buy_kwh: float
    curtail_kwh: float
    energy_charge_yuan: float
    demand_charge_yuan: float
    ppa_revenue_yuan: float
    owner_avg_price_yuan_per_kwh: float
    baseline_price_yuan_per_kwh: float
    savings_yuan: float
    savings_ratio: float
    net_load_peak_kw: float
    price_advantage_yuan: float = 0.0
    arbitrage_revenue_yuan: float = 0.0


@dataclass(slots=True)
class SettlementResult:
    """完整结算结果：月度明细 + 年度汇总。"""

    monthly: list[MonthlySettlementResult]
    annual_summary: dict[str, float]


def settle_monthly(
    dispatch: DispatchSimulationResult,
    *,
    tariff: Tariff | None = None,
    green_price_yuan_per_kwh: float | None = None,
    ppa_price_yuan_per_kwh: float | None = None,
    grid_buy_price_yuan_per_kwh: float | None = None,
    baseline_price_yuan_per_kwh: float | None = None,
    demand_charge_yuan_per_kw: float = 0.0,
) -> SettlementResult:
    """从 canonical dispatch 结果生成月度结算。

    口径说明：
    - 绿电收入按 `ppa_price_yuan_per_kwh * green_used_kwh` 进入投资方收入；
    - 业主侧成本按绿电结算价、电网购电价和需量费合计；
    - 需量费使用 dispatch 输出的 `net_load_kw` 月内峰值，不从月度电量反推；
    - 传 ``tariff`` 时电网购电价按逐时 TOU 价计费
     （``Σ grid_buy_kwh[t]*price[t]``，按月分组），绿电价取
      ``tariff.green_price_yuan_per_kwh``，需量费率取 ``tariff.demand_charge``；
      未传 tariff 时走旧标量路径。``ppa_price`` 与 ``baseline_price`` 始终由标量提供。
    - 年度汇总只由月度结果相加得到，保证可审计。
    """
    if ppa_price_yuan_per_kwh is None:
        raise ValueError("ppa_price_yuan_per_kwh is required")
    ppa_price = float(ppa_price_yuan_per_kwh)

    tou_grid_by_month: dict[str, float] | None
    if tariff is not None:
        tariff.validate(len(dispatch.grid_buy_kwh))
        grid_prices = np.asarray(tariff.grid_buy_price_yuan_per_kwh, dtype=float)
        green_price = float(tariff.green_price_yuan_per_kwh)
        demand_rate = (
            tariff.demand_charge.rate_yuan_per_kw
            if tariff.demand_charge is not None
            else 0.0
        )
        if baseline_price_yuan_per_kwh is None:
            raise ValueError("baseline_price_yuan_per_kwh is required when tariff is given")
        baseline_price = float(baseline_price_yuan_per_kwh)
        period_keys = dispatch.timestamps.to_period("M").astype(str)
        tou_grid_by_month = {
            month: float(
                (
                    dispatch.grid_buy_kwh[period_keys == month]
                    * grid_prices[period_keys == month]
                ).sum()
            )
            for month in sorted(set(period_keys))
        }
        # 电价提升优势 / 套利收益：按月从逐时 discharge/grid_charge 与 TOU 价算（spec §4.3）。
        discharge_arr = dispatch.discharge_kwh
        grid_charge_arr = dispatch.grid_charge_kwh
        is_arbitrage = dispatch.metadata.get("dispatch_mode") == "arbitrage"
        price_adv_by_month: dict[str, float] = {}
        arb_rev_by_month: dict[str, float] = {}
        for month in sorted(set(period_keys)):
            mask = period_keys == month
            month_prices = grid_prices[mask]
            mean_p = float(month_prices.mean())
            price_adv_by_month[month] = float(
                (discharge_arr[mask] * (month_prices - mean_p)).sum()
            )
            if is_arbitrage:
                arb_rev_by_month[month] = float(
                    (discharge_arr[mask] * month_prices).sum()
                    - (grid_charge_arr[mask] * month_prices).sum()
                )
            else:
                arb_rev_by_month[month] = 0.0
    else:
        if (
            green_price_yuan_per_kwh is None
            or grid_buy_price_yuan_per_kwh is None
            or baseline_price_yuan_per_kwh is None
        ):
            raise ValueError("either tariff or all scalar prices must be provided")
        green_price = float(green_price_yuan_per_kwh)
        demand_rate = float(demand_charge_yuan_per_kw)
        baseline_price = float(baseline_price_yuan_per_kwh)
        tou_grid_by_month = None
        price_adv_by_month = None
        arb_rev_by_month = None

    monthly: list[MonthlySettlementResult] = []
    for month, summary in dispatch.monthly_summary.items():
        # 物理量来自 canonical dispatch 的月度汇总，保持仿真和结算同源。
        green_used = float(summary["green_used_kwh"])
        grid_buy = float(summary["grid_buy_kwh"])
        curtail = float(summary["curtail_kwh"])
        load = float(summary["load_kwh"])
        # peak_kw 是本月净负荷功率峰值，直接驱动需量电费。
        peak_kw = float(summary["net_load_peak_kw"])

        # 业主侧能源账单：绿电按 green price 结算；购网按 TOU 逐时价（或扁平标量）。
        grid_charge_cost = (
            tou_grid_by_month[month]
            if tou_grid_by_month is not None
            else grid_buy * float(grid_buy_price_yuan_per_kwh)
        )
        energy_charge = grid_charge_cost + green_used * green_price
        demand_charge = peak_kw * demand_rate
        total_owner_cost = energy_charge + demand_charge
        baseline_cost = load * baseline_price
        # 投资方 PPA 收入与业主绿电结算价分开：green price = PPA + 绿电附加价。
        ppa_revenue = green_used * ppa_price
        savings = baseline_cost - total_owner_cost
        price_adv = price_adv_by_month[month] if price_adv_by_month is not None else 0.0
        arb_rev = arb_rev_by_month[month] if arb_rev_by_month is not None else 0.0
        monthly.append(
            MonthlySettlementResult(
                month=month,
                green_used_kwh=green_used,
                grid_buy_kwh=grid_buy,
                curtail_kwh=curtail,
                energy_charge_yuan=float(energy_charge),
                demand_charge_yuan=float(demand_charge),
                ppa_revenue_yuan=float(ppa_revenue),
                owner_avg_price_yuan_per_kwh=float(total_owner_cost / load) if load > 1e-9 else 0.0,
                baseline_price_yuan_per_kwh=float(baseline_price),
                savings_yuan=float(savings),
                savings_ratio=float(savings / baseline_cost) if baseline_cost > 1e-9 else 0.0,
                net_load_peak_kw=peak_kw,
                price_advantage_yuan=float(price_adv),
                arbitrage_revenue_yuan=float(arb_rev),
            )
        )

    # 年度汇总仅由 monthly list 相加，避免年度口径和月度口径漂移。
    annual = {
        "green_used_kwh": float(sum(row.green_used_kwh for row in monthly)),
        "grid_buy_kwh": float(sum(row.grid_buy_kwh for row in monthly)),
        "curtail_kwh": float(sum(row.curtail_kwh for row in monthly)),
        "energy_charge_yuan": float(sum(row.energy_charge_yuan for row in monthly)),
        "demand_charge_yuan": float(sum(row.demand_charge_yuan for row in monthly)),
        "ppa_revenue_yuan": float(sum(row.ppa_revenue_yuan for row in monthly)),
        "savings_yuan": float(sum(row.savings_yuan for row in monthly)),
        "price_advantage_yuan": float(sum(row.price_advantage_yuan for row in monthly)),
        "arbitrage_revenue_yuan": float(sum(row.arbitrage_revenue_yuan for row in monthly)),
        "load_kwh": float(dispatch.annual_summary["load_kwh"]),
    }
    annual["owner_avg_price_yuan_per_kwh"] = (
        float((annual["energy_charge_yuan"] + annual["demand_charge_yuan"]) / annual["load_kwh"])
        if annual["load_kwh"] > 1e-9
        else 0.0
    )
    baseline_cost = annual["load_kwh"] * baseline_price_yuan_per_kwh
    annual["savings_ratio"] = float(annual["savings_yuan"] / baseline_cost) if baseline_cost > 1e-9 else 0.0
    return SettlementResult(monthly=monthly, annual_summary=annual)
