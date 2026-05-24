# python libraries
import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)

import time
import copy
from datetime import datetime, timedelta
from typing import List, Tuple, Dict

import pandas as pd

from model import BaseModelMainClass
from model.model_packages.ProfitSimulation_WithMaxDemand_ByDay.models.optimization.EsArbitraryRangeScheduler_withMaxDemand_optim import (
    EsArbitraryRangeScheduler_withMaxDemand
)
from model.model_packages.ProfitSimulation_WithMaxDemand_ByDay.models.simulation.EssSimulation_withoutMaxDemand import (
    EssSimulationModel
)
# from model.model_packages.ProfitSimulation_WithMaxDemand_ByDay.models.simulation.EssSimulation import (
#     EssSimulationModel
# )
from utils.log_util import logger


def preprocess_data(raw_df: pd.DataFrame, 
                    time_col: str="time", 
                    new_time_col: str="time",
                    set_index: bool=False, 
                    start_time: datetime=None, 
                    end_time: datetime=None, 
                    rename: bool=False,
                    fillna: bool=False):
    """
    数据处理
    """
    # dict to DataFrame
    if isinstance(raw_df, pd.DataFrame):
        df = copy.deepcopy(raw_df)
    else:
        raw_df: dict
        df = pd.DataFrame(raw_df)
    # TODO 构造时间戳完整的历史数据
    # df_processed = pd.DataFrame({"time": period_array})
    # 转换时间戳类型
    df[new_time_col] = pd.to_datetime(df[time_col])
    # 去除重复时间戳
    df.drop_duplicates(subset=new_time_col, keep="last", inplace=True, ignore_index=True)
    for col in df.columns:
        # 将数据转换为字符串类型
        if col not in ["time", "type"]:
            df[col] = df[col].apply(lambda x: float(x))
        # 缺失值检测
        if df[col].isnull().any():
            logger.info(f"{col} 缺失值检测: {df[col].isna().sum()}")
        # 缺失值填充
        if fillna:
            df[col] = df[col].ffill()
            df[col] = df[col].bfill()
        # 缺失值检测
        if df[col].isnull().any():
            logger.info(f"{col} 缺失值再检测: {df[col].isna().sum()}")
    # 索引设置
    if set_index:
        df.set_index(new_time_col, inplace=True)
        # data filter
        if start_time is not None and end_time is not None:
            df = df[(df.index >= start_time) & (df.index < end_time)]
    # rename
    if rename:
        df.rename(columns={"power_opt": "value"}, inplace=True)

    return df

