"""Wind/PV/BESS IRR resource-parameter tuning helpers."""
from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ele_trading.utils.log_util import logger

from .wind_pv_bess_irr_planner import (
    WindPVBESSIRRPlanConfig,
    WindPVBESSIRRResult,
    plan_wind_pv_bess_for_target_irr,
)


BuildWindCurve = Callable[[dict[str, Any], Path], pd.Series]
BuildPVCurve = Callable[[dict[str, Any], pd.DatetimeIndex, Path], pd.Series]
CurveCachePath = Callable[[Path, str, dict[str, Any]], Path]
SummaryUpdateCallback = Callable[[pd.DataFrame], None]


@dataclass(slots=True)
class WindPVBESSIRRTuningResult:
    """Parameter tuning result for the runner."""

    result: WindPVBESSIRRResult | None
    parameter_search_summary: pd.DataFrame
    best_summary: dict[str, Any] | None
    raw_diagnostics: pd.DataFrame | None = None


@dataclass(slots=True)
class _StageScenarioPayload:
    stage: str
    stage_index: int
    stage_total: int
    metadata: dict[str, Any]
    df_load: pd.DataFrame
    wind_unit: pd.Series
    pv_unit: pd.Series
    cfg: WindPVBESSIRRPlanConfig
    retain_diagnostics: bool


@dataclass(slots=True)
class _StageScenarioResult:
    result: WindPVBESSIRRResult
    metadata: dict[str, Any]
    summary_row: dict[str, Any]
    elapsed_seconds: float


def _float_range(start: float, stop: float, step: float) -> list[float]:
    """Return an inclusive float range, supporting ascending and descending ranges."""
    if step <= 0:
        raise ValueError("resource tuning step must be positive")
    start = float(start)
    stop = float(stop)
    values: list[float] = []
    if start <= stop:
        current = start
        while current <= stop + step * 1e-9:
            values.append(round(current, 9))
            current += step
        if values[-1] < round(stop, 9):
            values.append(round(stop, 9))
    else:
        current = start
        while current >= stop - step * 1e-9:
            values.append(round(current, 9))
            current -= step
        if values[-1] > round(stop, 9):
            values.append(round(stop, 9))
    return values


def curve_equivalent_hours(series: pd.Series, capacity_kw: float) -> float:
    """Calculate equivalent full-load hours from a unit output curve."""
    if len(series) < 2 or capacity_kw <= 0:
        return 0.0
    dt_hours = (series.index[1] - series.index[0]).total_seconds() / 3600.0
    return float(series.sum() * dt_hours / capacity_kw)


def _resource_adjustment_score(
    wind_flh: float,
    pv_flh: float,
    base_wind_flh: float,
    base_pv_flh: float,
) -> float:
    wind_uplift = max(wind_flh / base_wind_flh - 1.0, 0.0) if base_wind_flh > 0 else 0.0
    pv_uplift = max(pv_flh / base_pv_flh - 1.0, 0.0) if base_pv_flh > 0 else 0.0
    return float(wind_uplift + pv_uplift)


