from pathlib import Path
from typing import Dict

import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.clock import build_day_time_points
from model.model_packages.Demand_Response_optim.strategy.dispatch import (
    prepare_strategy_rule_context,
)
from model.model_packages.Demand_Response_optim.strategy.match import (
    build_rule_match_context,
    get_day_rule_matches,
    get_night_rule_matches,
)


def summarize_dispatch_path(period_map: Dict, response_mode: str) -> str:
    """
    生成当前场景的后置调度说明，便于写入 Mermaid 节点。
    """
    period_type = period_map["period_profile"]["period_type"]
    current_time = period_map["current_time"]
    if period_type == "day":
        response_date = period_map["response_date"]
        time_points = build_day_time_points(response_date)
        if response_mode == "日前":
            return "白天日前: 完整后置调度"
        if response_mode == "日内":
            if current_time < time_points["10:00"]:
                return "白天日内: 10:00前仅保留基础规则结果"
            return "白天日内: 10:00后执行完整后置调度"
        if current_time < time_points["19:00"]:
            return "白天快速: 19:00前仅保留基础规则结果"
        return "白天快速: 19:00后执行完整后置调度"

    if response_mode == "日内-快速":
        return "跨夜快速: rule5跳过收益比较, 采用快速保守策略"
    return "跨夜模式: 基础规则后按常规路径执行, rule5可做收益比较"


def collect_rule_diagnostics(df_strategy_period, period_map: Dict) -> Dict:
    """
    复用现有规则上下文，生成当前测试场景的规则命中诊断。
    """
    period_profile = period_map["period_profile"]
    rule_context = prepare_strategy_rule_context(df_strategy_period.copy(), period_map)
    runtime_period_map = rule_context["period_map"]
    if period_profile["period_type"] == "day":
        response_date = runtime_period_map["response_date"]
    else:
        response_date = runtime_period_map["response_end_date"]
    match_context = build_rule_match_context(
        response_date=response_date,
        response_start=runtime_period_map["response"]["start"],
        delta_discharge_power_1=rule_context["delta_discharge_power_1"],
        delta_discharge_power_2=rule_context["delta_discharge_power_2"],
        peak1_discharge_power=rule_context["peak1_discharge_power"],
        peak2_discharge_power=rule_context["peak2_discharge_power"],
    )
    if period_profile["period_type"] == "day":
        rule_matches = get_day_rule_matches(match_context)
    else:
        rule_matches = get_night_rule_matches(match_context)
    matched_rules = [rule_name for rule_name, matched in rule_matches.items() if matched]
    return {
        "rule_context": rule_context,
        "rule_matches": rule_matches,
        "matched_rules": matched_rules,
        "dispatch_path": summarize_dispatch_path(runtime_period_map, period_profile["response_mode"]),
    }


def format_rule_matches(rule_matches: Dict) -> str:
    """
    把规则命中结果压成 Mermaid 节点可展示的一行文本。
    """
    return "<br/>".join(
        f"{rule_name}={'T' if matched else 'F'}" for rule_name, matched in rule_matches.items()
    )


def format_stage_summary(stage_name: str, stage_result: Dict) -> str:
    """
    生成阶段输出摘要，聚焦容量和关键结果是否存在。
    """
    if stage_result is None:
        return f"{stage_name}: 无结果"
    response_load = stage_result.get("response_load")
    response_strategy = stage_result.get("response_strategy")
    response_capacity = stage_result.get("response_capacity")
    response_profit = stage_result.get("response_profit")
    return "<br/>".join(
        [
            f"{stage_name}",
            f"response_load={'Y' if response_load is not None else 'N'}",
            f"response_strategy={'Y' if response_strategy is not None else 'N'}",
            f"response_profit={'Y' if response_profit is not None else 'N'}",
            f"response_capacity={response_capacity}",
        ]
    )


