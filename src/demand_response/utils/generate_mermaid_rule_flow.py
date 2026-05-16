import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = str(Path(__file__).resolve().parents[4])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import pandas as pd

from model.model_packages.Demand_Response_optim.main_RQ_RN import (
    ModelMainClass,
    build_main_debug_case,
)
from model.model_packages.Demand_Response_optim.testing.localtest_support import (
    DEFAULT_ROUTE_LIST,
    FULL_COVERAGE_NOTIFICATION_HOURS_LIST,
    FULL_COVERAGE_RESPONSE_TIME_LEN_LIST,
    build_model_cfgs,
    generate_all_day_coverage_cases,
    load_case_input,
    resolve_response_mode,
)
from model.model_packages.Demand_Response_optim.utils.mermaid_rule_flow_common import (
    build_case_markdown,
    collect_rule_diagnostics,
)
from utils.log_util import logger


class MermaidFlowModel(ModelMainClass):
    """
    复用生产主流程，并在运行过程中采集 Mermaid 所需的中间结果。
    """

    def __init__(self):
        super().__init__()
        self.normalized_input_data = None
        self.period_map = None
        self.df_history_future = None
        self.df_strategy_period = None
        self.df_response_period = None
        self.allowed = None
        self.result_pre_declare_stage = None
        self.result_declare_stage = None

    def prepare_input_data(self, input_data: Dict) -> Dict:
        """
        记录标准化后的输入，供 Mermaid 摘要复用。
        """
        normalized_input_data = super().prepare_input_data(input_data)
        self.normalized_input_data = normalized_input_data
        return normalized_input_data

    def should_respond(self, period_map: Dict) -> bool:
        """
        记录资格判断结果，便于生成 Mermaid 分支说明。
        """
        allowed = super().should_respond(period_map)
        self.allowed = allowed
        return allowed

    def visualize_results(
        self,
        input_data: Dict,
        period_map: Dict,
        df_history_future,
        df_strategy_period,
        result_pre_declare_stage: Dict,
        result_declare_stage: Dict,
    ):
        """
        仅采集阶段结果，不在离线 Mermaid 工具里重复做图片输出。
        """
        self.result_pre_declare_stage = result_pre_declare_stage
        self.result_declare_stage = result_declare_stage


def infer_response_mode(current_time, response_start) -> str:
    """
    根据通知提前量推导当前场景的业务模式。
    """
    notification_hours = (pd.to_datetime(response_start) - pd.to_datetime(current_time)).total_seconds() / 3600.0
    return resolve_response_mode(notification_hours)


