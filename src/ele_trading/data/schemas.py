from dataclasses import dataclass
from typing import List


@dataclass(slots=True)
class PriceSeries:
    """价格序列数据结构。"""

    timestamps: List[int]
    prices: List[float]
    label: str = "sample"


@dataclass(slots=True)
class StorageConfig:
    """储能物理约束与效率参数。"""

    asset_name: str
    soc0: float
    soc_min: float
    soc_max: float
    p_ch_max: float
    p_dis_max: float
    eta_ch: float
    eta_dis: float
    deg_cost: float
    dt: float


@dataclass(slots=True)
class ScenarioRecord:
    """单条场景记录。"""

    scenario: str
    hour: int
    price: float
    weight: float