def build_mermaid_text(case_info: Dict, allowed: bool, diagnostics: Dict, pre_stage: Dict, declare_stage: Dict) -> str:
    """
    生成当前测试场景的 Mermaid 流程图。
    """
    matched_rules = "、".join(diagnostics["matched_rules"]) if diagnostics["matched_rules"] else "无基础规则命中"
    mermaid_lines = [
        "flowchart TD",
        f'    A["测试输入<br/>route={case_info["route"]}<br/>response_mode={case_info["response_mode"]}<br/>current_time={case_info["current_time"]}<br/>response={case_info["response_start"]}~{case_info["response_end"]}"]',
        f'    B["prepare_input_data<br/>时间规范化 + 模式校验"]',
        f'    C["preprocessing_period<br/>period_type={case_info["period_type"]}<br/>response_mode={case_info["response_mode"]}"]',
        '    D["preprocessing_input_data<br/>装配 history/future/strategy/response"]',
        f'    E["should_respond<br/>allowed={allowed}"]',
        "    A --> B --> C --> D --> E",
    ]

    if not allowed:
        mermaid_lines.append('    E --> Z["不参与需求响应<br/>主流程直接返回空结果"]')
        return "\n".join(mermaid_lines)

    mermaid_lines.extend(
        [
            '    E --> F["申报前阶段 pre_declare_stage"]',
            f'    F --> G["规则命中诊断<br/>{format_rule_matches(diagnostics["rule_matches"])}"]',
            f'    G --> H["基础规则执行<br/>{matched_rules}"]',
            f'    H --> I["模式后置调度<br/>{diagnostics["dispatch_path"]}"]',
            f'    I --> J["{format_stage_summary("申报前输出", pre_stage)}"]',
            '    J --> K["申报后阶段 declare_stage"]',
            f'    K --> L["复用同一规则流<br/>{diagnostics["dispatch_path"]}"]',
            f'    L --> M["{format_stage_summary("申报后输出", declare_stage)}"]',
        ]
    )
    return "\n".join(mermaid_lines)


def build_case_markdown(case_info: Dict, allowed: bool, diagnostics: Dict, pre_stage: Dict, declare_stage: Dict) -> str:
    """
    生成完整 Mermaid Markdown 文本。
    """
    mermaid_text = build_mermaid_text(case_info, allowed, diagnostics, pre_stage, declare_stage)
    return "\n".join(
        [
            "# 需求响应测试规则流",
            "",
            f"- route: `{case_info['route']}`",
            f"- period_type: `{case_info['period_type']}`",
            f"- response_mode: `{case_info['response_mode']}`",
            f"- response_period: `{case_info['response_start']} ~ {case_info['response_end']}`",
            "",
            "```mermaid",
            mermaid_text,
            "```",
            "",
        ]
    )


def get_visual_result_dir(*, current_time, response_period: Dict, response_mode: str, route: str) -> Path:
    """
    计算与绘图 PNG 完全一致的结果目录。
    """
    response_time_len = (
        response_period["end"] + pd.Timedelta(minutes=5) - response_period["start"]
    ).total_seconds() / 3600
    notice_time_len = round((response_period["start"] - current_time).total_seconds() / 3600, 2)
    response_date = response_period["start"].date()
    return Path(
        f"./model/model_packages/Demand_Response_optim/result/{response_date}/{response_mode}/{route}/response-{response_time_len}/notice-{notice_time_len}"
    )


def save_rule_flow_mermaid(
    *,
    input_data: Dict,
    period_map: Dict,
    df_strategy_period,
    result_pre_declare_stage: Dict,
    result_declare_stage: Dict,
    allowed: bool,
) -> Path:
    """
    按与可视化图片相同的路径规则保存 Mermaid 规则流程图。
    """
    diagnostics = collect_rule_diagnostics(df_strategy_period, period_map)
    case_info = {
        "route": input_data["route"],
        "response_mode": input_data["response_mode"],
        "current_time": period_map["current_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "response_start": period_map["response"]["start"].strftime("%Y-%m-%d %H:%M:%S"),
        "response_end": period_map["response"]["end"].strftime("%Y-%m-%d %H:%M:%S"),
        "period_type": period_map["period_profile"]["period_type"],
    }
    markdown_text = build_case_markdown(
        case_info=case_info,
        allowed=allowed,
        diagnostics=diagnostics,
        pre_stage=result_pre_declare_stage,
        declare_stage=result_declare_stage,
    )
    result_dir = get_visual_result_dir(
        current_time=period_map["current_time"],
        response_period=period_map["response"],
        response_mode=input_data["response_mode"],
        route=input_data["route"],
    )
    result_dir.mkdir(parents=True, exist_ok=True)
    file_name = (
        f"{input_data['route']}-"
        f"{input_data['response_mode']}-"
        f"{period_map['response']['start'].hour:02d}{period_map['response']['start'].minute:02d}-"
        f"{period_map['response']['end'].hour:02d}{period_map['response']['end'].minute:02d}"
    )
    output_path = result_dir.joinpath(f"算法规则流程-[{file_name}].md")
    output_path.write_text(markdown_text, encoding="utf-8")
    return output_path
