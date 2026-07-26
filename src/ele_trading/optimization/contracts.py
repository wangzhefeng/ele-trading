"""Active generic optimization result contracts."""

from dataclasses import dataclass


@dataclass(slots=True)
class BESSArbitrageResult:
    objective: float
    p_ch: list[float]
    p_dis: list[float]
    soc: list[float]


@dataclass(slots=True)
class MPCStepResult:
    step: int
    price: float
    p_ch: float
    p_dis: float
    soc_next: float
    step_objective: float
