"""PuLP 求解器通用工具。"""

from pulp import LpStatus


def check_pulp_status(model, context: str = "dispatch") -> None:
    """检查 PuLP 求解状态，非 Optimal 则抛出 RuntimeError。"""
    status = LpStatus[model.status]
    if status != "Optimal":
        raise RuntimeError(f"{context} failed: {status}")
