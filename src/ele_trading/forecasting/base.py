from dataclasses import dataclass
from typing import List


@dataclass(slots=True)
class ForecastOutput:
    """统一预测输出。"""

    horizon: int
    point_forecast: List[float]
    lower_quantile: List[float] | None = None
    upper_quantile: List[float] | None = None
