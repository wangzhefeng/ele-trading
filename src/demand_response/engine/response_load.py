from typing import Dict

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response_optim.engine.response_baseline import calc_baseline
from model.model_packages.Demand_Response_optim.engine.response_strategy import (
    profit_output,
    strategy_adjust_model,
)
from model.model_packages.Demand_Response_optim.utils.tools import extract_daily_period_data
from utils.log_util import logger


def build_stage_output(
    *,
    response_load=None,
    response_capacity=None,
    response_baseline=None,
    response_strategy=None,
    response_profit=None,
):
    return {
        "response_load": response_load,
        "response_capacity": response_capacity,
        "response_baseline": response_baseline,
        "response_strategy": response_strategy,
        "response_profit": response_profit,
    }


def _strategy_result(strategy_result: Dict):
    """
    从策略调整结果中解包出策略表和峰一放电负荷。
    """
    if strategy_result is None:
        return None, None
    return strategy_result.get("strategy_df"), strategy_result.get("peak1_discharge_load")


def calc_response_capacity(df_response_load: pd.DataFrame, load_col: str, device_info: Dict, verbose: bool = True):
    """
    把 15 分钟响应负荷曲线积分成响应电量，并受设备容量上限约束。
    """
    response_capacity = np.nanmin(
        [df_response_load[load_col].sum() * (15 / 60), device_info["es_capacity_max"]]
    )
    if verbose:
        logger.info(f"debug::response_capacity: {response_capacity} kWh")
    return response_capacity


def get_response_power(
    response_period_df: pd.DataFrame,
    df_baseline: pd.DataFrame,
    df_response_period: pd.DataFrame,
    df_strategy: pd.DataFrame,
) -> pd.DataFrame:
    """
    根据基线、需求负荷和策略负荷计算实际响应负荷。
    """
    strategy_response = response_period_df.copy()
    strategy_response["baseline_load"] = strategy_response["time"].map(df_baseline.set_index("time")["value"])
    strategy_response["demand_load"] = strategy_response["time"].map(
        df_response_period.set_index("time")["demand_load"]
    )
    strategy_response["strategy_load"] = strategy_response["time"].map(
        df_strategy.set_index("time")["strategy_load"]
    )
    strategy_response["value"] = strategy_response.apply(
        lambda x: (x["baseline_load"] - x["demand_load"] + x["strategy_load"]),
        axis=1,
    )
    logger.info(f"debug::strategy_response: \n{strategy_response}")
    return strategy_response[["time", "value"]]


def get_pred_response_power(
    response_period_df: pd.DataFrame,
    df_baseline: pd.DataFrame,
    baseline_col: str,
    df_strategy_period: pd.DataFrame,
    strategy_load_pred: float,
    device_info: Dict,
    method: str = None,
):
    """
    根据预测基线和策略负荷估算可申报的响应负荷与容量。
    """
    strategy_response = response_period_df.copy()
    if method == "pred":
        strategy_response["baseline_load_min"] = strategy_response["time"].map(
            df_baseline.set_index("time")["aidc_load_min"]
        )
        strategy_response["baseline_load_mean"] = strategy_response["time"].map(
            df_baseline.set_index("time")["aidc_load_mean"]
        )
        strategy_response["baseline_load_max"] = strategy_response["time"].map(
            df_baseline.set_index("time")["aidc_load_max"]
        )
    else:
        strategy_response["baseline_load"] = strategy_response["time"].map(
            df_baseline.set_index("time")[baseline_col]
        )

    if method == "pred":
        strategy_response["demand_load_min"] = strategy_response["time"].map(
            df_strategy_period.set_index("time")["demand_load_min"]
        )
        strategy_response["demand_load_mean"] = strategy_response["time"].map(
            df_strategy_period.set_index("time")["demand_load_mean"]
        )
        strategy_response["demand_load_max"] = strategy_response["time"].map(
            df_strategy_period.set_index("time")["demand_load_max"]
        )
    else:
        strategy_response["demand_load"] = strategy_response["time"].map(
            df_strategy_period.set_index("time")["demand_load"]
        )

    strategy_response["strategy_load_pred"] = strategy_load_pred
    if method == "pred":
        strategy_response["value_min"] = strategy_response.apply(
            lambda x: (x["baseline_load_min"] - (x["demand_load_max"] - x["strategy_load_pred"])),
            axis=1,
        )
        strategy_response["value_mean"] = strategy_response.apply(
            lambda x: (x["baseline_load_mean"] - (x["demand_load_mean"] - x["strategy_load_pred"])),
            axis=1,
        )
        strategy_response["value_max"] = strategy_response.apply(
            lambda x: (x["baseline_load_max"] - (x["demand_load_min"] - x["strategy_load_pred"])),
            axis=1,
        )
        df_response_load = strategy_response[["time", "value_min", "value_mean", "value_max"]]
    else:
        strategy_response["value"] = strategy_response.apply(
            lambda x: (x["baseline_load"] - (x["demand_load"] - x["strategy_load_pred"])),
            axis=1,
        )
        df_response_load = strategy_response[["time", "value"]]

    with pd.option_context("display.max_rows", None, "display.max_columns", None):
        logger.info(f"debug::strategy_response: \n{strategy_response}")

    if method == "pred":
        response_capacity_min = calc_response_capacity(df_response_load, "value_min", device_info, verbose=False)
        response_capacity_mean = calc_response_capacity(df_response_load, "value_mean", device_info, verbose=False)
        response_capacity_max = calc_response_capacity(df_response_load, "value_max", device_info, verbose=False)
        response_capacity = [response_capacity_min, response_capacity_mean, response_capacity_max]
    else:
        response_capacity = calc_response_capacity(df_response_load, "value", device_info, verbose=False)
    logger.info(f"debug::response_capacity: {response_capacity} kWh")

    return df_response_load, response_capacity


