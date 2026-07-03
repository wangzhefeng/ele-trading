from __future__ import annotations

from dataclasses import replace
from itertools import product

import pandas as pd

from invest_est_models.config_loader import BaselineProjectConfig, CaseConfig, ProjectConfig
from invest_est_models.dispatch import dispatch_rule_based
from invest_est_models.finance import compute_npv, compute_payback_years, compute_project_irr
from invest_est_models.settlement import settle_monthly


def run_capacity_search(base_timeseries: pd.DataFrame, case: CaseConfig) -> dict[str, object]:
    """执行 v1 粗网格容量搜索，返回候选方案、不可行原因和最优方案。"""

    baseline = _baseline_metrics(base_timeseries, case)
    candidates: list[dict[str, object]] = []
    best_payload: dict[str, object] | None = None
    for project in _candidate_projects(case.project, case):
        scaled = _scale_resource_by_capacity(base_timeseries, base=case.project, candidate=project)
        dispatch = dispatch_rule_based(scaled, project.bess)
        monthly = settle_monthly(dispatch, project)
        row = _evaluate_candidate(dispatch, monthly, project, case, baseline)
        candidates.append(row)
        if row["is_feasible"] and _is_better_candidate(row, best_payload):
            best_payload = {"row": row, "dispatch": dispatch, "monthly": monthly, "project": project}

    candidates_df = pd.DataFrame(candidates)
    infeasible_df = candidates_df.loc[~candidates_df["is_feasible"]].copy() if not candidates_df.empty else pd.DataFrame()
    return {
        "candidates": candidates_df,
        "infeasible": infeasible_df,
        "best": best_payload["row"] if best_payload else None,
        "best_dispatch": best_payload["dispatch"] if best_payload else None,
        "best_monthly": best_payload["monthly"] if best_payload else None,
        "best_project": best_payload["project"] if best_payload else None,
    }


def _candidate_projects(base: ProjectConfig, case: CaseConfig) -> list[ProjectConfig]:
    """把 YAML 候选数组展开成 ProjectConfig 列表。"""

    search = case.search
    projects = []
    for wind_kw, pv_kw, bess_power_kw, bess_energy_kwh, ppa_price in product(
        search.wind_capacity_kw or (base.wind_capacity_kw,),
        search.pv_capacity_kw or (base.pv_capacity_kw,),
        search.bess_power_kw or (base.bess.power_kw,),
        search.bess_energy_kwh or (base.bess.energy_kwh,),
        search.ppa_price or (base.ppa_price,),
    ):
        bess = replace(base.bess, power_kw=bess_power_kw, energy_kwh=bess_energy_kwh)
        projects.append(
            replace(
                base,
                wind_capacity_kw=wind_kw,
                pv_capacity_kw=pv_kw,
                ppa_price=ppa_price,
                bess=bess,
            )
        )
    return projects


def _scale_resource_by_capacity(df: pd.DataFrame, base: ProjectConfig, candidate: ProjectConfig) -> pd.DataFrame:
    """按候选容量缩放资源曲线；基准容量为 0 时保持原曲线不变。"""

    scaled = df.copy()
    if base.pv_capacity_kw > 0:
        scaled["pv_kw"] = scaled["pv_kw"] * candidate.pv_capacity_kw / base.pv_capacity_kw
    if base.wind_capacity_kw > 0:
        scaled["wind_kw"] = scaled["wind_kw"] * candidate.wind_capacity_kw / base.wind_capacity_kw
    return scaled


