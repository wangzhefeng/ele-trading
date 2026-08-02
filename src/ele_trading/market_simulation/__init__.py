"""市场数字孪生领域的公开入口（v5 M7 / D-014）。

独立于交易/回测主链与具体市场插件；只允许依赖
domain/forecasting/scenario/optimization 下层能力。
"""

from .contingency import ContingencyOutcome, N1Report, run_n1_screening
from .grid.contracts import Branch, Bus, Generator, GridSnapshot
from .sced import SCEDResult, solve_sced, solve_sced_multiperiod
from .scuc import (
    SCUCResult,
    UpliftReport,
    compute_uplift,
    price_from_commitment,
    solve_scuc,
)

__all__ = [
    "Branch",
    "Bus",
    "ContingencyOutcome",
    "Generator",
    "GridSnapshot",
    "N1Report",
    "SCEDResult",
    "SCUCResult",
    "UpliftReport",
    "compute_uplift",
    "price_from_commitment",
    "run_n1_screening",
    "solve_sced",
    "solve_sced_multiperiod",
    "solve_scuc",
]
