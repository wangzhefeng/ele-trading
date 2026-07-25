from .irr import (
    annual_cashflow_table,
    annual_cashflows,
    backsolve_ppa_price,
    compute_capex,
    compute_npv,
    compute_payback_years,
    compute_project_irr,
)
from .metrics import compute_irr

__all__ = [
    "annual_cashflow_table",
    # 年度现金流和 CAPEX。
    "annual_cashflows",
    "compute_capex",
    # IRR 和固定 PPA 价格反求。
    "backsolve_ppa_price",
    "compute_npv",
    "compute_payback_years",
    "compute_project_irr",
    # 通用二分法 IRR(现金流序列口径)。
    "compute_irr",
]