def build_history_response_stats(df_history_future: pd.DataFrame, period_map: Dict):
    """
    提取历史样本日的响应窗口统计量，供申报前容量估算使用。
    """
    history_data_mask = df_history_future["time"] < pd.to_datetime(f"{period_map['current_time'].date()} 00:00:00")

    df_aidc_load_history = extract_daily_period_data(
        data=df_history_future.loc[history_data_mask, :],
        data_col="aidc_load",
        time_period=period_map["response"],
    )
    df_aidc_load_history["aidc_load_min"] = df_aidc_load_history.apply(lambda x: np.nanmin(x), axis=1)
    df_aidc_load_history["aidc_load_mean"] = df_aidc_load_history.apply(lambda x: np.nanmean(x), axis=1)
    df_aidc_load_history["aidc_load_max"] = df_aidc_load_history.apply(lambda x: np.nanmax(x), axis=1)
    df_aidc_load_history["time"] = period_map["response_df"]["time"].values
    df_aidc_load_history = df_aidc_load_history.reset_index(drop=True)
    df_aidc_load_history = df_aidc_load_history[["time", "aidc_load_min", "aidc_load_mean", "aidc_load_max"]]

    df_demand_load_history = extract_daily_period_data(
        data=df_history_future.loc[history_data_mask, :],
        data_col="demand_load",
        time_period=period_map["response"],
    )
    df_demand_load_history["demand_load_min"] = df_demand_load_history.apply(lambda x: np.nanmin(x), axis=1)
    df_demand_load_history["demand_load_mean"] = df_demand_load_history.apply(lambda x: np.nanmean(x), axis=1)
    df_demand_load_history["demand_load_max"] = df_demand_load_history.apply(lambda x: np.nanmax(x), axis=1)
    df_demand_load_history["time"] = period_map["response_df"]["time"].values
    df_demand_load_history = df_demand_load_history.reset_index(drop=True)
    df_demand_load_history = df_demand_load_history[
        ["time", "demand_load_min", "demand_load_mean", "demand_load_max"]
    ]

    with pd.option_context("display.max_rows", None, "display.max_columns", None):
        logger.info(f"debug::df_aidc_load_history: \n{df_aidc_load_history}")
        logger.info(f"debug::df_demand_load_history: \n{df_demand_load_history}")

    return df_aidc_load_history, df_demand_load_history


def calc_response_strategy_load_pred(df_strategy_new: pd.DataFrame, response_period: Dict, max_discharge_load: float):
    """
    计算调整后策略在响应窗口内的平均可申报策略功率。
    """
    response_period_mask = (
        (df_strategy_new["time"] >= response_period["start"])
        & (df_strategy_new["time"] <= response_period["end"])
    )
    strategy_load_pred = np.nanmean(df_strategy_new.loc[response_period_mask, "strategy_load"].values)
    strategy_load_pred = np.nanmin([strategy_load_pred, max_discharge_load])
    logger.info(f"debug::strategy_load_pred: {strategy_load_pred} kW")
    return strategy_load_pred