def iter_resource_scenarios(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build resource-parameter scenarios from the resource_tuning config section."""
    tuning = config.get("resource_tuning", {})
    wind_base = float(config["wind_simulation"]["target_full_load_hours"])
    pv_cloud_base = float(config["pv_simulation"]["cloud_factor"])
    pv_loss_base = float(config["pv_simulation"]["system_loss"])

    wind_values = _float_range(
        tuning.get("wind_target_full_load_hours_min", wind_base),
        tuning.get("wind_target_full_load_hours_max", wind_base),
        tuning.get("wind_target_full_load_hours_step", 50.0),
    )
    cloud_values = _float_range(
        tuning.get("pv_cloud_factor_min", pv_cloud_base),
        tuning.get("pv_cloud_factor_max", pv_cloud_base),
        tuning.get("pv_cloud_factor_step", 0.03),
    )
    loss_values = _float_range(
        tuning.get("pv_system_loss_min", pv_loss_base),
        tuning.get("pv_system_loss_max", pv_loss_base),
        tuning.get("pv_system_loss_step", 0.03),
    )

    scenarios: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float]] = set()
    for wind_flh in wind_values:
        for cloud_factor in cloud_values:
            for system_loss in loss_values:
                key = (float(wind_flh), float(cloud_factor), float(system_loss))
                seen.add(key)
                scenario_config = {
                    **config,
                    "wind_simulation": {
                        **config["wind_simulation"],
                        "target_full_load_hours": float(wind_flh),
                    },
                    "pv_simulation": {
                        **config["pv_simulation"],
                        "cloud_factor": float(cloud_factor),
                        "system_loss": float(system_loss),
                    },
                }
                scenarios.append({
                    "wind_target_full_load_hours": float(wind_flh),
                    "pv_cloud_factor": float(cloud_factor),
                    "pv_system_loss": float(system_loss),
                    "config": scenario_config,
                })
    base_key = (float(wind_base), float(pv_cloud_base), float(pv_loss_base))
    if base_key not in seen:
        scenarios.append({
            "wind_target_full_load_hours": float(wind_base),
            "pv_cloud_factor": float(pv_cloud_base),
            "pv_system_loss": float(pv_loss_base),
            "config": config,
        })
    return scenarios


def _bounded_cfg(
    cfg: WindPVBESSIRRPlanConfig,
    *,
    wind_min: float,
    wind_max: float,
    wind_step: float,
    pv_min: float,
    pv_max: float,
    pv_step: float,
    bess_min: float,
    bess_max: float,
    bess_step: float,
) -> WindPVBESSIRRPlanConfig:
    return replace(
        cfg,
        wind_min_mw=max(cfg.wind_min_mw, wind_min),
        wind_max_mw=min(cfg.wind_max_mw, wind_max),
        wind_step_mw=wind_step,
        pv_min_mw=max(cfg.pv_min_mw, pv_min),
        pv_max_mw=min(cfg.pv_max_mw, pv_max),
        pv_step_mw=pv_step,
        bess_min_mwh=max(cfg.bess_min_mwh, bess_min),
        bess_max_mwh=min(cfg.bess_max_mwh, bess_max),
        bess_step_mwh=bess_step,
    )


def _result_summary_row(result: WindPVBESSIRRResult, metadata: dict[str, Any], stage: str) -> dict[str, Any]:
    best_reason = "ok" if result.status == "ok" else None
    annual_green_generation_kwh = result.annual_green_generation_kwh
    row = {
        **metadata,
        "stage": stage,
        "status": result.status,
        "has_feasible_solution": result.status == "ok",
        "best_reason": best_reason,
        "elapsed_seconds": None,
        "total_combinations": None,
        "reason_counts": None,
        "worker_pid": None,
        "wind_mw": result.wind_mw,
        "pv_mw": result.pv_mw,
        "bess_mwh": result.bess_mwh,
        "irr": result.irr,
        "irr_ti_pre": result.irr_ti_pre,
        "irr_ti_post": result.irr_ti_post,
        "irr_eq_pre": result.irr_eq_pre,
        "irr_eq_post": result.irr_eq_post,
        "irr_gap": None if result.irr is None else abs(result.irr - metadata["target_irr"]),
        "green_price": result.green_price,
        "ppa_price": result.ppa_price,
        "owner_avg_price": result.owner_avg_price,
        "total_capex_yuan": result.total_capex_yuan,
        "annual_cashflow_yuan": result.annual_cashflow_yuan,
        "annual_green_generation_kwh": annual_green_generation_kwh,
        "annual_green_used_kwh": result.annual_green_used_kwh,
        "annual_grid_buy_kwh": result.annual_grid_buy_kwh,
        "self_use_ratio": result.self_use_ratio,
        "load_cover_ratio": result.load_cover_ratio,
        "curtail_kwh": result.curtail_kwh,
    }
    if result.status != "ok" and result.diagnostic_summary:
        best = result.diagnostic_summary.get("max_irr_candidate") or {}
        best_reason = best.get("reason")
        row.update({
            "best_reason": best_reason,
            "wind_mw": best.get("wind_mw", 0.0),
            "pv_mw": best.get("pv_mw", 0.0),
            "bess_mwh": best.get("bess_mwh", 0.0),
            "irr": best.get("irr"),
            "irr_ti_pre": best.get("irr_ti_pre"),
            "irr_ti_post": best.get("irr_ti_post"),
            "irr_eq_pre": best.get("irr_eq_pre"),
            "irr_eq_post": best.get("irr_eq_post"),
            "irr_gap": best.get("irr_gap"),
            "green_price": best.get("green_price"),
            "ppa_price": best.get("ppa_price"),
            "owner_avg_price": best.get("owner_avg_price"),
            "total_capex_yuan": best.get("total_capex_yuan"),
            "annual_cashflow_yuan": best.get("annual_cashflow_yuan"),
            "annual_green_generation_kwh": best.get("annual_green_generation_kwh"),
            "annual_green_used_kwh": best.get("annual_green_used_kwh"),
            "annual_grid_buy_kwh": best.get("annual_grid_buy_kwh"),
            "self_use_ratio": best.get("self_use_ratio"),
            "load_cover_ratio": best.get("load_cover_ratio"),
            "curtail_kwh": best.get("curtail_kwh"),
        })
    if result.diagnostic_summary:
        row["total_combinations"] = result.diagnostic_summary.get("total_combinations")
        row["reason_counts"] = result.diagnostic_summary.get("reason_counts")
    return row


def _run_stage_scenario(payload: _StageScenarioPayload) -> _StageScenarioResult:
    start = time.perf_counter()
    result = plan_wind_pv_bess_for_target_irr(
        payload.df_load,
        payload.wind_unit,
        payload.pv_unit,
        cfg=payload.cfg,
        retain_diagnostics=payload.retain_diagnostics,
    )
    elapsed = time.perf_counter() - start
    row = _result_summary_row(result, payload.metadata, stage=payload.stage)
    row["stage_index"] = payload.stage_index
    row["stage_total"] = payload.stage_total
    row["elapsed_seconds"] = float(elapsed)
    row["worker_pid"] = os.getpid()
    return _StageScenarioResult(
        result=result,
        metadata=payload.metadata,
        summary_row=row,
        elapsed_seconds=float(elapsed),
    )


def _log_stage_result(stage_result: _StageScenarioResult, stage: str) -> None:
    row = stage_result.summary_row
    logger.info(
        "%s %s/%s scenario_id=%s status=%s irr=%s elapsed=%.2fs",
        stage,
        row.get("stage_index"),
        row.get("stage_total"),
        row.get("scenario_id"),
        row.get("status"),
        row.get("irr"),
        stage_result.elapsed_seconds,
    )


def _summary_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    stage_order = {"coarse": 0, "fine": 1}
    return pd.DataFrame(sorted(
        rows,
        key=lambda row: (
            stage_order.get(str(row.get("stage")), 99),
            int(row.get("stage_index") or 0),
            int(row.get("scenario_id") or 0),
        ),
    ))


def _run_stage_scenarios(
    payloads: list[_StageScenarioPayload],
    *,
    parallel_enabled: bool,
    max_workers: int,
    on_summary_update: SummaryUpdateCallback | None,
    completed_rows: list[dict[str, Any]],
) -> list[_StageScenarioResult]:
    if not payloads:
        return []

    results: list[_StageScenarioResult] = []
    if parallel_enabled and max_workers > 1:
        worker_count = min(max_workers, len(payloads))
        logger.info("并行运行 %s 阶段资源场景: workers=%d scenarios=%d", payloads[0].stage, worker_count, len(payloads))
        try:
            with ProcessPoolExecutor(max_workers=worker_count) as executor:
                future_map = {executor.submit(_run_stage_scenario, payload): payload for payload in payloads}
                for future in as_completed(future_map):
                    stage_result = future.result()
                    results.append(stage_result)
                    completed_rows.append(stage_result.summary_row)
                    _log_stage_result(stage_result, stage_result.summary_row["stage"])
                    if on_summary_update is not None:
                        on_summary_update(_summary_dataframe(completed_rows))
        except (NotImplementedError, PermissionError) as exc:
            logger.warning("并行执行不可用，降级为串行运行 %s 阶段: %s", payloads[0].stage, exc)
            for payload in payloads:
                stage_result = _run_stage_scenario(payload)
                results.append(stage_result)
                completed_rows.append(stage_result.summary_row)
                _log_stage_result(stage_result, stage_result.summary_row["stage"])
                if on_summary_update is not None:
                    on_summary_update(_summary_dataframe(completed_rows))
    else:
        logger.info("串行运行 %s 阶段资源场景: scenarios=%d", payloads[0].stage, len(payloads))
        for payload in payloads:
            stage_result = _run_stage_scenario(payload)
            results.append(stage_result)
            completed_rows.append(stage_result.summary_row)
            _log_stage_result(stage_result, stage_result.summary_row["stage"])
            if on_summary_update is not None:
                on_summary_update(_summary_dataframe(completed_rows))

    return sorted(
        results,
        key=lambda item: (item.summary_row.get("stage_index", 0), item.summary_row.get("scenario_id", 0)),
    )


def _add_metadata(df: pd.DataFrame | None, metadata: dict[str, Any]) -> pd.DataFrame | None:
    if df is None:
        return None
    enriched = df.copy()
    for key, value in metadata.items():
        if key not in enriched.columns:
            enriched[key] = value
    lead_cols = [key for key in metadata if key in enriched.columns]
    other_cols = [col for col in enriched.columns if col not in lead_cols]
    return enriched[lead_cols + other_cols]


def _sort_feasible(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(
        ["resource_adjustment_score", "total_capex_yuan", "irr_gap"],
        na_position="last",
    ).reset_index(drop=True)


def _add_resource_adjustment_columns(scenario_df: pd.DataFrame, config: dict[str, Any]) -> pd.DataFrame:
    df = scenario_df.copy()
    wind_base = float(config["wind_simulation"]["target_full_load_hours"])
    pv_cloud_base = float(config["pv_simulation"]["cloud_factor"])
    pv_loss_base = float(config["pv_simulation"]["system_loss"])
    base_rows = df[
        (df["wind_target_full_load_hours"].astype(float) == wind_base)
        & (df["pv_cloud_factor"].astype(float) == pv_cloud_base)
        & (df["pv_system_loss"].astype(float) == pv_loss_base)
        & (df["stage"] == "coarse")
    ]
    if base_rows.empty:
        raise RuntimeError("base resource scenario is missing from parameter search results")
    base_row = base_rows.iloc[0]
    base_wind_flh = float(base_row["wind_unit_flh"])
    base_pv_flh = float(base_row["pv_unit_flh"])
    df["base_wind_unit_flh"] = base_wind_flh
    df["base_pv_unit_flh"] = base_pv_flh
    df["resource_adjustment_score"] = df.apply(
        lambda row: _resource_adjustment_score(
            float(row["wind_unit_flh"]),
            float(row["pv_unit_flh"]),
            base_wind_flh,
            base_pv_flh,
        ),
        axis=1,
    )
    return df


def run_wind_pv_bess_irr_resource_tuning(
    config: dict[str, Any],
    df_load: pd.DataFrame,
    time_index: pd.DatetimeIndex,
    data_dir: Path,
    base_cfg: WindPVBESSIRRPlanConfig,
    *,
    build_wind_unit_curve: BuildWindCurve,
    build_pv_unit_curve: BuildPVCurve,
    curve_cache_path: CurveCachePath,
    on_summary_update: SummaryUpdateCallback | None = None,
) -> WindPVBESSIRRTuningResult:
    """Run resource-parameter tuning plus two-stage Wind/PV/BESS capacity search."""
    tuning = config.get("resource_tuning", {})
    scenarios = iter_resource_scenarios(config)
    parallel_enabled = bool(tuning.get("parallel_enabled", False))
    max_workers = max(1, int(tuning.get("max_workers", 1)))
    retain_intermediate_diagnostics = bool(tuning.get("retain_intermediate_diagnostics", True))

    coarse_bounds = tuning.get("coarse_search_bounds", {})
    fine_window = tuning.get("fine_search_window", {})
    fine_step = tuning.get("fine_search_step", {})
    coarse_cfg = _bounded_cfg(
        base_cfg,
        wind_min=coarse_bounds.get("wind_min_mw", 80.0),
        wind_max=coarse_bounds.get("wind_max_mw", 170.0),
        wind_step=coarse_bounds.get("wind_step_mw", config["search"]["wind_step_mw"]),
        pv_min=coarse_bounds.get("pv_min_mw", 40.0),
        pv_max=coarse_bounds.get("pv_max_mw", config["capacity"]["pv_max_mw"]),
        pv_step=coarse_bounds.get("pv_step_mw", config["search"]["pv_step_mw"]),
        bess_min=coarse_bounds.get("bess_min_mwh", 0.0),
        bess_max=coarse_bounds.get("bess_max_mwh", 20.0),
        bess_step=coarse_bounds.get("bess_step_mwh", config["search"]["bess_step_mwh"]),
    )

    completed_rows: list[dict[str, Any]] = []
    scenario_payloads_for_stage: list[_StageScenarioPayload] = []
    scenario_payloads: dict[int, tuple[dict[str, Any], pd.Series, pd.Series]] = {}
    for idx, scenario in enumerate(scenarios, start=1):
        scenario_config = scenario["config"]
        wind_curve_path = curve_cache_path(data_dir, "wind_unit_curve", scenario_config["wind_simulation"])
        pv_curve_path = curve_cache_path(data_dir, "pv_unit_curve", scenario_config["pv_simulation"])
        wind_unit = build_wind_unit_curve(
            scenario_config,
            wind_curve_path,
        )
        pv_unit = build_pv_unit_curve(
            scenario_config,
            time_index,
            pv_curve_path,
        )
        wind_flh = curve_equivalent_hours(wind_unit, 1000.0)
        pv_flh = curve_equivalent_hours(pv_unit, 1.0)
        metadata = {
            "scenario_id": idx,
            "target_irr": base_cfg.target_irr,
            "wind_target_full_load_hours": scenario["wind_target_full_load_hours"],
            "pv_cloud_factor": scenario["pv_cloud_factor"],
            "pv_system_loss": scenario["pv_system_loss"],
            "wind_unit_flh": wind_flh,
            "pv_unit_flh": pv_flh,
            "base_wind_unit_flh": None,
            "base_pv_unit_flh": None,
            "resource_adjustment_score": None,
            "wind_curve_cache_path": str(wind_curve_path),
            "pv_curve_cache_path": str(pv_curve_path),
        }
        scenario_payloads[idx] = (metadata, wind_unit, pv_unit)
        scenario_payloads_for_stage.append(_StageScenarioPayload(
            stage="coarse",
            stage_index=idx,
            stage_total=len(scenarios),
            metadata=metadata,
            df_load=df_load,
            wind_unit=wind_unit,
            pv_unit=pv_unit,
            cfg=coarse_cfg,
            retain_diagnostics=retain_intermediate_diagnostics,
        ))

    coarse_results = _run_stage_scenarios(
        scenario_payloads_for_stage,
        parallel_enabled=parallel_enabled,
        max_workers=max_workers,
        on_summary_update=on_summary_update,
        completed_rows=completed_rows,
    )
    scenario_df = _add_resource_adjustment_columns(
        pd.DataFrame([stage_result.summary_row for stage_result in coarse_results]),
        config,
    )
    for _, row in scenario_df.iterrows():
        metadata, _, _ = scenario_payloads[int(row["scenario_id"])]
        metadata["base_wind_unit_flh"] = float(row["base_wind_unit_flh"])
        metadata["base_pv_unit_flh"] = float(row["base_pv_unit_flh"])
        metadata["resource_adjustment_score"] = float(row["resource_adjustment_score"])
    completed_rows[:] = scenario_df.to_dict("records")
    if on_summary_update is not None:
        on_summary_update(pd.DataFrame(completed_rows))
    near_df = scenario_df[
        scenario_df["irr"].notna()
        & (scenario_df["irr"] >= base_cfg.target_irr - max(base_cfg.irr_tolerance * 3, 0.006))
    ].copy()
    if near_df.empty:
        near_df = scenario_df[scenario_df["irr"].notna()].sort_values("irr", ascending=False).head(5).copy()
    near_df = _sort_feasible(near_df).head(int(tuning.get("fine_scenario_limit", 12)))
    if near_df.empty:
        summary_df = scenario_df.copy()
        if on_summary_update is not None:
            on_summary_update(summary_df)
        return WindPVBESSIRRTuningResult(
            result=None,
            parameter_search_summary=summary_df,
            best_summary=None,
        )

    fine_payloads: list[_StageScenarioPayload] = []
    fine_cfg_by_scenario: dict[int, WindPVBESSIRRPlanConfig] = {}
    for fine_idx, (_, row) in enumerate(near_df.iterrows(), start=1):
        metadata, wind_unit, pv_unit = scenario_payloads[int(row["scenario_id"])]
        bess_window = fine_window.get("bess_mwh", 10.0)
        fine_cfg = _bounded_cfg(
            base_cfg,
            wind_min=float(row["wind_mw"]) - fine_window.get("wind_mw", 15.0),
            wind_max=float(row["wind_mw"]) + fine_window.get("wind_mw", 15.0),
            wind_step=fine_step.get("wind_mw", 1.0),
            pv_min=float(row["pv_mw"]) - fine_window.get("pv_mw", 15.0),
            pv_max=float(row["pv_mw"]) + fine_window.get("pv_mw", 15.0),
            pv_step=fine_step.get("pv_mw", 1.0),
            bess_min=max(coarse_cfg.bess_min_mwh, float(row["bess_mwh"]) - bess_window),
            bess_max=min(coarse_cfg.bess_max_mwh, float(row["bess_mwh"]) + bess_window),
            bess_step=fine_step.get("bess_mwh", 1.0),
        )
        scenario_id = int(row["scenario_id"])
        fine_cfg_by_scenario[scenario_id] = fine_cfg
        fine_payloads.append(_StageScenarioPayload(
            stage="fine",
            stage_index=fine_idx,
            stage_total=len(near_df),
            metadata=metadata,
            df_load=df_load,
            wind_unit=wind_unit,
            pv_unit=pv_unit,
            cfg=fine_cfg,
            retain_diagnostics=retain_intermediate_diagnostics,
        ))

    fine_results = _run_stage_scenarios(
        fine_payloads,
        parallel_enabled=parallel_enabled,
        max_workers=max_workers,
        on_summary_update=on_summary_update,
        completed_rows=completed_rows,
    )
    final_df = pd.DataFrame([stage_result.summary_row for stage_result in fine_results])
    summary_df = pd.concat([scenario_df, final_df], ignore_index=True)
    if on_summary_update is not None:
        on_summary_update(summary_df)
    ok_df = final_df[final_df["status"] == "ok"].copy()
    if ok_df.empty:
        return WindPVBESSIRRTuningResult(
            result=None,
            parameter_search_summary=summary_df,
            best_summary=None,
        )

    ok_df = _sort_feasible(ok_df)
    best_row = ok_df.iloc[0]
    best_result: WindPVBESSIRRResult | None = None
    best_metadata: dict[str, Any] | None = None
    raw_diagnostics: pd.DataFrame | None = None
    best_scenario_id = int(best_row["scenario_id"])
    best_metadata, best_wind_unit, best_pv_unit = scenario_payloads[best_scenario_id]
    best_cfg = fine_cfg_by_scenario[best_scenario_id]
    best_result = plan_wind_pv_bess_for_target_irr(
        df_load,
        best_wind_unit,
        best_pv_unit,
        cfg=best_cfg,
        retain_diagnostics=True,
    )
    best_metadata = best_metadata
    raw_diagnostics = best_result.diagnostics.copy() if best_result.diagnostics is not None else None
    if best_result is None or best_metadata is None:
        raise RuntimeError("failed to locate best resource tuning result")

    best_result.diagnostics = _add_metadata(best_result.diagnostics, best_metadata)
    if best_result.diagnostics is not None and not best_result.diagnostics.empty:
        best_result.diagnostics = _sort_feasible(best_result.diagnostics)
        best_result.best_solution = best_result.diagnostics.iloc[0].to_dict()
    return WindPVBESSIRRTuningResult(
        result=best_result,
        parameter_search_summary=summary_df,
        best_summary=best_row.to_dict(),
        raw_diagnostics=raw_diagnostics,
    )
