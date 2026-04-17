from __future__ import annotations

import numpy as np
from scipy.spatial.distance import cdist

from .sampler import PriceScenario


def normalize_weights(scenarios: list[PriceScenario]) -> list[PriceScenario]:
    """把场景权重归一化到 1。"""
    total = sum(s.weight for s in scenarios)
    if total <= 0:
        raise ValueError('场景权重和必须大于 0')
    return [PriceScenario(name=s.name, prices=s.prices, weight=s.weight / total) for s in scenarios]


def reduce_scenarios(scenarios: list[PriceScenario], top_k: int) -> list[PriceScenario]:
    """Kantorovich/Wasserstein 后向缩减。

    迭代剔除「转移代价最小」的场景（Heitsch & Römisch 2003）：
    1. 计算所有场景间 L1 距离矩阵。
    2. 每轮找出使 Kantorovich 距离增量最小的场景并剔除。
    3. 将其权重转移给距离最近的保留场景。
    4. 重复直至剩余 top_k 个场景，最后归一化权重。
    """
    if top_k <= 0:
        raise ValueError('top_k 必须大于 0')
    if top_k >= len(scenarios):
        return normalize_weights(list(scenarios))

    prices_matrix = np.array([s.prices for s in scenarios], dtype=float)  # (N, T)
    weights = np.array([s.weight for s in scenarios], dtype=float)
    names = [s.name for s in scenarios]
    dist_matrix = cdist(prices_matrix, prices_matrix, metric='cityblock')  # (N, N) L1

    active = list(range(len(scenarios)))

    while len(active) > top_k:
        best_candidate = None
        best_cost = np.inf

        for i in active:
            others = [j for j in active if j != i]
            nearest_dist = min(dist_matrix[i, j] for j in others)
            cost = weights[i] * nearest_dist
            if cost < best_cost:
                best_cost = cost
                best_candidate = i

        others = [j for j in active if j != best_candidate]
        nearest = min(others, key=lambda j: dist_matrix[best_candidate, j])
        weights[nearest] += weights[best_candidate]
        active.remove(best_candidate)

    reduced = [
        PriceScenario(name=names[i], prices=prices_matrix[i].tolist(), weight=float(weights[i]))
        for i in active
    ]
    return normalize_weights(reduced)
