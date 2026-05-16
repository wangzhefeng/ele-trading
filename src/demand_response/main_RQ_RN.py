import sys
from pathlib import Path
PROJECT_ROOT = str(Path(__file__).resolve().parents[3])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from datetime import timedelta
from typing import Dict

import pandas as pd

from model import BaseModelMainClass
from model.model_packages.Demand_Response_optim.engine.period_context import (
    preprocessing_period,
)
from model.model_packages.Demand_Response_optim.engine.input_context import (
    load_optional_response_frame,
    preprocessing_input_data,
)
from model.model_packages.Demand_Response_optim.engine.power_bounds import (
    get_charge_max_load,
    get_peak1_discharge_max_load,
    get_peak2_discharge_max_load,
)
from model.model_packages.Demand_Response_optim.engine.eligibility import (
    day_notification_allowed,
    day_response_period_allowed,
    night_response_period_allowed,
)
from model.model_packages.Demand_Response_optim.engine.response_load import (
    pre_declare_stage,
    declare_cleaning_response_stage,
)
from model.model_packages.Demand_Response_optim.engine.response_profit import (
    get_response_price,
)
from model.model_packages.Demand_Response_optim.utils.mermaid_rule_flow_common import (
    save_rule_flow_mermaid,
)
from model.model_packages.Demand_Response_optim.utils.data_visual import (
    check_input_data,
    plot_results,
)
from utils.log_util import logger


