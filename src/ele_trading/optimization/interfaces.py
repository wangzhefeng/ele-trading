from dataclasses import dataclass
from typing import List


@dataclass(slots=True)
class StorageArbitrageResult:
    objective: float
    p_ch: List[float]
    p_dis: List[float]
    soc: List[float]


@dataclass(slots=True)
class MPCStepResult:
    step: int
    price: float
    p_ch: float
    p_dis: float
    soc_next: float
    step_objective: float
