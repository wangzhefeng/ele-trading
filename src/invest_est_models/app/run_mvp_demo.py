from __future__ import annotations

import argparse
from pathlib import Path

from invest_est_models.config_loader import ProjectConfig, load_case_config
from invest_est_models.data_provider import (
    build_timeseries,
    generate_sample_csvs,
    read_load_csv,
    read_price_csv,
    read_resource_csv,
)
from invest_est_models.dispatch import dispatch_rule_based
from invest_est_models.finance import backsolve_ppa_price, compute_project_irr
from invest_est_models.settlement import settle_monthly


def run_case_from_yaml(config_path: str | Path) -> dict[str, object]:
    """按 YAML 配置运行 MVP 测算场景，并写出配置指定的结果文件。"""

    case = load_case_config(config_path)
    if case.sample_data.enabled:
        generate_sample_csvs(
            output_dir=case.paths.load_csv.parent,
            year=case.sample_data.year,
            freq=case.sample_data.freq,
        )
    result = run_mvp_case(case.paths.load_csv, case.paths.price_csv, case.paths.resource_csv, case.project)
    case.paths.monthly_output_csv.parent.mkdir(parents=True, exist_ok=True)
    case.paths.dispatch_output_csv.parent.mkdir(parents=True, exist_ok=True)
    result["monthly"].to_csv(case.paths.monthly_output_csv, index=False)
    result["dispatch"].to_csv(case.paths.dispatch_output_csv, index=False)
    result["case"] = case
    return result


def run_mvp_case(
    load_csv: str | Path,
    price_csv: str | Path,
    resource_csv: str | Path,
    config: ProjectConfig,
) -> dict[str, object]:
    """运行 MVP 主链路：数据读取、调度、结算、财务和 PPA 反求。"""

    load = read_load_csv(load_csv)
    price = read_price_csv(price_csv)
    resource = read_resource_csv(resource_csv)
    timeseries = build_timeseries(load, price, resource)
    dispatch = dispatch_rule_based(timeseries, config.bess)
    monthly = settle_monthly(dispatch, config)
    irr = compute_project_irr(monthly, config)
    target_ppa_price = backsolve_ppa_price(dispatch, config)
    return {
        "timeseries": timeseries,
        "dispatch": dispatch,
        "monthly": monthly,
        "project_irr": irr,
        "target_ppa_price": target_ppa_price,
    }


def main() -> None:
    """命令行入口：从 YAML 配置运行一个 MVP 测算场景。"""

    parser = argparse.ArgumentParser(description="Run an MVP investment-estimation scenario from YAML.")
    # 默认场景配置放在 configs/ 下；用户可通过 --config 覆盖。
    default_config = Path(__file__).resolve().parents[1] / "configs" / "mvp_demo.yaml"
    parser.add_argument("--config", default=default_config, help="Path to a scenario YAML config.")
    args = parser.parse_args()

    # app 脚本直接承载 MVP 编排流程，并只打印关键摘要。
    result = run_case_from_yaml(args.config)
    print(f"project_irr={result['project_irr']}")
    print(f"target_ppa_price={result['target_ppa_price']}")


if __name__ == "__main__":
    main()
