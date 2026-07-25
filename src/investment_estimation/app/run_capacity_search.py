from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from investment_estimation.capacity_search import run_capacity_search
from investment_estimation.config_loader import CaseConfig, load_case_config
from investment_estimation.data_provider import (
    build_timeseries,
    generate_sample_csvs,
    read_load_csv,
    read_price_csv,
    read_resource_csv,
)
from investment_estimation.finance import annual_cashflow_table


def run_search_from_yaml(config_path: str | Path) -> dict[str, object]:
    """按 YAML 配置运行 v1 容量搜索，并写出候选、最优和不可行结果。"""

    case = load_case_config(config_path)
    if case.sample_data.enabled:
        generate_sample_csvs(
            output_dir=case.paths.load_csv.parent,
            year=case.sample_data.year,
            freq=case.sample_data.freq,
        )
    load = read_load_csv(case.paths.load_csv)
    price = read_price_csv(case.paths.price_csv)
    resource = read_resource_csv(case.paths.resource_csv)
    timeseries = build_timeseries(load, price, resource)
    result = run_capacity_search(timeseries, case)
    _write_search_outputs(result, case)
    result["case"] = case
    return result


def _write_search_outputs(result: dict[str, object], case: CaseConfig) -> None:
    """根据 YAML 路径写出 v1 搜索结果。"""

    if case.paths.candidate_output_csv is not None:
        case.paths.candidate_output_csv.parent.mkdir(parents=True, exist_ok=True)
        result["candidates"].to_csv(case.paths.candidate_output_csv, index=False)
    if case.paths.infeasible_reasons_csv is not None:
        case.paths.infeasible_reasons_csv.parent.mkdir(parents=True, exist_ok=True)
        result["infeasible"].to_csv(case.paths.infeasible_reasons_csv, index=False)
    if result["best"] is not None and case.paths.best_summary_csv is not None:
        case.paths.best_summary_csv.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([result["best"]]).to_csv(case.paths.best_summary_csv, index=False)
    if result["best_monthly"] is not None and result["best_project"] is not None and case.paths.annual_cashflows_csv is not None:
        case.paths.annual_cashflows_csv.parent.mkdir(parents=True, exist_ok=True)
        annual = annual_cashflow_table(result["best_monthly"], result["best_project"], discount_rate=case.search.min_project_irr)
        annual.to_csv(case.paths.annual_cashflows_csv, index=False)


def main() -> None:
    """命令行入口：从 YAML 配置运行 v1 容量搜索。"""

    parser = argparse.ArgumentParser(description="Run a v1 investment-estimation capacity search from YAML.")
    default_config = Path(__file__).resolve().parents[1] / "configs" / "v1_capacity_search_demo.yaml"
    parser.add_argument("--config", default=default_config, help="Path to a v1 search YAML config.")
    args = parser.parse_args()

    result = run_search_from_yaml(args.config)
    candidates = result["candidates"]
    feasible_count = int(candidates["is_feasible"].sum()) if not candidates.empty else 0
    print(f"candidate_count={len(candidates)}")
    print(f"feasible_count={feasible_count}")
    print(f"best={result['best']}")


if __name__ == "__main__":
    main()
