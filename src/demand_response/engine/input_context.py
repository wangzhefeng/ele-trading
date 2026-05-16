import copy
from datetime import timedelta
from typing import Dict

import numpy as np
import pandas as pd

from model.model_packages.Demand_Response_optim.strategy.core import (
    calc_soc,
)
from model.model_packages.Demand_Response_optim.utils.tools import (
    find_adjacent_date,
)
from utils.log_util import logger


def process_frame_data(df, period_array, fillna: bool = False):
    """
    按目标时间轴对输入表做对齐，并按需填充缺失值。
    """
    df_processed = pd.DataFrame({"time": period_array})
    df_raw = copy.deepcopy(df)
    if df_raw is not None:
        df_raw["time"] = pd.to_datetime(df_raw["time"])
        df_raw.drop_duplicates(subset="time", keep="last", inplace=True, ignore_index=True)
        for col in df_raw.columns:
            if col not in ["time", "type", "property"]:
                df_raw[col] = df_raw[col].apply(lambda x: float(x))
            if col not in ["time", "type"]:
                df_processed[col] = df_processed["time"].map(df_raw.set_index("time")[col])
            if df_processed[col].isnull().any():
                logger.info(f"debug::{col} 缺失值检测: {df_processed[col].isna().sum()}")
            if fillna:
                df_processed[col] = df_processed[col].ffill()
                df_processed[col] = df_processed[col].bfill()
            if df_processed[col].isnull().any():
                logger.info(f"debug::{col} 缺失值再检测: {df_processed[col].isna().sum()}")
    return df_processed


def load_price_frame(input_data: Dict, period_map: Dict):
    """
    加载并对齐电价数据，作为收益和策略计算的价格基础。
    """
    logger.info(f"{'-' * 40}")
    logger.info("debug::process df_price...")
    return process_frame_data(input_data["ele_price"], period_map["data_df"]["time"], fillna=True)


def load_optional_response_frame(input_data: Dict, key: str, time_array, divide_by_two: bool = False):
    """
    加载可选的响应相关输入，如申报基线或申报负荷。
    """
    if input_data.get(key) is not None:
        df_loaded = process_frame_data(input_data[key], time_array)
        if divide_by_two:
            df_loaded["value"] = df_loaded["value"].apply(lambda x: x / 2.0)
        logger.info(f"debug::df_{key}: \n{df_loaded}")
        return df_loaded

    logger.info(f"debug::df_{key}: None")
    return None


def load_combined_history_future(history_df, predict_df, current_time, history_time, future_time):
    """
    把历史值和预测值拼成统一序列，再切成历史段和未来段。
    """
    df_all = pd.concat([history_df, predict_df], axis=0)
    df_all["time"] = pd.to_datetime(df_all["time"])
    df_history = df_all.loc[df_all["time"] <= current_time, :]
    df_history = process_frame_data(df_history, history_time, fillna=True)
    df_future = df_all.loc[df_all["time"] > current_time, :]
    df_future = process_frame_data(df_future, future_time, fillna=True)
    return df_history, df_future


def filter_history_frames_by_dates(
    history_frames: Dict[str, pd.DataFrame],
    df_date_set,
    current_date,
    response_date,
    cutoff_hour: int,
):
    """
    按响应日期过滤历史样本日，避免无关日期干扰统计。
    """
    if current_date == response_date:
        day_before_response_date = response_date - timedelta(days=1)
        if day_before_response_date in df_date_set:
            filter_dates = df_date_set | {response_date}
            return {
                key: df[df["time"].apply(lambda x: x.date() in filter_dates)]
                for key, df in history_frames.items()
            }

        filter_dates = df_date_set | {day_before_response_date, response_date}
        start_time = pd.to_datetime(f"{day_before_response_date} 00:00:00")
        end_time = pd.to_datetime(f"{day_before_response_date} {cutoff_hour:02d}:00:00")
        filtered_frames = {}
        for key, df in history_frames.items():
            filtered_df = df[df["time"].apply(lambda x: x.date() in filter_dates)]
            filtered_df = filtered_df.loc[
                (filtered_df["time"] < start_time) | (filtered_df["time"] >= end_time)
            ]
            filtered_frames[key] = filtered_df
        return filtered_frames

    filter_dates = df_date_set | {current_date}
    return {
        key: df[df["time"].apply(lambda x: x.date() in filter_dates)]
        for key, df in history_frames.items()
    }


