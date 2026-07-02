from __future__ import annotations

import pandas as pd
from scipy.optimize import brentq

from invest_est_models.config_loader import ProjectConfig


def compute_capex(config: ProjectConfig) -> float:
    """计算初始总投资 CAPEX，当前只包含风、光、储设备投资。"""

    finance = config.finance
    bess = config.bess
    return (
        config.wind_capacity_kw * finance.capex_wind_per_kw
        + config.pv_capacity_kw * finance.capex_pv_per_kw
        + bess.power_kw * finance.capex_bess_power_per_kw
        + bess.energy_kwh * finance.capex_bess_energy_per_kwh
    )


def annual_cashflows(monthly: pd.DataFrame, config: ProjectConfig) -> list[float]:
    """根据首年月度结算结果构造项目生命周期年度税前现金流。"""

    capex = compute_capex(config)
    # 当前 MVP 用首年投资方收入作为基准年收入，再按年衰减。
    base_revenue = float(monthly["investor_revenue"].sum())
    fixed_om = capex * config.finance.fixed_om_pct_of_capex
    # 第 0 年为初始投资流出，后续年份为运营期现金流入。
    flows = [-capex]
    for year in range(1, config.finance.project_years + 1):
        # 用统一衰减率近似风光出力衰减导致的收入下降。
        degradation = (1.0 - config.finance.renewable_degradation_pct) ** (year - 1)
        replacement = 0.0
        # 储能更换成本只在配置指定年份发生一次。
        if config.finance.bess_replacement_year == year:
            replacement = (
                config.bess.power_kw * config.finance.capex_bess_power_per_kw
                + config.bess.energy_kwh * config.finance.capex_bess_energy_per_kwh
            ) * config.finance.bess_replacement_cost_pct
        flows.append(base_revenue * degradation - fixed_om - replacement)
    return flows


def annual_cashflow_table(monthly: pd.DataFrame, config: ProjectConfig, discount_rate: float = 0.08) -> pd.DataFrame:
    """输出年度现金流明细表，便于 v1 结果审阅和导出。"""

    flows = annual_cashflows(monthly, config)
    records = []
    cumulative = 0.0
    cumulative_discounted = 0.0
    for year, cashflow in enumerate(flows):
        discounted = cashflow / ((1.0 + discount_rate) ** year)
        cumulative += cashflow
        cumulative_discounted += discounted
        records.append(
            {
                "year": year,
                "cashflow": cashflow,
                "discount_rate": discount_rate,
                "discounted_cashflow": discounted,
                "cumulative_cashflow": cumulative,
                "cumulative_discounted_cashflow": cumulative_discounted,
            }
        )
    return pd.DataFrame(records)


def compute_npv(monthly: pd.DataFrame, config: ProjectConfig, discount_rate: float = 0.08) -> float:
    """按给定折现率计算 NPV。"""

    flows = annual_cashflows(monthly, config)
    return float(sum(cf / ((1.0 + discount_rate) ** i) for i, cf in enumerate(flows)))


def compute_payback_years(monthly: pd.DataFrame, config: ProjectConfig, discounted: bool = False, discount_rate: float = 0.08) -> float | None:
    """计算静态或动态回收期；若测算期内无法回收则返回 None。"""

    table = annual_cashflow_table(monthly, config, discount_rate=discount_rate)
    value_col = "discounted_cashflow" if discounted else "cashflow"
    cumulative = 0.0
    previous_cumulative = 0.0
    for _, row in table.iterrows():
        year = int(row["year"])
        cashflow = float(row[value_col])
        cumulative += cashflow
        if year == 0:
            previous_cumulative = cumulative
            continue
        if cumulative >= 0 and cashflow != 0:
            return (year - 1) + abs(previous_cumulative) / cashflow
        previous_cumulative = cumulative
    return None


def compute_project_irr(monthly: pd.DataFrame, config: ProjectConfig) -> float | None:
    """根据年度税前现金流求项目 IRR；无有效正负现金流时返回 None。"""

    flows = annual_cashflows(monthly, config)
    if not (any(v < 0 for v in flows) and any(v > 0 for v in flows)):
        return None

    def npv(rate: float) -> float:
        """给定折现率下的净现值，IRR 即 NPV 为 0 的折现率。"""

        return float(sum(cf / ((1.0 + rate) ** i) for i, cf in enumerate(flows)))

    try:
        # 搜索区间覆盖 -95% 到 100%，避免 Newton 法对初值敏感。
        return brentq(npv, -0.95, 1.0)
    except ValueError:
        return None


def backsolve_ppa_price(
    dispatch: pd.DataFrame,
    config: ProjectConfig,
    low: float = 0.0,
    high: float = 2.0,
) -> float | None:
    """反求达到目标税前项目 IRR 的固定 PPA 单价。"""

    from dataclasses import replace

    from invest_est_models.settlement import settle_monthly

    def gap(price: float) -> float:
        """目标函数：指定 PPA 单价下的 IRR 与目标 IRR 的差值。"""

        trial = replace(config, ppa_price=price)
        monthly = settle_monthly(dispatch, trial)
        irr = compute_project_irr(monthly, trial)
        if irr is None:
            return float("-inf")
        return irr - config.target_irr

    # 若最低价已经满足目标，直接返回下界；若最高价仍不满足，则判定不可行。
    if gap(low) >= 0:
        return low
    if gap(high) < 0:
        return None
    return brentq(gap, low, high)
