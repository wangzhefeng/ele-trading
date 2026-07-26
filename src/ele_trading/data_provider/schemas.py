from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

from .asset_data import BESSConfig


@dataclass(slots=True)
class PriceSeries:
    """价格序列数据结构。"""

    timestamps: List[int]
    prices: List[float]
    label: str = "sample"


@dataclass(slots=True)
class ScenarioRecord:
    """单条场景记录。"""

    scenario: str
    hour: int
    price: float
    weight: float


@dataclass(slots=True)
class ObservedPowerSeries:
    """Timezone-aware observed load or renewable power series."""

    values: pd.Series
    unit: str
    source: str
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.values, pd.Series):
            raise ValueError("values must be a pandas Series")
        index = self.values.index
        if (
            not isinstance(index, pd.DatetimeIndex)
            or index.tz is None
            or not index.is_monotonic_increasing
            or not index.is_unique
        ):
            raise ValueError(
                "observed power index must be timezone-aware, monotonic, and unique"
            )
        if (
            not pd.api.types.is_numeric_dtype(self.values.dtype)
            or not np.isfinite(self.values.to_numpy(dtype=float)).all()
        ):
            raise ValueError("observed power values must be finite numeric values")
        if not self.unit.strip():
            raise ValueError("unit must not be empty")
        if not self.source.strip():
            raise ValueError("source must not be empty")
        self.quality_flags = tuple(self.quality_flags)
