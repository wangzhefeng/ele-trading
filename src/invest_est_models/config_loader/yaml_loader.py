from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    BaselineProjectConfig,
    BESSConfig,
    CapacitySearchConfig,
    CaseConfig,
    FinanceConfig,
    PathConfig,
    ProjectConfig,
    SampleDataConfig,
    SettlementConfig,
)


def load_case_config(path: str | Path) -> CaseConfig:
    """读取单个 YAML 测算场景配置，并转换为强类型配置对象。"""

    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    # 场景 YAML 默认放在 configs/ 下；此时相对路径统一按 invest_est_models 根目录解析。
    package_root = config_path.parents[1] if config_path.parent.name == "configs" else config_path.parent

    # YAML 顶层分组与 dataclass 一一对应，避免 app 脚本中硬编码场景参数。
    project_raw = dict(raw.get("project", {}))
    bess = BESSConfig(**_tuple_fields(raw.get("bess", {}), ("charge_price_types", "discharge_price_types")))
    finance = FinanceConfig(**dict(raw.get("finance", {})))
    settlement = SettlementConfig(**dict(raw.get("settlement", {})))
    project = ProjectConfig(**project_raw, bess=bess, finance=finance, settlement=settlement)
    paths = _load_paths(raw.get("paths", {}), package_root)
    sample_data = SampleDataConfig(**dict(raw.get("sample_data", {})))
    search = CapacitySearchConfig(**_tuple_fields(
        raw.get("search", {}),
        ("wind_capacity_kw", "pv_capacity_kw", "bess_power_kw", "bess_energy_kwh", "ppa_price"),
    ))
    baseline_project = _load_baseline_project(raw.get("baseline_project"))
    return CaseConfig(
        name=str(raw.get("scenario", {}).get("name", config_path.stem)),
        paths=paths,
        project=project,
        sample_data=sample_data,
        search=search,
        baseline_project=baseline_project,
    )


def _load_paths(raw: dict[str, Any], base_dir: Path) -> PathConfig:
    """校验路径字段完整性，并把相对路径转换为可直接读写的绝对口径 Path。"""

    required = ("load_csv", "price_csv", "resource_csv", "monthly_output_csv", "dispatch_output_csv")
    missing = sorted(set(required) - set(raw))
    if missing:
        raise ValueError(f"Missing path fields in YAML config: {missing}")
    optional = ("candidate_output_csv", "best_summary_csv", "infeasible_reasons_csv", "annual_cashflows_csv")
    values = {key: _resolve_path(raw[key], base_dir) for key in required}
    values.update({key: _resolve_path(raw[key], base_dir) for key in optional if raw.get(key)})
    return PathConfig(**values)


def _resolve_path(value: str, base_dir: Path) -> Path:
    """将 YAML 中的路径值解析为 Path；绝对路径保持不变。"""

    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _tuple_fields(raw: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """把 YAML list 转成 tuple，匹配冻结 dataclass 的不可变配置语义。"""

    data = dict(raw)
    for field in fields:
        if field in data:
            data[field] = tuple(data[field])
    return data


def _load_baseline_project(raw: dict[str, Any] | None) -> BaselineProjectConfig | None:
    """读取 V5 基准方案配置；未配置时返回 None，保持 V1-V4 口径不变。"""

    if raw is None:
        return None
    return BaselineProjectConfig(**dict(raw))