def _evaluate_candidate(
    dispatch: pd.DataFrame,
    monthly: pd.DataFrame,
    project: ProjectConfig,
    case: CaseConfig,
    baseline: dict[str, float | None],
) -> dict[str, object]:
    """计算单个候选方案的指标和约束满足状态。"""

    project_irr = compute_project_irr(monthly, project)
    baseline_cost = float(monthly["baseline_grid_cost"].sum())
    owner_saving = float(monthly["owner_saving"].sum())
    owner_saving_pct = owner_saving / baseline_cost if baseline_cost else 0.0
    renewable_generation = float(((dispatch["pv_kw"] + dispatch["wind_kw"]) * dispatch["dt_hours"]).sum())
    ppa_energy = float(monthly["ppa_energy_kwh"].sum())
    export_energy = float(monthly["grid_sell_kwh"].sum())
    self_use_ratio = ppa_energy / renewable_generation if renewable_generation else 0.0
    export_ratio = export_energy / renewable_generation if renewable_generation else 0.0
    reasons = _infeasible_reasons(project_irr, owner_saving_pct, self_use_ratio, export_ratio, case)
    objective = _objective_fields(case, project_irr, owner_saving_pct, baseline)
    return {
        "wind_capacity_kw": project.wind_capacity_kw,
        "pv_capacity_kw": project.pv_capacity_kw,
        "bess_power_kw": project.bess.power_kw,
        "bess_energy_kwh": project.bess.energy_kwh,
        "ppa_price": project.ppa_price,
        "project_irr": project_irr,
        "candidate_project_irr": project_irr,
        "npv_at_target_irr": compute_npv(monthly, project, discount_rate=case.search.min_project_irr),
        "payback_years": compute_payback_years(monthly, project),
        "owner_saving": owner_saving,
        "owner_saving_pct": owner_saving_pct,
        "candidate_owner_saving_pct": owner_saving_pct,
        "renewable_generation_kwh": renewable_generation,
        "ppa_energy_kwh": ppa_energy,
        "export_energy_kwh": export_energy,
        "self_use_ratio": self_use_ratio,
        "export_ratio": export_ratio,
        "is_feasible": not reasons,
        "infeasible_reasons": "; ".join(reasons),
        **objective,
    }


def _infeasible_reasons(
    project_irr: float | None,
    owner_saving_pct: float,
    self_use_ratio: float,
    export_ratio: float,
    case: CaseConfig,
) -> list[str]:
    """按 v1 搜索配置返回不可行原因列表。"""

    reasons = []
    search = case.search
    if project_irr is None:
        reasons.append("project_irr_unavailable")
    elif project_irr < search.min_project_irr:
        reasons.append("project_irr_below_min")
    if owner_saving_pct < search.min_owner_saving_pct:
        reasons.append("owner_saving_pct_below_min")
    if search.min_self_use_ratio is not None and self_use_ratio < search.min_self_use_ratio:
        reasons.append("self_use_ratio_below_min")
    if search.max_export_ratio is not None and export_ratio > search.max_export_ratio:
        reasons.append("export_ratio_above_max")
    return reasons


def _is_better_candidate(row: dict[str, object], best_payload: dict[str, object] | None) -> bool:
    """按 objective_mode 对可行候选方案排序。"""

    if best_payload is None:
        return True
    best = best_payload["row"]
    return _ranking_key(row) > _ranking_key(best)


def _ranking_key(row: dict[str, object]) -> tuple[float, float, float]:
    """把目标模式转成可比较排序键，值越大代表方案越优。"""

    mode = str(row["objective_mode"])
    if mode == "owner_saving_first":
        return (
            float(row["owner_saving_pct"]),
            _none_to_low(row["project_irr"]),
            _none_to_low(row["npv_at_target_irr"]),
        )
    if mode == "investor_irr_uplift":
        return (
            _none_to_low(row["irr_uplift"]),
            _none_to_low(row["candidate_project_irr"]),
            float(row["candidate_owner_saving_pct"]),
        )
    return (
        _none_to_low(row["project_irr"]),
        float(row["owner_saving_pct"]),
        _none_to_low(row["npv_at_target_irr"]),
    )


