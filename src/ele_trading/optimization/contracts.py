"""活动优化内核的通用结果契约（dataclass 定义）。

这里只存放跨模型复用的结果类型，不含任何优化逻辑；
市场相关的市场参数（偏差考核系数等）由 trading/ 上层注入，不进入本层。
"""

from dataclasses import dataclass


@dataclass(slots=True)
class BESSArbitrageResult:
    """确定性单市场储能套利的求解结果。"""

    objective: float      # 目标函数值：电价套利收益 - 线性退化成本
    p_ch: list[float]     # 各时段充电功率
    p_dis: list[float]    # 各时段放电功率
    soc: list[float]      # 各时段末 SOC


@dataclass(slots=True)
class MPCStepResult:
    """MPC 滚动优化中单步执行的结果记录。"""

    step: int              # 当前滚动步序号
    price: float           # 当前步实际电价
    p_ch: float            # 本步执行的充电功率（窗口第 1 时段决策）
    p_dis: float           # 本步执行的放电功率（窗口第 1 时段决策）
    soc_next: float        # 本步执行后的 SOC（作为下一窗口初值）
    step_objective: float  # 本步求解的窗口目标函数值
