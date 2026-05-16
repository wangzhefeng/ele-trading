import sys
PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from pathlib import Path

import pandas as pd


from model.model_packages.Demand_Response_optim.testing.localtest_support import (
    DEFAULT_ROUTE_LIST,
    all_day_time_bucket,
    build_model_cfgs,
    generate_all_day_coverage_cases,
    load_case_input,
)
from model.model_packages.Demand_Response_optim.main_RQ_RN import ModelMainClass


def main():
    # 模型配置
    model_cfgs = build_model_cfgs()
    # 模型输入
    project_dir = Path(__file__).resolve().parent
    route_list = DEFAULT_ROUTE_LIST
    response_date = "2026-01-23"
    response_time_len_list = [2.0]
    notification_hours_list = [0.5]

    result_response_load = pd.DataFrame()
    for route in route_list:
        cases = generate_all_day_coverage_cases(
            response_date=response_date,
            response_time_len_list=response_time_len_list,
            notification_hours_list=notification_hours_list,
        )
        df_profit_all = pd.DataFrame()
        result_declare_stage_response_capacity = pd.DataFrame()
        result_declare_stage_response_load = pd.DataFrame()
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
            # ------------------------------
            # 结果处理
            # ------------------------------
            declare_stage = output["response_strategy_declare"]
            if declare_stage is None:
                continue
            # 响应负荷结果
            if declare_stage["response_load"] is not None:
                load_df = pd.DataFrame({
                    "response_period": case["response_period_label"],
                    f"{route}-notice{case['notification_hours']}h": declare_stage["response_load"]["value"],
                })
                load_df = load_df.groupby("response_period").mean()
                result_declare_stage_response_load = pd.concat(
                    [result_declare_stage_response_load, load_df],
                    axis=0,
                )
            # 响应容量结果
            result_declare_stage_response_capacity = pd.concat(
                [
                    result_declare_stage_response_capacity,
                    pd.DataFrame(
                        {"value": declare_stage["response_capacity"]},
                        index=[case["response_period_label"]],
                    ),
                ],
                axis=0,
            )
            # 响应收益结果
            profit_df = declare_stage["response_profit"]
            if profit_df is not None:
                profit_df = profit_df.copy()
                profit_df["需求响应时段"] = case["response_period_label"]
                profit_df["时段类型"] = case["period_type"]
                profit_df = profit_df[["需求响应时段"] + [col for col in profit_df.columns if col != "需求响应时段"]]
                df_profit_all = pd.concat([df_profit_all, profit_df], axis=0, ignore_index=True)
        # ------------------------------
        # 结果保存
        # ------------------------------
        response_time_len = response_time_len_list[0]
        notification_hours = notification_hours_list[0]
        res_dir = project_dir.joinpath(f"result/{response_date}/日内-快速/{route}/response-{response_time_len}")
        res_dir.mkdir(parents=True, exist_ok=True)
        # 响应负荷结果保存
        result_declare_stage_response_load.to_csv(
            res_dir.joinpath(f"result_declare_stage_response_load_{response_time_len}h-{notification_hours}h.csv"),
            encoding="utf-8",
        )
        # 日内-快速滚动申报负荷
        result_declare_stage_response_load_reset = result_declare_stage_response_load.reset_index()
        result_declare_stage_response_load_reset["response_period_start"] = pd.to_datetime(
            result_declare_stage_response_load_reset["response_period"].apply(lambda x: x.split("~")[0])
        )
        result_declare_stage_response_load_reset["申报时段"] = result_declare_stage_response_load_reset[
            "response_period_start"
        ].apply(all_day_time_bucket)
        result_declare_stage_response_load_reset["响应时长"] = response_time_len
        result_declare_stage_response_load_reset = result_declare_stage_response_load_reset[
            [col for col in result_declare_stage_response_load_reset.columns if col not in ["index", "response_period_start"]]
        ]
        result_response_load = pd.concat([result_response_load, result_declare_stage_response_load_reset], axis=0)
        # 响应负荷结果保存
        result_declare_stage_response_capacity.to_csv(
            res_dir.joinpath(f"result_declare_stage_response_capacity_{response_time_len}h-{notification_hours}h.csv"),
            encoding="utf-8",
            index=True,
        )
        # 响应收益结果保存
        df_profit_all.to_csv(
            res_dir.joinpath(f"df_profit_all_{response_time_len}h-{notification_hours}h.csv"),
            encoding="utf-8",
            index=False,
        )
    # ------------------------------
    # 日内-快速滚动申报结果
    # ------------------------------
    final_result = result_response_load.groupby(["申报时段"])[
        [col for col in result_response_load.columns if col not in ["申报时段", "响应时长", "response_period"]]
    ].min()
    if [col for col in final_result.columns if col.endswith("notice0.5h")]:
        final_result["notice-0.5h"] = final_result[
            [col for col in final_result.columns if col.endswith("notice0.5h")]
        ].sum(axis=1) * 0.8
        final_result = final_result[["notice-0.5h"]]
    
    final_result.to_csv(project_dir.joinpath(f"result/{response_date}/final_result.csv"), encoding="utf-8")


if __name__ == "__main__":
    main()
