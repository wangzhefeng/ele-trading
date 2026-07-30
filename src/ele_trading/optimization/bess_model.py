"""电池储能系统（BESS）的可复用 PuLP 约束内核。

提供 BESSConfig 参数契约和 add_bess_constraints() 约束构建器，
统一封装 SOC 动态、充放电效率、功率上限、充放电互斥、末端 SOC、
吞吐量和禁止反送（no-export）约束，供套利、MPC、Two-stage 及
trading/ 上层模型复用，避免各处重复实现储能物理约束。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Mapping

import numpy as np
from pulp import (
    LpBinary,
    LpProblem,
    LpVariable,
    lpSum,
)


@dataclass(frozen=True, slots=True)
class BESSConfig:
    """PuLP 调度模型共享的储能物理与运行参数。"""

    soc0: float                          # 初始 SOC
    soc_min: float                       # SOC 下限
    soc_max: float                       # SOC 上限
    p_ch_max: float                      # 充电功率上限
    p_dis_max: float                     # 放电功率上限
    eta_ch: float                        # 充电效率，(0, 1]
    eta_dis: float                       # 放电效率，(0, 1]
    dt: float = 0.25                     # 时段时长（小时）；15 分钟颗粒度为 0.25
    terminal_soc: float | None = None    # 末端 SOC 强制值；None 表示不约束
    max_throughput: float | None = None  # 总吞吐量上限（充放电量之和）；None 表示不约束
    no_export: bool = False              # 是否禁止向电网反送（净放电不超过净负荷）

    def __post_init__(self) -> None:
        """参数合法性校验：取值有限、区间有序、效率与时长在物理合理范围。"""
        numeric_values = {
            "soc0": self.soc0,
            "soc_min": self.soc_min,
            "soc_max": self.soc_max,
            "p_ch_max": self.p_ch_max,
            "p_dis_max": self.p_dis_max,
            "eta_ch": self.eta_ch,
            "eta_dis": self.eta_dis,
            "dt": self.dt,
        }
        if not all(np.isfinite(value) for value in numeric_values.values()):
            raise ValueError("BESS configuration values must be finite")
        if self.soc_min > self.soc_max:
            raise ValueError("soc_min must not exceed soc_max")
        if not self.soc_min <= self.soc0 <= self.soc_max:
            raise ValueError("soc0 must be within SOC bounds")
        if self.p_ch_max < 0.0 or self.p_dis_max < 0.0:
            raise ValueError("BESS power limits must be non-negative")
        if not 0.0 < self.eta_ch <= 1.0:
            raise ValueError("eta_ch must be within (0, 1]")
        if not 0.0 < self.eta_dis <= 1.0:
            raise ValueError("eta_dis must be within (0, 1]")
        if self.dt <= 0.0:
            raise ValueError("dt must be positive")
        if self.terminal_soc is not None:
            if (
                not np.isfinite(self.terminal_soc)
                or not self.soc_min
                <= self.terminal_soc
                <= self.soc_max
            ):
                raise ValueError(
                    "terminal_soc must be finite and within SOC bounds"
                )
        if self.max_throughput is not None:
            if (
                not np.isfinite(self.max_throughput)
                or self.max_throughput < 0.0
            ):
                raise ValueError(
                    "max_throughput must be finite and non-negative"
                )


@dataclass(frozen=True, slots=True)
class BESSVariables:
    """add_bess_constraints() 创建的 PuLP 决策变量集合。"""

    p_charge: dict[Hashable, LpVariable]      # 各时段充电功率
    p_discharge: dict[Hashable, LpVariable]   # 各时段放电功率
    soc: dict[Hashable, LpVariable]           # 各时段末 SOC
    charge_mode: dict[Hashable, LpVariable]   # 充电状态 0/1（互斥用）
    discharge_mode: dict[Hashable, LpVariable]  # 放电状态 0/1（互斥用）


def add_bess_constraints(
    model: LpProblem,
    time_steps,
    config: BESSConfig,
    *,
    net_load: Mapping[Hashable, object] | None = None,
    prefix: str = "bess",
) -> BESSVariables:
    """向模型添加 SOC、效率、功率、末端、吞吐量与反送限制约束。

    参数：
        model: 目标 PuLP 模型。
        time_steps: 时段标识序列（可哈希、不重复、非空）。
        config: 储能物理与运行参数。
        net_load: 各时段净负荷；仅在 no_export=True 时必须提供，
            用于约束净放电不超过净负荷（禁止向电网反送）。
        prefix: 变量/约束名前缀，同一模型内多次调用时必须互不相同。
    """
    if not isinstance(model, LpProblem):
        raise ValueError("model must be a PuLP LpProblem")
    steps = tuple(time_steps)
    if not steps:
        raise ValueError("time_steps must not be empty")
    if len(set(steps)) != len(steps):
        raise ValueError("time_steps must be unique")
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("prefix must not be empty")
    if config.no_export:
        if net_load is None or any(step not in net_load for step in steps):
            raise ValueError(
                "net_load must provide every time step when no_export is set"
            )

    # ---------------- 决策变量 ----------------
    # 变量名使用序号而非原始时段标识，避免标识中特殊字符进入变量名
    p_charge = {
        step: LpVariable(
            f"{prefix}_p_charge_{position}",
            lowBound=0.0,
            upBound=config.p_ch_max,
        )
        for position, step in enumerate(steps)
    }
    p_discharge = {
        step: LpVariable(
            f"{prefix}_p_discharge_{position}",
            lowBound=0.0,
            upBound=config.p_dis_max,
        )
        for position, step in enumerate(steps)
    }
    soc = {
        step: LpVariable(
            f"{prefix}_soc_{position}",
            lowBound=config.soc_min,
            upBound=config.soc_max,
        )
        for position, step in enumerate(steps)
    }
    charge_mode = {
        step: LpVariable(
            f"{prefix}_charge_mode_{position}",
            cat=LpBinary,
        )
        for position, step in enumerate(steps)
    }
    discharge_mode = {
        step: LpVariable(
            f"{prefix}_discharge_mode_{position}",
            cat=LpBinary,
        )
        for position, step in enumerate(steps)
    }

    # ---------------- 逐时段约束 ----------------
    for position, step in enumerate(steps):
        # 充放电互斥：同一时段不允许既充又放
        model += (
            charge_mode[step] + discharge_mode[step] <= 1.0,
            f"{prefix}_exclusive_{position}",
        )
        # 功率与状态联动：未处于充电/放电状态时对应功率必须为 0
        model += (
            p_charge[step]
            <= config.p_ch_max * charge_mode[step],
            f"{prefix}_charge_limit_{position}",
        )
        model += (
            p_discharge[step]
            <= config.p_dis_max * discharge_mode[step],
            f"{prefix}_discharge_limit_{position}",
        )
        # SOC 动态：首时段以 soc0 为初值，其余时段递推
        # soc_t = soc_{t-1} + eta_ch * p_ch * dt - p_dis * dt / eta_dis
        previous_soc = (
            config.soc0
            if position == 0
            else soc[steps[position - 1]]
        )
        model += (
            soc[step]
            == previous_soc
            + config.eta_ch * p_charge[step] * config.dt
            - p_discharge[step] * config.dt / config.eta_dis,
            f"{prefix}_soc_balance_{position}",
        )
        # 禁止反送：净放电功率不超过净负荷
        if config.no_export:
            assert net_load is not None
            model += (
                p_discharge[step] - p_charge[step]
                <= net_load[step],
                f"{prefix}_no_export_{position}",
            )

    # ---------------- 全局约束 ----------------
    # 末端 SOC 强制值（如要求规划期末回到初始 SOC）
    if config.terminal_soc is not None:
        model += (
            soc[steps[-1]] == config.terminal_soc,
            f"{prefix}_terminal_soc",
        )
    # 总吞吐量上限：sum_t (p_ch + p_dis) * dt <= max_throughput
    if config.max_throughput is not None:
        model += (
            lpSum(
                (
                    p_charge[step] + p_discharge[step]
                )
                * config.dt
                for step in steps
            )
            <= config.max_throughput,
            f"{prefix}_throughput",
        )

    return BESSVariables(
        p_charge=p_charge,
        p_discharge=p_discharge,
        soc=soc,
        charge_mode=charge_mode,
        discharge_mode=discharge_mode,
    )


# 向后兼容别名：历史代码使用 BESSParameters 名称
BESSParameters = BESSConfig
