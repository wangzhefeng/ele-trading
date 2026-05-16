from datetime import timedelta
from typing import Dict

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response_optim.utils.tools import extract_daily_period_data
from utils.log_util import logger


def apply_strategy_to_history_future(df: pd.DataFrame, df_strategy_new: pd.DataFrame, period_map: Dict):
    """
    把调整后的策略回写到完整时间轴，为基线重算做准备。
    """
    df_5min = df.copy()
    if df_strategy_new is not None and period_map["strategy"] is not None:
        df_history_future_mask = (
            (df_5min["time"] >= period_map["strategy"]["start"])
            & (df_5min["time"] <= period_map["strategy"]["end"])
        )
        df_strategy_period_mask = (
            (df_strategy_new["time"] >= period_map["strategy"]["start"])
            & (df_strategy_new["time"] <= period_map["strategy"]["end"])
        )
        df_5min.loc[df_history_future_mask, "strategy_load"] = df_strategy_new.loc[
            df_strategy_period_mask, "strategy_load"
        ].values
    return df_5min


def resample_aidc_load_15min(df_5min: pd.DataFrame):
    """
    将 5 分钟 AIDC 负荷转换为 15 分钟基线计算粒度。
    """
    return df_5min[["time", "aidc_load"]].set_index("time").resample(
        "15min", label="left", closed="right"
    ).mean()


def calc_baseline_coef(df_15min: pd.DataFrame, period_map: Dict, response_reference_date, coef_reference_date=None):
    """
    比较历史样本与响应参考日，计算基线修正系数。
    """
    current_date = period_map["current_time"].date()
    df_baseline_coef_data = extract_daily_period_data(df_15min, "aidc_load", period_map["baseline_coef"])
    df_baseline_coef_hist = df_baseline_coef_data.loc[
        :,
        [col for col in df_baseline_coef_data.columns if col not in [current_date, response_reference_date]],
    ]
    logger.info(f"debug::df_baseline_coef_hist: \n{df_baseline_coef_hist}")

    coef_reference_date = coef_reference_date or response_reference_date
    if coef_reference_date not in df_baseline_coef_data.columns:
        coef_reference_date = response_reference_date
    df_baseline_coef_resp = df_baseline_coef_data.loc[:, coef_reference_date]

    hist_value = np.nanmean(df_baseline_coef_hist.apply(lambda x: np.nanmean(x), axis=1).values)
    resp_value = np.nanmean(df_baseline_coef_resp.values)

    if np.isnan(hist_value) or hist_value == 0 or np.isnan(resp_value):
        baseline_coef = 1.0
    else:
        baseline_coef = resp_value / hist_value
        baseline_coef = np.nanmin([baseline_coef, 1.2])
        if np.isnan(baseline_coef) or baseline_coef <= 0:
            baseline_coef = 1.0

    logger.info(f"debug::baseline_coef: {baseline_coef}")
    return baseline_coef


def calc_adjusted_baseline(df_15min: pd.DataFrame, period_map: Dict, response_reference_date, baseline_coef: float):
    """
    对历史平均基线乘以修正系数，得到最终响应基线。
    """
    current_date = period_map["current_time"].date()
    df_baseline_data = extract_daily_period_data(df_15min, "aidc_load", period_map["response"])
    df_baseline_hist = df_baseline_data.loc[
        :,
        [col for col in df_baseline_data.columns if col not in [current_date, response_reference_date]],
    ].apply(lambda x: np.nanmean(x), axis=1).to_frame()
    df_baseline_hist["time"] = period_map["response_df_15min"]["time"].values
    df_baseline_hist.reset_index(inplace=True, drop=True)
    df_baseline_hist.columns = ["value", "time"]

    df_baseline = df_baseline_hist.copy()
    df_baseline["value"] = df_baseline["value"].apply(lambda x: x * baseline_coef)
    df_baseline = df_baseline[["time", "value"]]
    logger.info(f"debug::df_baseline: \n{df_baseline}")
    return df_baseline


def calc_baseline(
    df: pd.DataFrame,
    df_strategy_new: pd.DataFrame,
    period_map: Dict,
    response_reference_date,
    coef_reference_date=None,
):
    """
    根据调整后的策略，重算需求响应时段的基线负荷。
    """
    df_5min = apply_strategy_to_history_future(df, df_strategy_new, period_map)
    df_15min = resample_aidc_load_15min(df_5min)
    baseline_coef = calc_baseline_coef(
        df_15min,
        period_map,
        response_reference_date=response_reference_date,
        coef_reference_date=coef_reference_date,
    )
    return calc_adjusted_baseline(
        df_15min,
        period_map,
        response_reference_date=response_reference_date,
        baseline_coef=baseline_coef,
    )
