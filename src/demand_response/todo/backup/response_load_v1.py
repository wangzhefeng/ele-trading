# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)

from datetime import datetime
from typing import Dict

import pandas as pd

from model.model_packages.Demand_Response.response_strategy import (
    strategy_adjust_model
)
from utils.log_util import logger


def get_response_power(response_period_df: pd.DataFrame, 
                       df_baseline: pd.DataFrame, 
                       df_response_period: pd.DataFrame, 
                       df_strategy: pd.DataFrame) -> pd.DataFrame:
    """
    需求响应时段可调负荷
    """
    strategy_response = response_period_df.copy()
    strategy_response["baseline_load"] = strategy_response["time"].map(df_baseline.set_index("time")["value"])
    strategy_response["demand_load_pred"] = strategy_response["time"].map(df_response_period.set_index("time")["demand_load_pred"])
    strategy_response["strategy_load"] = strategy_response["time"].map(df_strategy.set_index("time")["strategy_load"])
    logger.info(f"debug::strategy_response: \n{strategy_response}")
    strategy_response["response_load"] = strategy_response.apply(
        lambda x: (x["baseline_load"] - x["demand_load_pred"] + x["strategy_load"]), 
        axis=1
    )
    response_power = strategy_response[["time", "response_load"]]
    
    return response_power

def pre_declare_stage(response_date: datetime,
                      response_period_df: pd.DataFrame, 
                      df_baseline: pd.DataFrame,
                      df_response_period: pd.DataFrame,
                      df_strategy: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    申报阶段-申报前
    需求响应申报量模型(申报阶段-申报前)计算可调负荷

    Args:
        response_date (datetiem): 响应日期
        response_period_df (pd.DataFrame): 响应时段时间索引
        df_baseline (pd.DataFrame): 申报阶段预估基线负荷(15min，平均)
        df_response_period (pd.DataFrame): 响应时段负荷预测值
        df_strategy (pd.DataFrame): 计划策略

    Returns:
        _type_: 可调负荷
    """
    # 需求响应时段可调负荷
    period_mask = (
        (df_strategy["time"] >= pd.to_datetime(f"{response_date} 08:00:00")) & 
        (df_strategy["time"] <= pd.to_datetime(f"{response_date} 10:00:00"))
    )
    discharge_load = df_strategy.loc[period_mask, "strategy_load"].max()
    logger.info(f"debug::max discharge_load: {discharge_load} kW")
    strategy_response = response_period_df.copy()
    strategy_response["baseline_load"] = strategy_response["time"].map(df_baseline.set_index("time")["value"])
    strategy_response["demand_load_pred"] = strategy_response["time"].map(df_response_period.set_index("time")["demand_load_pred"])
    # strategy_response["strategy_load"] = strategy_response["time"].map(df_strategy.set_index("time")["strategy_load"])
    strategy_response["strategy_load_pred"] = discharge_load
    logger.info(f"debug::strategy_response: \n{strategy_response}")
    strategy_response["response_load"] = strategy_response.apply(
        lambda x: (x["baseline_load"] - x["demand_load_pred"] + x["strategy_load_pred"]) * 0.8, 
        axis=1
    )
    response_power = strategy_response[["time", "response_load"]]
    # 需求响应容量计算
    response_capacity = response_power["response_load"].sum() * (15/60)
    logger.info(f"debug::response_capacity: {response_capacity} kWh")
    # 输出结果
    output = {
        "response_power": response_power,
    }
    
    return output

def declare_cleaning_response_stage(baseline_coef_period: Dict,
                                    climbing_period: Dict,
                                    response_period: Dict,
                                    response_period_df: pd.DataFrame, 
                                    response_before_1h_period: Dict,
                                    response_after_1h_period: Dict,
                                    df_strategy_period: pd.DataFrame,
                                    df_baseline: pd.DataFrame,
                                    df_response_period: pd.DataFrame,
                                    df_response_load: float,
                                    clearing_price: float,
                                    max_discharge_load: float,
                                    max_charge_load: float,
                                    battery_capacity: float,
                                    freq: str):
    """
    需求响应申报量模型(申报阶段-申报后)
    """
    # 需求响应容量计算
    response_capacity = df_response_load["value"].sum() * (15/60) * 1.3
    logger.info(f"debug::response_capacity: {response_capacity} kWh")
    logger.info(f"debug::clearing_price: {clearing_price} 元/kWh")
    logger.info(f"debug::battery_capacity: {battery_capacity} kWh")
    # 需求响应策略
    df_strategy = strategy_adjust_model(
        df_strategy_period,
        baseline_coef_period,
        climbing_period,
        response_period,
        response_before_1h_period,
        response_after_1h_period,
        response_capacity,
        battery_capacity,
        clearing_price,
        max_discharge_load,
        max_charge_load,
        freq,
    )
    # 需求响应申报容量
    response_power = get_response_power(
        response_period_df, 
        df_baseline, 
        df_response_period, 
        df_strategy,
    )
    # 输出结果
    output = {
        "response_power": response_power,
        "response_strategy": df_strategy,
    }
    
    return output




# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
