"""市场数字孪生使用的版本化网架与机组契约。"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import pandas as pd


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_finite(value: float, field_name: str) -> float:
    numeric = float(value)
    if not isfinite(numeric):
        raise ValueError(f"{field_name} must be finite")
    return numeric


@dataclass(frozen=True, slots=True)
class Bus:
    """DC 网络节点。"""

    bus_id: str
    name: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.bus_id, "bus_id")
        if self.name is not None:
            _require_non_empty(self.name, "name")


@dataclass(frozen=True, slots=True)
class Branch:
    """DC 网络支路。susceptance 使用统一标幺/角度口径。"""

    branch_id: str
    from_bus: str
    to_bus: str
    susceptance: float
    thermal_limit_mw: float
    in_service: bool = True

    def __post_init__(self) -> None:
        for field_name in ("branch_id", "from_bus", "to_bus"):
            _require_non_empty(getattr(self, field_name), field_name)
        if self.from_bus == self.to_bus:
            raise ValueError("branch endpoints must be different")
        susceptance = _require_finite(self.susceptance, "susceptance")
        thermal_limit = _require_finite(
            self.thermal_limit_mw,
            "thermal_limit_mw",
        )
        if susceptance == 0.0:
            raise ValueError("susceptance must be non-zero")
        if thermal_limit <= 0.0:
            raise ValueError("thermal_limit_mw must be positive")
        if not isinstance(self.in_service, bool):
            raise ValueError("in_service must be a boolean")
        object.__setattr__(self, "susceptance", susceptance)
        object.__setattr__(self, "thermal_limit_mw", thermal_limit)


@dataclass(frozen=True, slots=True)
class Generator:
    """SCED/SCUC 共享的机组物理与成本参数。"""

    generator_id: str
    bus_id: str
    p_min_mw: float
    p_max_mw: float
    ramp_up_mw: float
    ramp_down_mw: float
    marginal_cost: float
    startup_cost: float = 0.0
    shutdown_cost: float = 0.0
    no_load_cost: float = 0.0
    minimum_up_periods: int = 0
    minimum_down_periods: int = 0
    initial_on: bool = False

    def __post_init__(self) -> None:
        _require_non_empty(self.generator_id, "generator_id")
        _require_non_empty(self.bus_id, "bus_id")
        numeric_fields = (
            "p_min_mw",
            "p_max_mw",
            "ramp_up_mw",
            "ramp_down_mw",
            "marginal_cost",
            "startup_cost",
            "shutdown_cost",
            "no_load_cost",
        )
        values = {
            name: _require_finite(getattr(self, name), name)
            for name in numeric_fields
        }
        if values["p_min_mw"] < 0.0 or values["p_max_mw"] < 0.0:
            raise ValueError("generator capacities must be non-negative")
        if values["p_min_mw"] > values["p_max_mw"]:
            raise ValueError("p_min_mw cannot exceed p_max_mw")
        if values["ramp_up_mw"] < 0.0 or values["ramp_down_mw"] < 0.0:
            raise ValueError("ramp limits must be non-negative")
        if any(
            values[name] < 0.0
            for name in ("startup_cost", "shutdown_cost", "no_load_cost")
        ):
            raise ValueError("fixed generator costs must be non-negative")
        for name in ("minimum_up_periods", "minimum_down_periods"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not isinstance(self.initial_on, bool):
            raise ValueError("initial_on must be a boolean")
        for name, value in values.items():
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class GridSnapshot:
    """某一 as-of 时刻可见的版本化 DC 网架和机组快照。"""

    as_of: pd.Timestamp
    version: str
    buses: tuple[Bus, ...]
    branches: tuple[Branch, ...]
    generators: tuple[Generator, ...]
    reserve_requirement_mw: float = 0.0
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        as_of = pd.Timestamp(self.as_of)
        if pd.isna(as_of) or as_of.tzinfo is None:
            raise ValueError("as_of must be a timezone-aware timestamp")
        _require_non_empty(self.version, "version")
        buses = tuple(self.buses)
        branches = tuple(self.branches)
        generators = tuple(self.generators)
        if not buses:
            raise ValueError("buses must not be empty")
        if not all(isinstance(item, Bus) for item in buses):
            raise ValueError("buses must contain Bus objects")
        if not all(isinstance(item, Branch) for item in branches):
            raise ValueError("branches must contain Branch objects")
        if not all(isinstance(item, Generator) for item in generators):
            raise ValueError("generators must contain Generator objects")
        self._require_unique((item.bus_id for item in buses), "bus")
        self._require_unique((item.branch_id for item in branches), "branch")
        self._require_unique(
            (item.generator_id for item in generators),
            "generator",
        )
        bus_ids = {item.bus_id for item in buses}
        for branch in branches:
            if branch.from_bus not in bus_ids or branch.to_bus not in bus_ids:
                raise ValueError(
                    f"branch {branch.branch_id!r} references an unknown bus"
                )
        for generator in generators:
            if generator.bus_id not in bus_ids:
                raise ValueError(
                    f"generator {generator.generator_id!r} references an unknown bus"
                )
        reserve = _require_finite(
            self.reserve_requirement_mw,
            "reserve_requirement_mw",
        )
        if reserve < 0.0:
            raise ValueError("reserve_requirement_mw must be non-negative")
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "buses", buses)
        object.__setattr__(self, "branches", branches)
        object.__setattr__(self, "generators", generators)
        object.__setattr__(self, "reserve_requirement_mw", reserve)
        object.__setattr__(self, "quality_flags", tuple(self.quality_flags))

    @staticmethod
    def _require_unique(values, asset_type: str) -> None:
        ids = tuple(values)
        if len(ids) != len(set(ids)):
            raise ValueError(f"{asset_type} IDs must be unique")

    @property
    def bus_ids(self) -> frozenset[str]:
        return frozenset(item.bus_id for item in self.buses)

    @property
    def branch_ids(self) -> frozenset[str]:
        return frozenset(item.branch_id for item in self.branches)

    @property
    def generator_ids(self) -> frozenset[str]:
        return frozenset(item.generator_id for item in self.generators)