def pre_declare_stage(df_baseline: pd.DataFrame,
                      df_history_future: pd.DataFrame,
                      df_strategy_period: pd.DataFrame,
                      period_map: Dict,
                      period_profile: Dict,
                      peak1_max_discharge_load: float,
                      peak2_max_discharge_load: float,
                      max_charge_load: float,
                      clearing_price: float,
                      device_info: Dict,
                      level: str = "min"):
    """
    执行申报前阶段，先估算容量再回推基线。
    """
    profit_df = None
    if df_baseline is None:
        logger.info(f"{'-' * 50}")
        logger.info("debug::可调负荷估计...")
        logger.info(f"{'-' * 50}")
        df_aidc_load_history, df_demand_load_history = build_history_response_stats(
            df_history_future, period_map
        )
        response_load_pred, response_capacity_pred = get_pred_response_power(
            response_period_df=period_map["response_df_15min"],
            df_baseline=df_aidc_load_history,
            baseline_col="aidc_load",
            df_strategy_period=df_demand_load_history,
            strategy_load_pred=peak1_max_discharge_load,
            device_info=device_info,
            method="pred",
        )
        logger.info(f"{'-' * 50}")
        logger.info("debug::根据预估的可调负荷调整需求响应策略...")
        logger.info(f"{'-' * 50}")
        if level == "min":
            response_capacity = response_capacity_pred[0]
        elif level == "mean":
            response_capacity = response_capacity_pred[1]
        else:
            response_capacity = response_capacity_pred[2]
        logger.info(f"debug::response_capacity: {response_capacity} kWh")
        strategy_result = strategy_adjust_model(
            df_strategy_period=df_strategy_period,
            response_capacity=response_capacity,
            period_map=period_map,
            period_profile=period_profile,
            peak1_max_discharge_load=peak1_max_discharge_load,
            peak2_max_discharge_load=peak2_max_discharge_load,
            max_charge_load=max_charge_load,
            clearing_price=clearing_price,
        )
        df_strategy_new, peak1_discharge_load = _strategy_result(strategy_result)
        if df_strategy_new is None:
            return build_stage_output()
        if period_profile["period_type"] == "night":
            df_strategy_period_raw = df_strategy_period[["time", "ele_price", "strategy_load"]].copy()
            profit_df = profit_output(
                df_strategy_period_raw=df_strategy_period_raw,
                df_strategy_period_new=df_strategy_new,
                period_map=period_map,
                period_profile=period_profile,
                peak1_discharge_load=peak1_discharge_load,
                response_capacity=response_capacity,
                clearing_price=clearing_price,
            )
        logger.info(f"{'-' * 50}")
        logger.info("debug::根据预估的可调负荷计算预估基线负荷...")
        logger.info(f"{'-' * 50}")
        df_baseline = calc_baseline(
            df=df_history_future,
            df_strategy_new=df_strategy_new,
            period_map=period_map,
            response_reference_date=period_profile["response_reference_date"],
            coef_reference_date=period_profile["coef_reference_date"],
        )
    strategy_load_pred = calc_response_strategy_load_pred(
        df_strategy_new, period_map["response"], peak1_max_discharge_load
    )
    logger.info(f"{'-' * 50}")
    logger.info("debug::根据预估的基线负荷计算申报前可调负荷...")
    logger.info(f"{'-' * 50}")
    response_load, response_capacity = get_pred_response_power(
        response_period_df=period_map["response_df_15min"],
        df_baseline=df_baseline,
        baseline_col="value",
        df_strategy_period=df_strategy_period,
        strategy_load_pred=strategy_load_pred,
        device_info=device_info,
    )
    return build_stage_output(
        response_load=response_load,
        response_capacity=response_capacity,
        response_baseline=df_baseline,
        response_profit=profit_df,
    )


def declare_cleaning_response_stage(df_baseline: pd.DataFrame,
                                    df_response_load: float,
                                    df_history_future: pd.DataFrame,
                                    df_strategy_period: pd.DataFrame,
                                    period_map: Dict,
                                    period_profile: Dict,
                                    peak1_max_discharge_load: float,
                                    peak2_max_discharge_load: float,
                                    max_charge_load: float,
                                    clearing_price: float,
                                    device_info: Dict):
    """
    执行申报后阶段，输出正式响应结果与收益。
    """
    response_capacity = calc_response_capacity(df_response_load, "value", device_info)
    strategy_result = strategy_adjust_model(
        df_strategy_period=df_strategy_period,
        response_capacity=response_capacity,
        period_map=period_map,
        period_profile=period_profile,
        peak1_max_discharge_load=peak1_max_discharge_load,
        peak2_max_discharge_load=peak2_max_discharge_load,
        max_charge_load=max_charge_load,
        clearing_price=clearing_price,
    )
    df_strategy_new, peak1_discharge_load = _strategy_result(strategy_result)
    if df_strategy_new is None:
        return build_stage_output()
    df_strategy_period_raw = df_strategy_period[["time", "ele_price", "strategy_load"]].copy()
    profit_df = profit_output(
        df_strategy_period_raw=df_strategy_period_raw,
        df_strategy_period_new=df_strategy_new,
        period_map=period_map,
        period_profile=period_profile,
        peak1_discharge_load=peak1_discharge_load,
        response_capacity=response_capacity,
        clearing_price=clearing_price,
    )
    if df_baseline is None:
        df_baseline = calc_baseline(
            df=df_history_future,
            df_strategy_new=df_strategy_new,
            period_map=period_map,
            response_reference_date=period_profile["response_reference_date"],
            coef_reference_date=period_profile["coef_reference_date"],
        )
    response_load = get_response_power(
        response_period_df=period_map["response_df_15min"],
        df_baseline=df_baseline,
        df_response_period=df_strategy_period,
        df_strategy=df_strategy_new,
    )
    response_capacity = calc_response_capacity(response_load, "value", device_info)
    return build_stage_output(
        response_load=response_load,
        response_capacity=response_capacity,
        response_baseline=df_baseline,
        response_strategy=df_strategy_new[["time", "strategy_load"]],
        response_profit=profit_df,
    )
