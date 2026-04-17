from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.stats.qmc import LatinHypercube
from scipy.stats import norm as scipy_norm


@dataclass(slots=True)
class PriceScenario:
    name: str
    prices: list[float]
    weight: float


def generate_price_scenarios(
    point_forecast: list[float],
    num_scenarios: int = 3,
    noise_scale: float = 0.08,
    random_seed: int = 7,
    method: str = 'lhs',
    corr_matrix: np.ndarray | None = None,
) -> list[PriceScenario]:
    """生成价格场景。

    method='lhs'  Latin Hypercube Sampling（默认，推荐）。
    method='mc'   简单蒙特卡洛（向后兼容）。
    corr_matrix   T×T 时序相关矩阵，None 时各时段独立。
    """
    if num_scenarios <= 0:
        raise ValueError('num_scenarios 必须大于 0')

    T = len(point_forecast)
    base = np.asarray(point_forecast, dtype=float)

    if method == 'lhs':
        sampler = LatinHypercube(d=T, seed=random_seed)
        uniform_samples = sampler.random(n=num_scenarios)  # (N, T) in [0,1]
        normal_samples = scipy_norm.ppf(np.clip(uniform_samples, 1e-6, 1 - 1e-6))
    else:
        rng = np.random.default_rng(random_seed)
        normal_samples = rng.standard_normal((num_scenarios, T))

    if corr_matrix is not None:
        L = np.linalg.cholesky(corr_matrix)
        normal_samples = normal_samples @ L.T

    price_matrix = base[np.newaxis, :] * (1.0 + noise_scale * normal_samples)
    price_matrix = np.maximum(price_matrix, 0.0)

    return [
        PriceScenario(
            name=f'scenario_{i}',
            prices=price_matrix[i].round(4).tolist(),
            weight=1.0 / num_scenarios,
        )
        for i in range(num_scenarios)
    ]