class ModelMainClass(BaseModelMainClass):
    """
    需求响应生产入口，统一承载输入校验、时段构造、数据装配和阶段调度。
    """
    ALLOWED_RESPONSE_MODES = {"日前", "日内", "日内-快速"}

    REQUIRED_INPUT_KEYS = [
        "current_time",
        "response_period",
        "response_type",
        "response_mode",
        "aidc_load_history",
        "demand_load_history",
        "demand_load_predict",
        "strategy_load_history",
        "strategy_load_predict",
        "ele_price",
        "soc_history",
        "df_date",
    ]

    def __init__(self, args=None) -> None:
        super().__init__(args=args)

    # ##############################
    # 输入校验与标准化
    # ##############################
    def prepare_input_data(self, input_data: Dict) -> Dict:
        """
        统一校验并标准化输入，确保主链路只处理规范化后的数据。
        """
        # input_data normalize
        normalized_input_data = dict(input_data)
        logger.info(f"debug::input_data: \n{normalized_input_data}")
        # ------------------------------
        # input_data check
        # ------------------------------
        # input_data items check
        missing_keys = [key for key in self.REQUIRED_INPUT_KEYS if key not in normalized_input_data]
        if missing_keys:
            raise KeyError(f"Missing required input_data keys: {missing_keys}")

        # input_data["current_time"] check
        if normalized_input_data["current_time"] is None:
            raise ValueError("input_data['current_time'] must not be None")

        # input_data["response_period"] check
        response_period = normalized_input_data["response_period"]
        if not isinstance(response_period, dict) or "start" not in response_period or "end" not in response_period:
            raise ValueError("input_data['response_period'] must contain 'start' and 'end'")

        # ------------------------------
        # input_data normalize
        # ------------------------------
        # current_time, response_period, response_mode normalize
        normalized_input_data["current_time"] = pd.to_datetime(normalized_input_data["current_time"]).floor("5min")
        normalized_input_data["response_period"] = {
            "start": pd.to_datetime(response_period["start"]),
            "end": pd.to_datetime(response_period["end"]),
        }
        normalized_input_data["response_mode"] = str(normalized_input_data["response_mode"]).strip()

        if normalized_input_data["response_period"]["start"] >= normalized_input_data["response_period"]["end"]:
            raise ValueError("input_data['response_period']['start'] must be earlier than 'end'")

        # input_data["response_mode"] check
        if normalized_input_data["response_mode"] not in self.ALLOWED_RESPONSE_MODES:
            raise ValueError(f"input_data['response_mode'] must be one of {sorted(self.ALLOWED_RESPONSE_MODES)}")

        response_notice_hours = (
            normalized_input_data["response_period"]["start"] - normalized_input_data["current_time"]
        ).total_seconds() / 3600.0
        if normalized_input_data["response_mode"] == "日前" and response_notice_hours < 8.0:
            raise ValueError("response_mode '日前' requires notification lead time >= 8 hours")
        if normalized_input_data["response_mode"] == "日内" and not (2.0 <= response_notice_hours < 8.0):
            raise ValueError("response_mode '日内' requires 2 <= notification lead time < 8 hours")
        if normalized_input_data["response_mode"] == "日内-快速" and response_notice_hours >= 2.0:
            raise ValueError("response_mode '日内-快速' requires notification lead time < 2 hours")

        return normalized_input_data

    # ##############################
    # 可选输入装配
    # ##############################
    def preprocessing_baseline(self, input_data: Dict, period_map: Dict):
        """
        读取申报、出清、实际基线等可选输入。
        """
        response_time = period_map["response_df_15min"]["time"]
        df_declare_baseline = load_optional_response_frame(input_data, "declare_baseline", response_time)
        df_clearing_baseline = load_optional_response_frame(input_data, "clearing_baseline", response_time)
        df_actual_baseline = load_optional_response_frame(input_data, "actual_baseline", response_time)
        return (df_declare_baseline, df_clearing_baseline, df_actual_baseline)

    def preprocessing_response_load(self, input_data: Dict, period_map: Dict):
        """
        读取申报负荷预测和出清负荷等可选输入。
        """
        response_time = period_map["response_df_15min"]["time"]
        df_declare_load_pred = load_optional_response_frame(
            input_data, "declare_load_pred", response_time, divide_by_two=True
        )
        df_clearing_load = load_optional_response_frame(
            input_data, "clearing_load", response_time, divide_by_two=True
        )
        return (df_declare_load_pred, df_clearing_load)

    # ##############################
    # 资格判断与功率边界
    # ##############################
    def should_respond(self, period_map: Dict) -> bool:
        """
        判断当前响应任务是否落在允许参与的业务窗口内。
        """
        period_type = period_map["period_profile"]["period_type"]
        if period_type == "day":
            period_allowed = day_response_period_allowed(response_period=period_map["response"])
            notification_allowed = day_notification_allowed(
                current_time=period_map["current_time"],
                response_date=period_map["response_date"],
            )
            logger.info(f"debug::period_allowed: {period_allowed}")
            logger.info(f"debug::notification_allowed: {notification_allowed}")
            return period_allowed and notification_allowed
        else:
            period_allowed = night_response_period_allowed(response_period=period_map["response"])
            logger.info(f"debug::period_allowed: {period_allowed}")
            return period_allowed

    def build_no_response_stage_output(self) -> Dict:
        """
        构造不参与需求响应时统一返回的空结果结构。
        """
        return {
            "response_load": None,
            "response_capacity": None,
            "response_baseline": None,
            "response_strategy": None,
            "response_profit": None,
        }

    def get_stage_power_kwargs(self, df_history_future, period_map: Dict) -> Dict:
        """
        提取峰时放电与充电功率边界，供后续阶段复用。
        """
        return {
            "period_profile": period_map["period_profile"],
            "peak1_max_discharge_load": get_peak1_discharge_max_load(df_history_future, period_map),
            "peak2_max_discharge_load": get_peak2_discharge_max_load(df_history_future, period_map),
            "max_charge_load": get_charge_max_load(df_history_future, period_map),
        }

    # ##############################
    # 结果可视化
    # ##############################
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
        按场景输出调图，便于核对策略调整前后的差异。

        白天和跨夜采用不同绘图窗口：
        - 白天日前模式补上策略开始前 22 小时历史，方便查看通知前后的策略衔接。
        - 跨夜模式聚焦晚高峰、响应时段、夜间充电和次日上午高峰，避免整段策略期过长导致关键信息被稀释。
        """
        if result_declare_stage.get("response_strategy") is None or result_declare_stage.get("response_baseline") is None:
            return
        if period_map["period_profile"]["period_type"] == "day" and input_data["response_mode"] == "日前":
            df_strategy_period_plot = pd.concat([
                df_history_future.loc[
                    (pd.to_datetime(df_history_future["time"]) >= period_map["strategy"]["start"] - timedelta(hours=22))
                    & (pd.to_datetime(df_history_future["time"]) < period_map["strategy"]["start"]), :
                ],
                df_strategy_period,
            ], axis=0)
        elif period_map["period_profile"]["period_type"] == "night":
            df_strategy_period_plot = df_history_future.loc[
                (pd.to_datetime(df_history_future["time"]) >= period_map["peak1_discharge"]["start"])
                & (pd.to_datetime(df_history_future["time"]) <= period_map["peak2_discharge"]["end"]), :
            ].copy()
        else:
            df_strategy_period_plot = df_strategy_period
        plot_results(
            df_strategy_period_plot,
            result_declare_stage["response_strategy"],
            result_declare_stage["response_baseline"],
            period_map,
            input_data["response_mode"],
            input_data["route"],
        )
        save_rule_flow_mermaid(
            input_data=input_data,
            period_map=period_map,
            df_strategy_period=df_strategy_period,
            result_pre_declare_stage=result_pre_declare_stage,
            result_declare_stage=result_declare_stage,
            allowed=True,
        )

    # ##############################
    # 主流程调度
    # ##############################
    def run(self, input_data: Dict, model_cfgs: Dict):
        """
        统一执行需求响应主流程。
        """
        # ##############################
        # 数据验证
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::开始`数据验证`...")
        logger.info(f"{'=' * 100}")
        input_data = self.prepare_input_data(input_data)
        # ##############################
        # 数据预处理-时段处理
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::数据预处理-时段处理...")
        logger.info(f"{'=' * 100}")
        period_map = preprocessing_period(
            response_period=input_data["response_period"],
            current_time=input_data["current_time"],
            response_mode=input_data["response_mode"],
            verbose=True,
        )
        if hasattr(self, "period_map"):
            self.period_map = period_map
        # ##############################
        # 输入数据预处理
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::数据预处理-输入数据预处理...")
        logger.info(f"{'=' * 100}")
        df_history_future, df_strategy_period, df_response_period = preprocessing_input_data(
            input_data=input_data,
            period_map=period_map,
            device_info=model_cfgs["devices_info"],
            current_date=period_map["current_date"],
            response_date=period_map["period_profile"]["response_reference_date"],
            fillna_history=period_map["period_profile"]["fillna_history"],
            history_cutoff_hour=period_map["period_profile"]["history_cutoff_hour"],
            recompute_missing_aidc=period_map["period_profile"]["recompute_missing_aidc"],
            verbose=True,
        )
        if hasattr(self, "df_history_future"):
            self.df_history_future = df_history_future
        if hasattr(self, "df_strategy_period"):
            self.df_strategy_period = df_strategy_period
        if hasattr(self, "df_response_period"):
            self.df_response_period = df_response_period

        if input_data.get("data_check", False):
            check_input_data(
                df_history_future,
                df_strategy_period,
                period_map,
                input_data["response_mode"],
                input_data["route"],
            )
        # ##############################
        # 计算历史数据中峰时放电最大功率、平时充电最大功率
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::数据预处理-计算历史数据中峰时放电最大功率、平时充电最大功率...")
        logger.info(f"{'=' * 100}")
        power_kwargs = self.get_stage_power_kwargs(df_history_future, period_map)
        # ##############################
        # 基线计算
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::数据预处理-基线负荷...")
        logger.info(f"{'=' * 100}")
        df_declare_baseline, df_clearing_baseline, df_actual_baseline = self.preprocessing_baseline(
            input_data=input_data, period_map=period_map
        )
        # ##############################
        # 响应负荷
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::数据预处理-响应负荷...")
        logger.info(f"{'=' * 100}")
        df_declare_load_pred, df_clearing_load = self.preprocessing_response_load(
            input_data=input_data, period_map=period_map
        )
        # ##############################
        # 需求响应价格计算
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::数据预处理-需求响应价格计算...")
        logger.info(f"{'=' * 100}")
        response_price = get_response_price(
            response_type=input_data["response_type"],
            current_time=period_map["current_time"],
            response_period=period_map["response"],
        )
        # ##############################
        # 根据需求响应时段判断是否进行需求响应
        # ##############################
        # 不进行需求响应时的输出结果
        no_response_stage_output = self.build_no_response_stage_output()

        logger.info(f"{'=' * 100}")
        logger.info("debug::判断需求响应时段是否在可调整时段内...")
        logger.info(f"{'=' * 100}")
        if not self.should_respond(period_map):
            logger.info("debug::需求响应时段不在可调整时段内，不进行需求响应!")
            logger.info(f"{'=' * 100}")
            logger.info("debug::输出最终结果...")
            logger.info(f"{'=' * 100}")
            no_response_output = {
                "response_strategy_pre_declare": {"response_load": None, "response_capacity": None, "response_profit": None},
                "response_strategy_declare": no_response_stage_output,
                "response_strategy_clearing": no_response_stage_output,
                "response_strategy": no_response_stage_output,
            }
            logger.info(f"output: \n{no_response_output}")
            return no_response_output
        else:
            logger.info("debug::需求响应时段在可调整时段内，进行需求响应...")
        # ##############################
        # 申报阶段-申报前
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::开始`申报阶段-申报前`计算...")
        logger.info(f"{'=' * 100}")
        if df_declare_load_pred is None:
            logger.info("debug::没有申报负荷数据，进行`申报阶段-申报前`计算...")
            result_pre_declare_stage = pre_declare_stage(
                df_baseline=df_declare_baseline,
                df_history_future=df_history_future,
                df_strategy_period=df_strategy_period,
                period_map=period_map,
                clearing_price=response_price,
                device_info=model_cfgs["devices_info"],
                **power_kwargs,
            )
        else:
            logger.info("debug::有申报负荷数据，不进行`申报阶段-申报前`计算!")
            result_pre_declare_stage = no_response_stage_output
        logger.info(f"debug::result_pre_declare_stage: \n{result_pre_declare_stage}")
        # ##############################
        # 申报阶段-申报后
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::开始`申报阶段-申报后`计算...")
        logger.info(f"{'=' * 100}")
        df_declare_load_pred = result_pre_declare_stage["response_load"]
        logger.info(f"debug::df_declare_load_pred: \n{df_declare_load_pred}")
        if df_declare_load_pred is not None:
            logger.info("debug::有申报负荷数据，进行`申报阶段-申报后`计算!")
            result_delcare_stage = declare_cleaning_response_stage(
                df_baseline=df_declare_baseline,
                df_response_load=df_declare_load_pred,
                df_history_future=df_history_future,
                df_strategy_period=df_strategy_period,
                period_map=period_map,
                clearing_price=response_price,
                device_info=model_cfgs["devices_info"],
                **power_kwargs,
            )
        else:
            logger.info("debug::没有申报负荷数据，不进行`申报阶段-申报后`计算!")
            result_delcare_stage = no_response_stage_output
        logger.info(f"debug::result_delcare_stage: \n{result_delcare_stage}")
        # ##############################
        # 模型输出结果可视化
        # ##############################
        if input_data.get("result_visual", False):
            self.visualize_results(
                input_data=input_data,
                period_map=period_map,
                df_history_future=df_history_future,
                df_strategy_period=df_strategy_period,
                result_pre_declare_stage=result_pre_declare_stage,
                result_declare_stage=result_delcare_stage,
            )
        # ##############################
        # 模型输出结果返回
        # ##############################
        logger.info(f"{'=' * 100}")
        logger.info("debug::输出最终结果...")
        logger.info(f"{'=' * 100}")
        output = {
            "response_strategy_pre_declare": result_pre_declare_stage,
            "response_strategy_declare": result_delcare_stage,
        }
        logger.info(f"output: \n{output}")
        return output

def build_main_debug_case() -> Dict:
    """
    导出 main() 当前使用的单例调试场景，供测试工具复用。
    """
    response_date = "2026-01-23"
    return {
        "route": "lingang_A",
        "response_date": response_date,
        "current_time": "2026-01-23 16:00:00",
        "response_mode": "日内-快速",
        "response_start": f"{response_date} 17:00:00",
        "response_end": f"{response_date} 18:30:00",
        "data_check": True,
        "result_visual": True,
        "result_save": True,
    }


def main():
    """
    本地单例调试入口，便于快速验证一组输入是否跑通。
    """
    from model.model_packages.Demand_Response_optim.testing.localtest_support import (
        build_model_cfgs,
        load_case_input,
    )

    # 模型配置
    model_cfgs = build_model_cfgs()
    # 模型输入
    project_dir = Path(__file__).resolve().parent
    debug_case = build_main_debug_case()
    input_data = load_case_input(
        project_dir=project_dir,
        route=debug_case["route"], 
        response_date=debug_case["response_date"],
        current_time=debug_case["current_time"],
        response_start=debug_case["response_start"],
        response_end=debug_case["response_end"],
        response_mode=debug_case["response_mode"],
        data_check=debug_case["data_check"],
        result_visual=debug_case["result_visual"],
        result_save=debug_case["result_save"],
    )
    output = ModelMainClass().run(input_data, model_cfgs)


if __name__ == "__main__":
    main()
