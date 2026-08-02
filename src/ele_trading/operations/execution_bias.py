"""执行偏差学习：把实测偏差转化为下一滚动窗口的约束收紧（v5 §11.3）。

滚动记录计划 vs 实测的功率/SOC 偏差，输出保守收紧量：
- 持续欠发（actual < planned）产生功率降额；
- 持续 SOC 低估产生 SOC 储备；
- 超发不产生负收紧；
- 样本不足时明确标记 unavailable，收紧量为 0，不悄悄生效。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class ConstraintTightening:
    """作用于下一窗口 BESS 约束的收紧量。"""

    power_derate_mw: float
    soc_reserve_mwh: float
    sample_count: int
    available: bool


class ExecutionBiasEstimator:
    """固定窗口的计划-实测偏差估计器。"""

    def __init__(
        self,
        *,
        window: int,
        min_samples: int = 4,
        tightening_sigma: float = 2.0,
    ) -> None:
        if not isinstance(window, int) or window <= 0:
            raise ValueError("window must be a positive integer")
        if not isinstance(min_samples, int) or min_samples <= 0:
            raise ValueError("min_samples must be a positive integer")
        if not np.isfinite(tightening_sigma) or tightening_sigma < 0.0:
            raise ValueError("tightening_sigma must be finite and non-negative")
        self._power_bias: deque[float] = deque(maxlen=window)
        self._soc_bias: deque[float] = deque(maxlen=window)
        self.min_samples = min_samples
        self.tightening_sigma = float(tightening_sigma)

    @staticmethod
    def _finite(value: float, field_name: str) -> float:
        result = float(value)
        if not np.isfinite(result):
            raise ValueError(f"{field_name} must be finite")
        return result

    def record_power(self, *, planned_mw: float, actual_mw: float) -> None:
        planned = self._finite(planned_mw, "planned_mw")
        actual = self._finite(actual_mw, "actual_mw")
        self._power_bias.append(actual - planned)

    def record_soc(self, *, planned_mwh: float, actual_mwh: float) -> None:
        planned = self._finite(planned_mwh, "planned_mwh")
        actual = self._finite(actual_mwh, "actual_mwh")
        self._soc_bias.append(actual - planned)

    def constraint_tightening(self) -> ConstraintTightening:
        """由偏差分布推导收紧量；样本不足时不生效。"""
        sample_count = max(len(self._power_bias), len(self._soc_bias))
        available = sample_count >= self.min_samples
        if not available:
            return ConstraintTightening(
                power_derate_mw=0.0,
                soc_reserve_mwh=0.0,
                sample_count=sample_count,
                available=False,
            )

        power_derate = 0.0
        if self._power_bias:
            bias = np.asarray(self._power_bias, dtype=float)
            mean = float(bias.mean())
            std = float(bias.std()) if len(bias) > 1 else 0.0
            # 欠发（负偏差）+ sigma 缓冲 → 降额；超发不降额
            power_derate = max(
                0.0, -mean + self.tightening_sigma * std * float(mean < 0.0)
            )

        soc_reserve = 0.0
        if self._soc_bias:
            soc_mean = float(np.mean(self._soc_bias))
            soc_reserve = max(0.0, -soc_mean)

        return ConstraintTightening(
            power_derate_mw=power_derate,
            soc_reserve_mwh=soc_reserve,
            sample_count=sample_count,
            available=True,
        )