def generate_month_ranges(start_time: datetime, end_time: datetime) -> List[Tuple[datetime, datetime]]:
    """
    生成每个月的开始、结束时间戳

    Args:
        start_time (datetime):  数据开始时间
        end_time (datetime): _description_

    Returns:
        _type_: _description_
    """
    if start_time >= end_time:
        return []
    
    result = []
    # 将当前时间定位到 start_time 所在月的第一天 00:00:00
    current = start_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current < end_time:
        # 计算下一个月的第一天，即当前月的结束时间点 (代表 "24:00:00")
        if current.month == 12:
            next_month_start = current.replace(
                year=current.year + 1, month=1, day=1, 
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            next_month_start = current.replace(
                month=current.month + 1, day=1,
                hour=0, minute=0, second=0, microsecond=0
            )
        # 将 (本月开始, 下月开始) 添加到结果列表
        # 这里的 next_month_start 逻辑上代表 "本月最后一天的24:00:00"
        result.append((current, next_month_start))
        # 当前时间跳到下一个月
        current = next_month_start
        # 如果下一个月已经大于等于 end_time，则停止循环
        if current >= end_time:
            break
    
    return result

def get_monthly_max_load(df: pd.DataFrame) -> Tuple[List[float],List[float]]:
    """
    从一个以时间索引的 DataFrame 中，提取每个月 'load' 列的最大值。

    参数:
        df (pd.DataFrame): 输入的 DataFrame，其 index 必须是时间对象 (DatetimeIndex)。
    返回:
        List[float]: 一个包含每个月 'load' 列最大值的列表，按时间顺序排列。
    异常:
        KeyError: 如果 DataFrame 中不存在 'load' 列。
        TypeError: 如果 DataFrame 的 index 不是 DatetimeIndex。
    """
    # 检查 'load' 列是否存在
    if 'total_load' not in df.columns:
        raise KeyError("DataFrame must have a 'load' column.")

    # 检查 index 是否为 DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame index must be a DatetimeIndex.")

    # 使用 resample 方法按月分组，并获取每个月 'load' 列的最大值, 'M' 表示按月的末尾进行分组
    monthly_total_load_max = df['total_load'].resample('M').max()
    monthly_demand_load_max = df['demand_load'].resample('M').max()
    monthly_diff = monthly_total_load_max - monthly_demand_load_max

    # 将结果转换为列表并返回
    return (monthly_total_load_max.tolist(), monthly_demand_load_max.tolist())

class ModelMainClass(BaseModelMainClass):

    def __init__(self, project, model, node, args: Dict) -> None:
        self.project = project
        self.model = model
        self.node = node
        self.args = args

    def optimization(self, 
                     demand_load_df: pd.DataFrame, 
                     ele_price_df: pd.DataFrame, 
                     max_demand_price: float, 
                     current_soc_list: List[float],
                     devices_info: List,
                     start_time: datetime, 
                     end_time: datetime):
        """
        调度策略
        """
        # 数据频率(分钟)
        freq_minutes = (demand_load_df["time"][1] - demand_load_df["time"][0]).total_seconds() / 60
        logger.info(f"freq_minutes: {freq_minutes}")
        # 生成每个月的开始、结束时间戳
        validation_day_list = generate_month_ranges(start_time, end_time)
        logger.info(f"validation_day_list: \n{validation_day_list}")
        # 遍历每个月数据进行计算
        days_strategy_list = []
        for time_pair in validation_day_list:
            vs_time, ve_time = time_pair[0], time_pair[1]
            logger.info(f"vs_time: {vs_time}, ve_time: {ve_time}")
            # 每个月 demand_load 数据
            mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
            step_demand_load_df = demand_load_df.loc[mask]
            # 每个月 ele_price 数据
            mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
            step_ele_price_df = ele_price_df.loc[mask]
            # step_ele_price_df = flat_valley_price_diff(step_ele_price_df)
            # 调度模型
            scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(
                schedule_time_range = step_demand_load_df["time"].to_list(), 
                demand_load = step_demand_load_df["value"].to_list(), 
                ele_prices = step_ele_price_df["value"].to_list(), 
                ele_types = step_ele_price_df["type"].to_list(),
                devices_info = devices_info,
                current_soc_list = current_soc_list,
                max_demand_price = max_demand_price,
                freq_minutes = freq_minutes,
            )
            opt_list = scheduler_model.run()
            days_strategy_list.append(opt_list[0])
        result_df = pd.concat(days_strategy_list)
        result_df["time"] = result_df.index
        result_df = result_df[["time", "value"]]
        result_df.reset_index(drop=True, inplace=True)
        
        return result_df

    def simulation(self, 
                   demand_load_df: pd.DataFrame, 
                   ele_price_df: pd.DataFrame, 
                   strategy_df: pd.DataFrame, 
                   max_demand_price: float, 
                   device_info: Dict):
        # ------------------------------
        # model
        # ------------------------------
        simulation_model = EssSimulationModel(device_info)
        es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(
            demand_load = demand_load_df, 
            es_strategy = strategy_df, 
            last_soc = 0,
        )
        logger.info(f"es_charge_df: \n{es_charge_df}")
        logger.info(f"es_soc_df: \n{es_soc_df}")
        logger.info(f"total_load_df: \n{total_load_df}")
        origin_balance, opt_balance = simulation_model.revenue_calculation(
            demand_load = demand_load_df, 
            es_load = es_charge_df, 
            ele_price = ele_price_df, 
            max_demand_price = max_demand_price,
        )
        # ------------------------------
        # cost
        # ------------------------------
        # 用电总量
        total_energy = demand_load_df["value"].sum()
        
        # 需量电费成本
        opt_max_demand_load_list, ori_max_demand_load_list = get_monthly_max_load(total_load_df)
        ori_max_demand_cost = max_demand_price * sum(ori_max_demand_load_list)
        opt_max_demand_cost = max_demand_price * sum(opt_max_demand_load_list)
        # 需量电费抬升成本
        max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost

        # 无储能、有储能的电费成本
        ori_cost = origin_balance + ori_max_demand_cost
        opt_cost = opt_balance + opt_max_demand_cost

        # 储能带来的收益（考虑了需量抬升带来的成本上升）
        # revenue = origin_balance - opt_balance - max_demand_rise_cost
        revenue = ori_cost - opt_cost

        # 充放电电量、成本（收益）
        es_charge_df["price"] = ele_price_df["value"]
        es_charge_df["balance"] = es_charge_df["value"] * es_charge_df["price"]
        charge_energy = - es_charge_df.loc[es_charge_df['value'] < 0, 'value'].sum()
        discharge_energy = es_charge_df.loc[es_charge_df['value'] > 0, 'value'].sum()
        charge_balance = - es_charge_df.loc[es_charge_df['balance'] < 0, 'balance'].sum()
        discharge_balance = es_charge_df.loc[es_charge_df['balance'] > 0, 'balance'].sum()
        
        return (
            revenue, 
            max_demand_rise_cost, 
            total_energy, 
            ori_cost, opt_cost, 
            charge_energy, discharge_energy, 
            charge_balance, discharge_balance,
        )
    
    def __calc_month_statistics(self, 
                                step_strategy_df: pd.DataFrame, 
                                target_col: str, 
                                vs_time: datetime, 
                                transfer_data: float):
        """
        每个月不同时间段充电时间、放电时间统计
        """
        df_pivot = step_strategy_df.pivot_table(
            index="time_col", 
            columns="date_col", 
            values=target_col,
            aggfunc="first",
        )
        df_pivot = df_pivot.reset_index()
        df_pivot["year"] = vs_time.year
        df_pivot["month"] = vs_time.month
        
        df = df_pivot[[col for col in df_pivot.columns if col != "time_col"]].groupby(["year", "month"]).sum() * transfer_data
        df["time_len"] = df[[col for col in df.columns if col not in ["year", "month"]]].mean(axis=1)
        df = df.reset_index()
        df = df[["year", "month", "time_len"]]

        return df

    def calc_statistics(self, strategy_df: pd.DataFrame, start_time: datetime, end_time: datetime, device_info: Dict):
        # 可用电量
        power_total = float(device_info["es_capacity_max"]) * float(device_info["usable_depth"])
        logger.info(f"power_total: {power_total}")
        # 数据频率(小时)
        freq_hours = (strategy_df.index[1] - strategy_df.index[0]).total_seconds() / (60*60)
        logger.info(f"freq_hours: {freq_hours}")
        # 数据预处理
        strategy_df = strategy_df.copy()
        strategy_df["date_col"] = strategy_df.index.date
        strategy_df["time_col"] = strategy_df.index.time
        strategy_df["charge_count"] = strategy_df["value"].apply(lambda x: 1 if x < 0.0 else 0)
        strategy_df["discharge_count"] = strategy_df["value"].apply(lambda x: 1 if x > 0.0 else 0)
        strategy_df["charge_load"] = strategy_df["value"].apply(lambda x: x if x < 0.0 else 0)
        strategy_df["discharge_load"] = strategy_df["value"].apply(lambda x: x if x > 0.0 else 0)
        strategy_df.reset_index(inplace=True)
        logger.info(f"strategy_df: \n{strategy_df}")
        # 分月统计
        strategy_load_df = pd.DataFrame()
        actual_charge_time_len = pd.DataFrame()
        actual_discharge_time_len = pd.DataFrame()
        equivalent_charge_time_len = pd.DataFrame()
        equivalent_discharge_time_len = pd.DataFrame()
        # 生成每个月的开始、结束时间戳
        validation_day_list = generate_month_ranges(start_time, end_time)
        logger.info(f"validation_day_list: \n{validation_day_list}")
        for time_pair in validation_day_list:
            # 每个月时间范围
            vs_time, ve_time = time_pair[0], time_pair[1]
            logger.info(f"vs_time: {vs_time}, ve_time: {ve_time}")
            # 每个月 demand_load 数据
            mask = (strategy_df['time'] >= vs_time) & (strategy_df['time'] < ve_time)
            step_strategy_df = strategy_df.loc[mask]
            # ------------------------------
            # 每个月不同时间段充电时间统计
            # ------------------------------
            df_charge = self.__calc_month_statistics(
                step_strategy_df, 
                target_col="charge_count", 
                vs_time=vs_time, 
                transfer_data=freq_hours
            )
            # 结果收集
            actual_charge_time_len = pd.concat([actual_charge_time_len, df_charge], axis=0)
            # ------------------------------
            # 每个月不同时间段放电时间统计
            # ------------------------------
            df_discharge = self.__calc_month_statistics(
                step_strategy_df, 
                target_col="discharge_count", 
                vs_time=vs_time, 
                transfer_data=freq_hours
            )
            # 结果收集
            actual_discharge_time_len = pd.concat([actual_discharge_time_len, df_discharge], axis=0)
            # ------------------------------
            # 每个月不同时间段充电等效时间统计
            # ------------------------------
            df_equivalent_charge = self.__calc_month_statistics(
                step_strategy_df, 
                target_col="charge_load", 
                vs_time=vs_time, 
                transfer_data=-1/power_total
            )
            # 结果收集
            equivalent_charge_time_len = pd.concat([equivalent_charge_time_len, df_equivalent_charge], axis=0)
            # ------------------------------
            # 每个月不同时间段放电等效时间统计
            # ------------------------------
            df_equivalent_discharge = self.__calc_month_statistics(
                step_strategy_df, 
                target_col="discharge_load", 
                vs_time=vs_time, 
                transfer_data=1/power_total
            )
            # 结果收集
            equivalent_discharge_time_len = pd.concat([equivalent_discharge_time_len, df_equivalent_discharge], axis=0)
            # ------------------------------
            # 每个月不同时间段充放电功率统计
            # ------------------------------
            df_load_pivot = step_strategy_df.pivot_table(
                index="time_col", 
                columns="date_col", 
                values="value",
                aggfunc="first",
            )
            df_load_pivot["strategy_load_avg"] = df_load_pivot.mean(axis=1)
            df_load_pivot = df_load_pivot.reset_index()
            df_load_pivot["year"] = vs_time.year
            df_load_pivot["month"] = vs_time.month
            df_load_pivot["hour"] = df_load_pivot["time_col"].apply(lambda x: f"{x.hour:02d}")
            df_load_pivot = df_load_pivot[["year", "month", "hour", "time_col", "strategy_load_avg"]]
            # 结果收集
            strategy_load_df = pd.concat([strategy_load_df, df_load_pivot], axis=0)
        return {
            "actual_charge_time_len": actual_charge_time_len,
            "actual_discharge_time_len": actual_discharge_time_len,
            "equivalent_charge_time_len": equivalent_charge_time_len,
            "equivalent_discharge_time_len": equivalent_discharge_time_len,
            "strategy_load_df": strategy_load_df,
        }
    
    def run(self, model_cfgs: Dict, input_data: Dict):
        # ------------------------------
        # experiment params
        # ------------------------------
        logger.info(f"{'=' * 100}")
        logger.info(f"输入参数预处理...")
        logger.info(f"{'=' * 100}")
        es_scale = float(model_cfgs["es_scale"])
        logger.info(f"es_scale: {es_scale} kWh")
        max_demand_price = float(model_cfgs["max_demand_price"])
        logger.info(f"max_demand_price: {max_demand_price} 元/kWh")
        current_soc_list = model_cfgs["current_soc_list"]
        logger.info(f"current_soc_list: {current_soc_list}")
        devices_info = model_cfgs["devices_info"]
        devices_info[0]["es_charge_max"] = es_scale
        devices_info[0]["es_charge_min"] = -es_scale
        devices_info[0]["es_capacity_max"] = es_scale * 2
        logger.info(f"device_info: \n{devices_info}")
        # ------------------------------
        # input data
        # ------------------------------
        logger.info(f"{'=' * 100}")
        logger.info(f"输入数据预处理...")
        logger.info(f"{'=' * 100}")
        demand_load_df = preprocess_data(input_data["demand_load"], time_col="time", new_time_col="time")
        logger.info(f"demand_load: \n{demand_load_df}")
        ele_price_df = preprocess_data(input_data["ele_price"], time_col="time", new_time_col="time")
        logger.info(f"ele_price: \n{ele_price_df}")
        # ------------------------------
        # data time range
        # ------------------------------
        logger.info(f"{'=' * 100}")
        logger.info(f"输入数据时间范围:")
        logger.info(f"{'=' * 100}")
        start_time = demand_load_df["time"].min()
        end_time = demand_load_df["time"].max() + timedelta(hours=1)
        logger.info(f"start_time: {start_time}, end_time: {end_time}")
        # ------------------------------
        # optimizer
        # ------------------------------
        logger.info(f"{'=' * 100}")
        logger.info(f"储能调度策略模型运行...")
        logger.info(f"{'=' * 100}")
        strategy_df = self.optimization(
            demand_load_df = demand_load_df, 
            ele_price_df = ele_price_df, 
            max_demand_price = max_demand_price, 
            current_soc_list = current_soc_list,
            devices_info = devices_info,
            start_time = start_time, 
            end_time = end_time,
        )
        logger.info(f"strategy_df: \n{strategy_df}")
        # ------------------------------
        # simulation
        # ------------------------------
        logger.info(f"{'=' * 100}")
        logger.info(f"储能收益仿真模型运行...")
        logger.info(f"{'=' * 100}")
        logger.info(f"{'-' * 50}")
        logger.info(f"储能收益仿真模型数据预处理...")
        logger.info(f"{'-' * 50}")
        # data
        demand_load_df = preprocess_data(
            demand_load_df, time_col="time", new_time_col="time", 
            set_index=True, start_time=start_time, end_time=end_time, 
        )
        ele_price_df = preprocess_data(
            ele_price_df, time_col="time", new_time_col="time", 
            set_index=True, start_time=start_time, end_time=end_time, 
        )
        strategy_df = preprocess_data(
            strategy_df, time_col="time", new_time_col="time", 
            set_index=True, start_time=start_time, end_time=end_time, 
        )
        logger.info(f"{'-' * 50}")
        logger.info(f"储能收益仿真模型收益测算...")
        logger.info(f"{'-' * 50}")
        # simulation
        (
            revenue, 
            max_demand_rise_cost, 
            total_energy, 
            ori_cost, opt_cost, 
            charge_energy, discharge_energy, 
            charge_balance, discharge_balance,
        ) = self.simulation(
            demand_load_df = demand_load_df, 
            ele_price_df = ele_price_df, 
            strategy_df = strategy_df, 
            max_demand_price = max_demand_price, 
            device_info = devices_info[0],
        )
        # ------------------------------
        # 指标计算
        # ------------------------------
        logger.info(f"{'-' * 50}")
        logger.info(f"可视化指标计算...")
        logger.info(f"{'-' * 50}")
        statistics_dict = self.calc_statistics(strategy_df, start_time, end_time, devices_info[0])
        # ------------------------------
        # result
        # ------------------------------
        logger.info(f"{'-' * 50}")
        logger.info(f"结果输出...")
        logger.info(f"{'-' * 50}")
        strategy_df = strategy_df.reset_index()
        output = {
            "output_dict": {
                "strategy_df": strategy_df,
                "revenue": revenue,
                "max_demand_rise_cost": max_demand_rise_cost,
                "total_energy": total_energy,
                "ori_cost": ori_cost,
                "opt_cost": opt_cost,
                "charge_energy": charge_energy,
                "discharge_energy": discharge_energy,
                "charge_balance": charge_balance,
                "discharge_balance": discharge_balance,
            }
        }
        output["output_dict"].update(statistics_dict)
        return output




# 测试代码 main 函数
def main():
    # ##############################
    # model_cfgs
    # ##############################
    model_cfgs = {
        "es_scale": 10000,
        "max_demand_price": 48.0,
        "current_soc_list": [0],
        "devices_info": [{
            "transform_capacity": 8883000,# 变压器容量
            "invertband": 0,              # 防逆流功率
            "soc_redundant_ratio": 0,     # 保电比例
            "usable_depth": 0.90,         # 可用深度
            "charge_loss": 0.92,          # 充电效率
            "discharge_loss": 0.95,       # 放电效率
            "es_charge_max": 8920,        # 最大功率(放电)
            "es_charge_min": -8920,       # 最大功率(充电)
            "es_capacity_max": 17888,     # 设计容量(最大)
            "es_capacity_min": 0,         # 设计容量(最小)
        }],
    }
    # ##############################
    # input data
    # ##############################
    input_data = {
        "demand_load": pd.read_csv(f"./data/estimate_zhangjiakou/route_A/demand_load.csv"),
        "ele_price": pd.read_csv(f"./data/estimate_zhangjiakou/route_A/ele_price.csv"),
    }
    # ##############################
    # model
    # ##############################
    s_time = time.time()

    # model
    model = ModelMainClass(project="test", model="test", node="test", args=None)
    res = model.run(model_cfgs=model_cfgs, input_data=input_data)
    # output
    output = res["output_dict"]
    logger.info(f"output: \n{output}")
    
    total_time = time.time() - s_time
    logger.info(f"total_time: {total_time}")

if __name__ == "__main__":
    main()
