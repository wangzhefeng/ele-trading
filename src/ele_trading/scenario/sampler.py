from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(slots=True)
class PriceScenario:
    name: str
    prices: list[float]
    weight: float


def generate_price_scenarios(point_forecast: list[float], num_scenarios: int = 3, noise_scale: float = 0.08, random_seed: int = 7) -> list[PriceScenario]:
    """基于点预测生成价格场景。"""
    if num_scenarios <= 0:
        raise ValueError('num_scenarios 必须大于 0')

    rng = np.random.default_rng(random_seed)
    base = np.asarray(point_forecast, dtype=float)
    scenarios: list[PriceScenario] = []
    for idx in range(num_scenarios):
        noise = rng.normal(loc=0.0, scale=noise_scale, size=len(base))
        scenario_prices = np.maximum(base * (1.0 + noise), 0.0)
        scenarios.append(PriceScenario(name=f'scenario_{idx}', prices=scenario_prices.round(4).tolist(), weight=1.0 / num_scenarios))
    return scenarios
