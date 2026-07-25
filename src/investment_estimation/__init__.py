"""风光储投资测算 MVP 包的顶层导出。"""

from .config_loader import BESSConfig, FinanceConfig, ProjectConfig
from .finance import backsolve_ppa_price, compute_project_irr

__all__ = [
    # 配置对象：供外部脚本直接构造场景参数。
    "BESSConfig",
    "FinanceConfig",
    "ProjectConfig",
    # 财务能力：供外部单独调用 IRR 和 PPA 反求。
    "backsolve_ppa_price",
    "compute_project_irr",
]