def _objective_fields(
    case: CaseConfig,
    project_irr: float | None,
    owner_saving_pct: float,
    baseline: dict[str, float | None],
) -> dict[str, object]:
    """根据目标模式补充输出字段，避免 CSV 结果脱离业务口径。"""

    mode = case.search.objective_mode
    if mode == "owner_saving_first":
        return {
            "objective_mode": mode,
            "objective_value": owner_saving_pct,
            "ranking_primary_metric": "owner_saving_pct",
            "ranking_secondary_metric": "project_irr",
            "constraint_min_project_irr": case.search.min_project_irr,
            "constraint_min_owner_saving_pct": case.search.min_owner_saving_pct,
            "baseline_project_irr": None,
            "baseline_owner_saving_pct": None,
            "irr_uplift": None,
        }
    if mode == "investor_irr_uplift":
        baseline_irr = baseline["baseline_project_irr"]
        irr_uplift = None if project_irr is None or baseline_irr is None else project_irr - baseline_irr
        return {
            "objective_mode": mode,
            "objective_value": irr_uplift,
            "ranking_primary_metric": "irr_uplift",
            "ranking_secondary_metric": "candidate_project_irr",
            "constraint_min_project_irr": case.search.min_project_irr,
            "constraint_min_owner_saving_pct": case.search.min_owner_saving_pct,
            "baseline_project_irr": baseline_irr,
            "baseline_owner_saving_pct": baseline["baseline_owner_saving_pct"],
            "irr_uplift": irr_uplift,
        }
    if mode != "investor_irr_first":
        raise ValueError(f"Unsupported objective_mode: {mode}")
    return {
        "objective_mode": mode,
        "objective_value": project_irr,
        "ranking_primary_metric": "project_irr",
        "ranking_secondary_metric": "owner_saving_pct",
        "constraint_min_project_irr": case.search.min_project_irr,
        "constraint_min_owner_saving_pct": case.search.min_owner_saving_pct,
        "baseline_project_irr": None,
        "baseline_owner_saving_pct": None,
        "irr_uplift": None,
    }


def _baseline_metrics(base_timeseries: pd.DataFrame, case: CaseConfig) -> dict[str, float | None]:
    """V5 模式先计算基准方案指标；其他模式返回空基准字段。"""

    if case.search.objective_mode != "investor_irr_uplift":
        return {"baseline_project_irr": None, "baseline_owner_saving_pct": None}
    if case.baseline_project is None:
        raise ValueError("baseline_project is required when objective_mode is investor_irr_uplift")
    baseline_project = _project_from_baseline(case.project, case.baseline_project)
    scaled = _scale_resource_by_capacity(base_timeseries, base=case.project, candidate=baseline_project)
    dispatch = dispatch_rule_based(scaled, baseline_project.bess)
    monthly = settle_monthly(dispatch, baseline_project)
    baseline_irr = compute_project_irr(monthly, baseline_project)
    if baseline_irr is None:
        raise ValueError("baseline_project_irr is unavailable")
    return {
        "baseline_project_irr": baseline_irr,
        "baseline_owner_saving_pct": _owner_saving_pct(monthly),
    }


def _project_from_baseline(base: ProjectConfig, baseline: BaselineProjectConfig) -> ProjectConfig:
    """用 baseline_project 覆盖项目容量和价格，未配置字段沿用主项目。"""

    bess = replace(
        base.bess,
        power_kw=baseline.bess_power_kw if baseline.bess_power_kw is not None else base.bess.power_kw,
        energy_kwh=baseline.bess_energy_kwh if baseline.bess_energy_kwh is not None else base.bess.energy_kwh,
    )
    return replace(
        base,
        wind_capacity_kw=baseline.wind_capacity_kw if baseline.wind_capacity_kw is not None else base.wind_capacity_kw,
        pv_capacity_kw=baseline.pv_capacity_kw if baseline.pv_capacity_kw is not None else base.pv_capacity_kw,
        ppa_price=baseline.ppa_price if baseline.ppa_price is not None else base.ppa_price,
        bess=bess,
    )


def _owner_saving_pct(monthly: pd.DataFrame) -> float:
    """按当前首年月度结算口径计算业主节费比例。"""

    baseline_cost = float(monthly["baseline_grid_cost"].sum())
    owner_saving = float(monthly["owner_saving"].sum())
    return owner_saving / baseline_cost if baseline_cost else 0.0


def _none_to_low(value: object) -> float:
    """排序辅助：不可用指标排在最后。"""

    return float(value) if value is not None else -1.0
