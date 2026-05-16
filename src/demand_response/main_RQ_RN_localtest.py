import sys
from pathlib import Path
PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd


from model.model_packages.Demand_Response_optim.testing.localtest_support import (
    DEFAULT_ROUTE_LIST,
    FULL_COVERAGE_NOTIFICATION_HOURS_LIST,
    FULL_COVERAGE_RESPONSE_TIME_LEN_LIST,
    build_model_cfgs,
    load_case_input,
    generate_all_day_coverage_cases,
)
from model.model_packages.Demand_Response_optim.main_RQ_RN import ModelMainClass


def main():
    # 模型配置
    model_cfgs = build_model_cfgs()
    # 模型输入
    project_dir = Path(__file__).resolve().parent
    route_list = DEFAULT_ROUTE_LIST
    response_date = "2026-01-23"
    response_time_len_list = FULL_COVERAGE_RESPONSE_TIME_LEN_LIST
    notification_hours_list = FULL_COVERAGE_NOTIFICATION_HOURS_LIST

    for route in route_list:
        result_rows = []
        
        cases = generate_all_day_coverage_cases(
            response_date=response_date,
            response_time_len_list=response_time_len_list,
            notification_hours_list=notification_hours_list,
        )
        for case in cases:
            input_data = load_case_input(
                project_dir=project_dir,
                response_date=response_date,
                route=route,
                current_time=case["current_time"],
                response_start=case["response_start"],
                response_end=case["response_end"],
                response_mode=case["response_mode"],
                data_check=False,
                result_visual=False,
                result_save=False,
            )
            output = ModelMainClass().run(input_data, model_cfgs)
            
            declare_stage = output["response_strategy_declare"]
            result_rows.append({
                "response_period": case["response_period_label"],
                "period_type": case["period_type"],
                "response_mode": case["response_mode"],
                "notification_hours": case["notification_hours"],
                "response_time_len": case["response_time_len"],
                "response_capacity": None if declare_stage is None else declare_stage["response_capacity"],
            })

        result_df = pd.DataFrame(result_rows)
        result_dir = project_dir.joinpath(f"result/{response_date}/full_all_day_localtest/{route}")
        result_dir.mkdir(parents=True, exist_ok=True)
        result_df.to_csv(
            result_dir.joinpath("result_declare_stage_response_capacity.csv"),
            encoding="utf-8",
            index=False,
        )


if __name__ == "__main__":
    main()
