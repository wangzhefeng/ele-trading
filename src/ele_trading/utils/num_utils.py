"""通用数值清洗工具。"""


def clean_value(raw: float | None, tol: float = 1e-9) -> float:
    """将求解器返回值清洗为干净的 float。

    - None → 抛出 RuntimeError
    - 绝对值小于 tol → 归零
    - 其余 → float()
    """
    if raw is None:
        raise RuntimeError("solver returned an empty variable value")
    if abs(raw) < tol:
        return 0.0
    return float(raw)


def clean_list(arr, tol: float = 1e-9) -> list[float]:
    """将 numpy 数组或可迭代对象转为干净的 float list。

    - None → 空列表
    - 每个元素取 float 后按 tol 清洗
    """
    if arr is None:
        return []
    import numpy as np

    return [clean_value(float(x), tol) for x in np.asarray(arr).flatten()]
