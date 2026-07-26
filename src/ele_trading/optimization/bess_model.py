"""Reusable PuLP constraints for battery energy storage systems."""

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
    """Physical and operating limits shared by PuLP dispatch models."""

    soc0: float
    soc_min: float
    soc_max: float
    p_ch_max: float
    p_dis_max: float
    eta_ch: float
    eta_dis: float
    dt: float = 0.25
    terminal_soc: float | None = None
    max_throughput: float | None = None
    no_export: bool = False

    def __post_init__(self) -> None:
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
    """PuLP decision variables created by :func:`add_bess_constraints`."""

    p_charge: dict[Hashable, LpVariable]
    p_discharge: dict[Hashable, LpVariable]
    soc: dict[Hashable, LpVariable]
    charge_mode: dict[Hashable, LpVariable]
    discharge_mode: dict[Hashable, LpVariable]


def add_bess_constraints(
    model: LpProblem,
    time_steps,
    config: BESSConfig,
    *,
    net_load: Mapping[Hashable, object] | None = None,
    prefix: str = "bess",
) -> BESSVariables:
    """Add SOC, efficiency, power, terminal, throughput and export limits."""
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

    for position, step in enumerate(steps):
        model += (
            charge_mode[step] + discharge_mode[step] <= 1.0,
            f"{prefix}_exclusive_{position}",
        )
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
        if config.no_export:
            assert net_load is not None
            model += (
                p_discharge[step] - p_charge[step]
                <= net_load[step],
                f"{prefix}_no_export_{position}",
            )

    if config.terminal_soc is not None:
        model += (
            soc[steps[-1]] == config.terminal_soc,
            f"{prefix}_terminal_soc",
        )
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


BESSParameters = BESSConfig

