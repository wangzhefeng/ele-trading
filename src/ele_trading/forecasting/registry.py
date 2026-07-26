"""Target and version keyed forecast-model registry."""

from __future__ import annotations

from typing import Protocol

from .contracts import ForecastRequest, ForecastResult


FORECAST_TARGETS = frozenset(
    {"weather", "price", "load", "wind_power", "pv_power"}
)


class ForecastModel(Protocol):
    """A request-oriented model accepted by the registry."""

    def forecast(self, request: ForecastRequest) -> ForecastResult:
        """Generate a forecast for one validated request."""
        ...


class UnknownForecastTargetError(ValueError):
    """Raised when a target is outside the supported forecast targets."""


class ForecastModelNotFoundError(LookupError):
    """Raised when no registered model matches the requested identity."""


class ForecastModelRegistry:
    """Resolve models by ``(target, model_name, model_version)``."""

    def __init__(self) -> None:
        self._models: dict[tuple[str, str, str], ForecastModel] = {}
        self._defaults: dict[str, tuple[str, str, str]] = {}
        self._latest: dict[tuple[str, str], tuple[str, str, str]] = {}

    def register(
        self,
        target: str,
        model_name: str,
        model_version: str,
        model: ForecastModel,
        *,
        default: bool = False,
    ) -> None:
        self._require_target(target)
        if not model_name.strip() or not model_version.strip():
            raise ValueError("model name and version must not be empty")
        key = (target, model_name, model_version)
        if key in self._models:
            raise ValueError(
                f"forecast model is already registered: {key!r}"
            )
        self._models[key] = model
        self._latest[(target, model_name)] = key
        if default:
            self._defaults[target] = key

    def resolve(
        self,
        target: str,
        model_name: str = "default",
        model_version: str | None = None,
    ) -> ForecastModel:
        self._require_target(target)
        if model_name == "default":
            key = self._defaults.get(target)
        elif model_version is None:
            key = self._latest.get((target, model_name))
        else:
            key = (target, model_name, model_version)
        model = self._models.get(key) if key is not None else None
        if model is None:
            identity = (
                f"{model_name}/{model_version}"
                if model_version is not None
                else model_name
            )
            raise ForecastModelNotFoundError(
                f"no forecast model for target {target!r} "
                f"and identity {identity!r}"
            )
        return model

    @staticmethod
    def _require_target(target: str) -> None:
        if target not in FORECAST_TARGETS:
            raise UnknownForecastTargetError(
                f"unknown forecast target {target!r}"
            )
