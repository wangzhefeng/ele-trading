from __future__ import annotations

from dataclasses import replace
from itertools import product

import pandas as pd

from invest_est_models.config_loader import CaseConfig, ProjectConfig
from invest_est_models.dispatch import dispatch_rule_based
from invest_est_models.finance import compute_npv, compute_payback_years, compute_project_irr
from invest_est_models.settlement import settle_monthly


def run_capacity_search(base_timeseries: pd.DataFrame, case: CaseConfig) -> dict[str, object]:
    """执行 v1 粗网格容量搜索，返回候选方案、不可行原因和最优方案。"""

    candidates: list[dict[str, object]] = []
    best_payload: dict[str, object] | None = None
    for project in _candidate_projects(case.project, case):
        scaled = _scale_resource_by_capacity(base_timeseries, base=case.project, candidate=project)
        dispatch = dispatch_rule_based(scaled, project.bess)
        monthly = settle_monthly(dispatch, project)
        row = _evaluate_candidate(dispatch, monthly, project, case)
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
    return {
        "wind_capacity_kw": project.wind_capacity_kw,
        "pv_capacity_kw": project.pv_capacity_kw,
        "bess_power_kw": project.bess.power_kw,
        "bess_energy_kwh": project.bess.energy_kwh,
        "ppa_price": project.ppa_price,
        "project_irr": project_irr,
        "npv_at_target_irr": compute_npv(monthly, project, discount_rate=case.search.min_project_irr),
        "payback_years": compute_payback_years(monthly, project),
        "owner_saving": owner_saving,
        "owner_saving_pct": owner_saving_pct,
        "renewable_generation_kwh": renewable_generation,
        "ppa_energy_kwh": ppa_energy,
        "export_energy_kwh": export_energy,
        "self_use_ratio": self_use_ratio,
        "export_ratio": export_ratio,
        "is_feasible": not reasons,
        "infeasible_reasons": "; ".join(reasons),
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
    """最优方案排序规则：先 IRR，再业主节费比例。"""

    if best_payload is None:
        return True
    best = best_payload["row"]
    return (
        float(row["project_irr"] or -1.0),
        float(row["owner_saving_pct"]),
    ) > (
        float(best["project_irr"] or -1.0),
        float(best["owner_saving_pct"]),
    )