class InputFrameBuilder:
    """
    统一负责历史未来表、策略表和响应表的构造。
    """

    def __init__(
        self,
        *,
        df_price,
        df_aidc_load_history,
        df_demand_load_history,
        df_strategy_load_history,
        df_soc_history,
        df_demand_load_predict,
        df_strategy_load_predict,
        period_map: Dict,
        device_info: Dict,
        df_date_set,
        all_df_date_set,
        recompute_missing_aidc: bool,
        verbose: bool,
    ):
        self.df_price = df_price
        self.df_aidc_load_history = df_aidc_load_history
        self.df_demand_load_history = df_demand_load_history
        self.df_strategy_load_history = df_strategy_load_history
        self.df_soc_history = df_soc_history
        self.df_demand_load_predict = df_demand_load_predict
        self.df_strategy_load_predict = df_strategy_load_predict
        self.period_map = period_map
        self.device_info = device_info
        self.df_date_set = df_date_set
        self.all_df_date_set = all_df_date_set
        self.recompute_missing_aidc = recompute_missing_aidc
        self.verbose = verbose

    def _build_history_frame(self):
        """
        构造历史段主表。
        """
        df_history = self.df_aidc_load_history[["time"]].copy()
        df_history["ele_price"] = df_history["time"].map(self.df_price.set_index("time")["price"])
        df_history["ele_type"] = df_history["time"].map(self.df_price.set_index("time")["property"])
        df_history["demand_load"] = df_history["time"].map(self.df_demand_load_history.set_index("time")["value"])
        df_history["strategy_load"] = df_history["time"].map(self.df_strategy_load_history.set_index("time")["value"])
        for date in self.all_df_date_set:
            df_history.loc[
                (df_history["time"] >= pd.to_datetime(f"{date} 10:00:00"))
                & (df_history["time"] <= pd.to_datetime(f"{date} 10:55:00")),
                "strategy_load",
            ] = 0.0
        df_history["aidc_load"] = df_history["time"].map(self.df_aidc_load_history.set_index("time")["value"])
        df_history["soc"] = df_history["time"].map(self.df_soc_history.set_index("time")["value"])
        return df_history

    def _build_future_frame(self):
        """
        构造未来预测段主表。
        """
        df_future = self.period_map["future_df"].copy()
        df_future["ele_price"] = df_future["time"].map(self.df_price.set_index("time")["price"])
        df_future["ele_type"] = df_future["time"].map(self.df_price.set_index("time")["property"])
        df_future["demand_load"] = df_future["time"].map(self.df_demand_load_predict.set_index("time")["value"])
        df_future["strategy_load"] = df_future["time"].map(self.df_strategy_load_predict.set_index("time")["value"])
        for date in self.all_df_date_set:
            df_future.loc[
                (df_future["time"] >= pd.to_datetime(f"{date} 10:00:00"))
                & (df_future["time"] <= pd.to_datetime(f"{date} 10:55:00")),
                "strategy_load",
            ] = 0.0
        df_future["aidc_load"] = df_future.apply(lambda x: x["demand_load"] - x["strategy_load"], axis=1)
        df_future["soc"] = np.nan
        return df_future

    def _build_strategy_period_frame(self, df_history, df_history_future):
        """
        构造策略期主表。
        """
        df_strategy_period = self.period_map["strategy_df"].copy()
        df_strategy_period["ele_price"] = df_strategy_period["time"].map(df_history_future.set_index("time")["ele_price"])
        df_strategy_period["ele_type"] = df_strategy_period["time"].map(df_history_future.set_index("time")["ele_type"])
        df_strategy_period["demand_load"] = df_strategy_period["time"].map(df_history_future.set_index("time")["demand_load"])
        df_strategy_period["strategy_load"] = df_strategy_period["time"].map(df_history_future.set_index("time")["strategy_load"])
        df_strategy_period["aidc_load"] = df_strategy_period["time"].map(df_history_future.set_index("time")["aidc_load"])
        df_strategy_period_soc = calc_soc(df_strategy_period, self.device_info)
        df_strategy_period["soc"] = df_strategy_period["time"].map(df_strategy_period_soc.set_index("time")["value"])
        dates_list = find_adjacent_date(self.df_date_set)
        # 使用相邻历史日的 SOC 轨迹作为策略期 SOC 对照样本。
        df_history_temp_mask = (
            (df_history["time"] >= pd.to_datetime(f"{dates_list[0][0]} 22:00:00"))
            & (df_history["time"] < pd.to_datetime(f"{dates_list[0][1]} 22:00:00"))
        )
        df_history_soc_1day = df_history.loc[df_history_temp_mask, ["time", "soc"]]
        df_history_soc_1day["time"] = pd.to_datetime(self.period_map["strategy_df"]["time"].values)
        df_strategy_period["soc_history"] = df_strategy_period["time"].map(df_history_soc_1day.set_index("time")["soc"])
        return df_strategy_period

    def _build_response_period_frame(self, df_strategy_period):
        """
        构造响应时段主表。
        """
        df_response_period = self.period_map["response_df"].copy()
        df_response_period["ele_price"] = df_response_period["time"].map(df_strategy_period.set_index("time")["ele_price"])
        df_response_period["ele_type"] = df_response_period["time"].map(df_strategy_period.set_index("time")["ele_type"])
        df_response_period["demand_load"] = df_response_period["time"].map(df_strategy_period.set_index("time")["demand_load"])
        df_response_period["strategy_load"] = df_response_period["time"].map(df_strategy_period.set_index("time")["strategy_load"])
        df_response_period["aidc_load"] = df_response_period["time"].map(df_strategy_period.set_index("time")["aidc_load"])
        df_response_period["soc"] = df_response_period["time"].map(df_strategy_period.set_index("time")["soc"])
        df_response_period["soc_history"] = df_response_period["time"].map(df_strategy_period.set_index("time")["soc_history"])
        return df_response_period

    def build(self):
        """
        输出历史未来表、策略期主表和响应时段主表。
        """
        df_history = self._build_history_frame()
        df_future = self._build_future_frame()
        df_history_future = pd.concat([df_history, df_future], axis=0)
        # 某些跨夜场景需要用总负荷减策略负荷重算 AIDC 负荷。
        if self.recompute_missing_aidc:
            df_history_future["aidc_load"] = df_history_future.apply(
                lambda x: x["demand_load"] - x["strategy_load"]
                if (x["aidc_load"] is np.nan or np.isnan(x["aidc_load"]))
                else x["aidc_load"],
                axis=1,
            )
        df_strategy_period = self._build_strategy_period_frame(df_history, df_history_future)
        df_response_period = self._build_response_period_frame(df_strategy_period)

        if self.verbose:
            with pd.option_context("display.max_columns", None):
                logger.info(f"debug::df_history.head(): \n{df_history.head()}")
                logger.info(f"debug::df_history.tail(): \n{df_history.tail()}")
                logger.info(f"debug::df_future.head(): \n{df_future.head()}")
                logger.info(f"debug::df_future.tail(): \n{df_future.tail()}")
                logger.info(f"debug::df_history_future.head(): \n{df_history_future.head()}")
                logger.info(f"debug::df_history_future.tail(): \n{df_history_future.tail()}")
                logger.info(f"debug::df_strategy_period.head(): \n{df_strategy_period.head()}")
                logger.info(f"debug::df_strategy_period.tail(): \n{df_strategy_period.tail()}")
        with pd.option_context("display.max_rows", None, "display.max_columns", None):
            logger.info(f"debug::df_response_period: \n{df_response_period}")

        return df_history_future, df_strategy_period, df_response_period