def build_case_flow(
    *,
    project_dir: Path,
    route: str,
    response_date: str,
    current_time: str,
    response_start: str,
    response_end: str,
    response_mode: str,
) -> str:
    """
    跑通单个测试场景，并输出 Mermaid Markdown。
    """
    model_cfgs = build_model_cfgs()
    model = MermaidFlowModel()
    input_data = load_case_input(
        project_dir=project_dir,
        route=route,
        response_date=response_date,
        current_time=current_time,
        response_start=response_start,
        response_end=response_end,
        response_mode=response_mode,
        data_check=False,
        result_visual=False,
        result_save=False,
    )
    output = model.run(input_data, model_cfgs)
    diagnostics = collect_rule_diagnostics(model.df_strategy_period, model.period_map)
    allowed = bool(model.allowed)
    pre_stage = output["response_strategy_pre_declare"]
    declare_stage = output["response_strategy_declare"]

    case_info = {
        "route": route,
        "response_mode": model.normalized_input_data["response_mode"],
        "current_time": model.normalized_input_data["current_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "response_start": model.normalized_input_data["response_period"]["start"].strftime("%Y-%m-%d %H:%M:%S"),
        "response_end": model.normalized_input_data["response_period"]["end"].strftime("%Y-%m-%d %H:%M:%S"),
        "period_type": model.period_map["period_profile"]["period_type"],
    }
    return build_case_markdown(case_info, allowed, diagnostics, pre_stage, declare_stage)


def get_main_cases() -> List[Dict]:
    """
    获取 main_RQ_RN.py 当前内置的单例调试场景。
    """
    return [build_main_debug_case()]


def get_localtest_cases(*, route: str, response_date: str) -> List[Dict]:
    """
    获取 localtest 入口当前会生成的全量测试场景。
    """
    cases = generate_all_day_coverage_cases(
        response_date=response_date,
        response_time_len_list=FULL_COVERAGE_RESPONSE_TIME_LEN_LIST,
        notification_hours_list=FULL_COVERAGE_NOTIFICATION_HOURS_LIST,
    )
    return [
        {
            "route": route,
            "response_date": response_date,
            "current_time": case["current_time"].strftime("%Y-%m-%d %H:%M:%S"),
            "response_mode": case["response_mode"],
            "response_start": case["response_start"].strftime("%Y-%m-%d %H:%M:%S"),
            "response_end": case["response_end"].strftime("%Y-%m-%d %H:%M:%S"),
            "period_type": case["period_type"],
            "response_period_label": case["response_period_label"],
        }
        for case in cases
    ]


def get_cases_by_source(*, source: str, route: str, response_date: str) -> List[Dict]:
    """
    按来源返回可选测试场景列表。
    """
    if source == "main":
        return get_main_cases()
    if source == "localtest":
        return get_localtest_cases(route=route, response_date=response_date)
    raise ValueError(f"Unsupported source: {source}")


def normalize_case_from_args(args) -> Dict:
    """
    把 direct 模式的命令行参数整理成单个测试场景。
    """
    response_mode = args.response_mode or infer_response_mode(args.current_time, args.response_start)
    return {
        "route": args.route,
        "response_date": args.response_date,
        "current_time": args.current_time,
        "response_mode": response_mode,
        "response_start": args.response_start,
        "response_end": args.response_end,
    }


def render_case_markdown(project_dir: Path, case: Dict) -> str:
    """
    生成单个 case 的 Mermaid Markdown。
    """
    return build_case_flow(
        project_dir=project_dir,
        route=case["route"],
        response_date=case["response_date"],
        current_time=case["current_time"],
        response_start=case["response_start"],
        response_end=case["response_end"],
        response_mode=case["response_mode"],
    )


def write_markdown_output(markdown_text: str, output_path: Path) -> None:
    """
    把 Mermaid Markdown 写入指定文件。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown_text, encoding="utf-8")


def open_output_file(output_path: Path) -> None:
    """
    在本机打开生成的 Mermaid 文档。
    """
    subprocess.run(["open", str(output_path)], check=False)


def parse_batch_indexes(batch_indexes: str) -> List[int]:
    """
    解析批量模式下的索引列表。
    """
    return [int(item.strip()) for item in batch_indexes.split(",") if item.strip()]


def parse_args():
    """
    解析命令行参数。
    """
    parser = argparse.ArgumentParser(description="生成需求响应测试场景的 Mermaid 规则流程图")
    parser.add_argument("--source", choices=["direct", "main", "localtest"], default="direct")
    parser.add_argument("--route", default=DEFAULT_ROUTE_LIST[0])
    parser.add_argument("--response-date", default="2026-01-23")
    parser.add_argument("--current-time", default=None)
    parser.add_argument("--response-start", default=None)
    parser.add_argument("--response-end", default=None)
    parser.add_argument("--response-mode", default=None)
    parser.add_argument("--case-index", type=int, default=None)
    parser.add_argument("--batch-indexes", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--open", action="store_true")
    return parser.parse_args()


def main():
    """
    生成 Mermaid 规则流程图，并按需保存到文件。
    """
    args = parse_args()
    logger.setLevel("WARNING")
    project_dir = Path(__file__).resolve().parents[1]
    if args.source == "direct":
        if not all([args.current_time, args.response_start, args.response_end]):
            raise ValueError("direct 模式必须提供 --current-time、--response-start、--response-end")
        case = normalize_case_from_args(args)
        markdown_text = render_case_markdown(project_dir, case)
        if args.output is not None:
            output_path = Path(args.output)
            write_markdown_output(markdown_text, output_path)
            if args.open:
                open_output_file(output_path)
            print(f"Mermaid 已写入: {output_path}")
            return
        print(markdown_text)
        return

    cases = get_cases_by_source(source=args.source, route=args.route, response_date=args.response_date)
    if args.batch_indexes is not None:
        batch_indexes = parse_batch_indexes(args.batch_indexes)
        output_dir = Path(args.output_dir or project_dir.joinpath("result/mermaid_rule_flow"))
        output_dir.mkdir(parents=True, exist_ok=True)
        generated_files = []
        for case_index in batch_indexes:
            if case_index < 0 or case_index >= len(cases):
                raise IndexError(f"case index out of range: {case_index}")
            case = cases[case_index]
            markdown_text = render_case_markdown(project_dir, case)
            output_path = output_dir.joinpath(f"{args.source}_case_{case_index}.md")
            write_markdown_output(markdown_text, output_path)
            generated_files.append(output_path)
        if args.open and generated_files:
            open_output_file(generated_files[0])
        print("批量 Mermaid 已写入:")
        for output_path in generated_files:
            print(output_path)
        return

    case_index = args.case_index if args.case_index is not None else 0
    if case_index < 0 or case_index >= len(cases):
        raise IndexError(f"case index out of range: {case_index}")
    case = cases[case_index]
    markdown_text = render_case_markdown(project_dir, case)
    if args.output is not None:
        output_path = Path(args.output)
        write_markdown_output(markdown_text, output_path)
        if args.open:
            open_output_file(output_path)
        print(f"Mermaid 已写入: {output_path}")
        return
    print(markdown_text)


if __name__ == "__main__":
    main()
