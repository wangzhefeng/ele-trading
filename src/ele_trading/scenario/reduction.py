from __future__ import annotations

from .sampler import PriceScenario


def normalize_weights(scenarios: list[PriceScenario]) -> list[PriceScenario]:
    """把场景权重归一化到 1。"""
    total = sum(s.weight for s in scenarios)
    if total <= 0:
        raise ValueError('场景权重和必须大于 0')
    return [PriceScenario(name=s.name, prices=s.prices, weight=s.weight / total) for s in scenarios]


def reduce_scenarios(scenarios: list[PriceScenario], top_k: int) -> list[PriceScenario]:
    """保留权重最高的前 K 个场景，作为最小占位式场景削减。"""
    if top_k <= 0:
        raise ValueError('top_k 必须大于 0')
    ranked = sorted(scenarios, key=lambda item: item.weight, reverse=True)[:top_k]
    return normalize_weights(ranked)