def build_input_frames(
    *,
    df_price,
    df_aidc_load_history,
    df_demand_load_history,
    df_strategy_load_history,
    df_soc_history,
    df_demand_load_predict,
    df_strategy_load_predict,
    period_map: Dict,
    device_info: Dict,
    df_date_set,
    all_df_date_set,
    recompute_missing_aidc: bool,
    verbose: bool,
):
    """
    统一构造历史未来表、策略表和响应表，供入口层直接调用。
    """
    return InputFrameBuilder(
        df_price=df_price,
        df_aidc_load_history=df_aidc_load_history,
        df_demand_load_history=df_demand_load_history,
        df_strategy_load_history=df_strategy_load_history,
        df_soc_history=df_soc_history,
        df_demand_load_predict=df_demand_load_predict,
        df_strategy_load_predict=df_strategy_load_predict,
        period_map=period_map,
        device_info=device_info,
        df_date_set=df_date_set,
        all_df_date_set=all_df_date_set,
        recompute_missing_aidc=recompute_missing_aidc,
        verbose=verbose,
    ).build()


def preprocessing_input_data(
    *,
    input_data: Dict,
    period_map: Dict,
    device_info: Dict,
    current_date,
    response_date,
    fillna_history: bool,
    history_cutoff_hour: int,
    recompute_missing_aidc: bool,
    verbose: bool,
):
    """
    统一完成输入数据装配，输出历史未来表、策略表和响应表。
    """
    df_price = load_price_frame(input_data, period_map)

    logger.info(f"{'-' * 40}")
    logger.info("debug::process df_demand_load_history...")
    logger.info("debug::process df_demand_load_predict...")
    df_demand_load_history, df_demand_load_predict = load_combined_history_future(
        input_data["demand_load_history"],
        input_data["demand_load_predict"],
        period_map["current_time"],
        period_map["history_df"]["time"],
        period_map["future_df"]["time"],
    )

    logger.info(f"{'-' * 40}")
    logger.info("debug::process df_strategy_load_history...")
    logger.info("debug::process df_strategy_load_predict...")
    df_strategy_load_history, df_strategy_load_predict = load_combined_history_future(
        input_data["strategy_load_history"],
        input_data["strategy_load_predict"],
        period_map["current_time"],
        period_map["history_df"]["time"],
        period_map["future_df"]["time"],
    )

    logger.info(f"{'-' * 40}")
    logger.info("debug::process df_aidc_load_history...")
    df_aidc_load_history = process_frame_data(
        input_data["aidc_load_history"],
        period_map["history_df"]["time"],
        fillna=fillna_history,
    )

    logger.info(f"{'-' * 40}")
    logger.info("debug::process df_soc_history...")
    df_soc_history = process_frame_data(
        input_data["soc_history"],
        period_map["history_df"]["time"],
        fillna=fillna_history,
    )

    df_date_set = set(pd.to_datetime(date).date() for date in input_data["df_date"]["date"].values)
    df_date_set.discard(current_date)
    logger.info(f"debug::df_date_set: \n{df_date_set}")
    all_df_date_set = df_date_set | {current_date, response_date}
    logger.info(f"debug::all_df_date_set:\n{all_df_date_set}")

    filtered_history_frames = filter_history_frames_by_dates(
        {
            "aidc": df_aidc_load_history,
            "demand": df_demand_load_history,
            "strategy": df_strategy_load_history,
            "soc": df_soc_history,
        },
        df_date_set=df_date_set,
        current_date=current_date,
        response_date=response_date,
        cutoff_hour=history_cutoff_hour,
    )

    return build_input_frames(
        df_price=df_price,
        df_aidc_load_history=filtered_history_frames["aidc"],
        df_demand_load_history=filtered_history_frames["demand"],
        df_strategy_load_history=filtered_history_frames["strategy"],
        df_soc_history=filtered_history_frames["soc"],
        df_demand_load_predict=df_demand_load_predict,
        df_strategy_load_predict=df_strategy_load_predict,
        period_map=period_map,
        device_info=device_info,
        df_date_set=df_date_set,
        all_df_date_set=all_df_date_set,
        recompute_missing_aidc=recompute_missing_aidc,
        verbose=verbose,
    )
