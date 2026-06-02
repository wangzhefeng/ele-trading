from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class SimulationResult:
    """新能源仿真统一输出。

    所有仿真模块（PV v1/v2, Wind v1/v2）共用此结果类。
    功率统一为 kW。
    """
    power_series: pd.Series                      # 出力时序（kW）
    total_generation_mwh: float                  # 年发电量（MWh）
    scale_factor: float                          # 校准系数 K
    selected_turbine: str | None = None          # 风电专属：机型名称
    turbine_count: int | None = None             # 风电专属：风机台数
    metadata: dict[str, float | str] | None = None  # 兼容层
