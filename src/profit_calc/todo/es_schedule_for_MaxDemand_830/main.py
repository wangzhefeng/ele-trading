# import sys
# from pathlib import Path
# ROOT = str(Path.cwd())
# if ROOT not in sys.path:
#     sys.path.append(ROOT)
import copy
import time
from typing import Dict

import pandas as pd
import multiprocessing as mp            

# from model import BaseModelMainClass
# from api.profit_simulation.schemas.base import ProjectStatus
from model.model_packages.ProfitSimulation_WithMaxDemand.utils.time_process import (
    generate_hourly_datetime_pairs, 
    get_month_range,
)
from model.model_packages.ProfitSimulation_WithMaxDemand.models import (
    optimizer, 
    simulator,
)
# from utils.cache import cache
from utils.log_util import logger


def preprocess_data(raw_df: pd.DataFrame, column_name: str="time", new_column_name: str="time", 
                    set_index: bool=False, rename: bool=False):
    df = copy.deepcopy(raw_df)
    if rename:
        df.rename(columns={"power_opt": "value"}, inplace=True)
    # 转换时间戳类型
    df[new_column_name] = pd.to_datetime(df[column_name])
    # 去除重复时间戳
    df.drop_duplicates(subset=new_column_name, keep="last", inplace=True, ignore_index=True)
    if set_index:
        df.set_index(new_column_name, inplace=True)
    
    return df


def optimization(month_num, ratio, demand_load_df, ele_price_df, devices_info, max_demand_control_line):
    logger.info(f"每月策略调度模型::month_num-ratio:{month_num:02d}-{ratio} start...")
    # time periods
    # --------------
    validation_day_list = generate_hourly_datetime_pairs(2025, month_num, 22)
    # daily strategy
    # --------------
    days_strategy_list = []
    for time_pair in validation_day_list:
        logger.info(f"每月策略调度模型::month_num-ratio-time_pair:{month_num:02d}-{ratio}-{time_pair} start...")
        # calc max demand control line
        # --------------
        vs_time, ve_time = time_pair[0], time_pair[1]
        step_demand_load_df = demand_load_df.loc[(demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)]
        step_ele_price_df = ele_price_df.loc[(ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)]
        # max demand control line
        max_demand_control_line_i = max_demand_control_line * (1 + ratio / 1000)
        logger.info(f"每月策略调度模型::month_num-ratio-time_pair:{month_num:02d}-{ratio}-{time_pair}-ratio_break:{(ratio / 1000) * 100:.2f}% max_demand_control_line_i: {max_demand_control_line_i:.2f}")
        # scheduler
        # --------------
        scheduler_model = optimizer.EsArbitraryRangeScheduler_withMaxDemand(
            schedule_time_range=step_demand_load_df["time"].to_list(), 
            demand_load=step_demand_load_df["value"].to_list(), 
            ele_prices=step_ele_price_df["value"].to_list(), 
            ele_types=step_ele_price_df["type"].to_list(),
            devices_info=devices_info,
            current_soc_list=[0],
            max_demand_line=max_demand_control_line_i,
            is_slow_charge=True,
        )
        opt_list = scheduler_model.run()
        logger.info(f"每月策略调度模型::month_num-ratio-time_pair:{month_num:02d}-{ratio}-{time_pair} end...")
        # scheduler result
        # --------------
        days_strategy_list.append(opt_list[0])
    # result collect
    # --------------
    result_df = pd.concat(days_strategy_list)
    result_df.index.name = "time"
    result_df.reset_index(inplace=True, drop=False)
    # one month result process
    # --------------
    save_range_start, save_range_end = get_month_range(month_num, 2025)
    save_result_df = result_df.loc[(result_df['time'] >= save_range_start) & (result_df['time'] < save_range_end)]
    # --------------
    logger.info(f"每月策略调度模型::month_num-ratio:{month_num:02d}-{ratio} end...")
    
    return save_result_df


def _simulation_ratio(input_data, month_num, ratio, devices_info, demand_load_df, ele_price_df):
    # strategy input
    strategy_df = preprocess_data(input_data["simulation"]["strategy"][f"month_{month_num:02d}"][ratio], set_index=True, rename=True)
    logger.info(f"需量突破收益测算::month_num-ratio:{month_num:02d}-{ratio}-strategy_df: \n{strategy_df.head()} \nstrategy_df.shape: {strategy_df.shape}")
    # simulation
    simulation_model = simulator.EssSimulationModel(devices_info[0])
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(
        demand_load_df, strategy_df, last_soc=0
    )
    origin_balance, opt_balance = simulation_model.revenue_calculation(
        demand_load_df, es_charge_df, ele_price_df, max_demand_price=38.4
    )
    
    return strategy_df, total_load_df, origin_balance, opt_balance


def simulation(month_num, ratio, input_data, demand_load_df, ele_price_df, ratio_result_list, devices_info):
    logger.info(f"需量突破收益测算::month_num-ratio:{month_num:02d}-{ratio} start...")
    strategy_df, total_load_df, origin_balance, opt_balance = _simulation_ratio(
        input_data, month_num, ratio, devices_info, demand_load_df, ele_price_df
    )
    logger.info(f"需量突破收益测算::month_num-ratio:{month_num:02d}-{ratio}-突破比例:{(ratio / 1000) * 100}%, 收益为: {origin_balance - opt_balance}")
    ratio_result_list.append((ratio, origin_balance - opt_balance))
    logger.info(f"需量突破收益测算::month_num-ratio:{month_num:02d}-{ratio} end...")

    return ratio_result_list


class ModelMainClass:#(BaseModelMainClass):
    
    def __init__(self, project, model, node, args: Dict) -> None:
        self.project = project
        self.model = model
        self.node = node
        self.args = args
    
    def run(self, input_data: Dict, model_cfgs: Dict):
        # experiment params
        devices_info = model_cfgs["devices_info"]
        # output collector
        break_ratio_list = []
        strategy_df_all = []
        # experiment
        for month_num in [5]:
            # ############################################################
            # 输入数据
            # ############################################################
            logger.info(f"模型输入数据::month_num:{month_num:02d} start...")
            # optimization input data
            demand_load_df = preprocess_data(input_data["optimization"]["demand_load"][f"month_{month_num:02d}"])
            ele_price_df = preprocess_data(input_data["optimization"]["ele_price"][f"month_{month_num:02d}"])
            logger.info(f"模型输入数据::month_num:{month_num:02d}-demand_load_df: \n{demand_load_df.head()} \nmonth_num:{month_num:02d}-demand_load_df.shape: {demand_load_df.shape}")
            logger.info(f"模型输入数据::month_num:{month_num:02d}-ele_price_df: \n{ele_price_df.head()} \nmonth_num:{month_num:02d}-ele_price_df.shape: {ele_price_df.shape}")
            # simulation input data
            save_range_start, save_range_end = get_month_range(month_num, 2025)
            demand_load_df_simu = demand_load_df.loc[(demand_load_df['time'] >= save_range_start) & (demand_load_df['time'] < save_range_end)]
            demand_load_df_simu.set_index("time", inplace=True)
            ele_price_df_simu = ele_price_df.loc[(ele_price_df['time'] >= save_range_start) & (ele_price_df['time'] < save_range_end)]
            ele_price_df_simu.set_index("time", inplace=True)
            logger.info(f"模型输入数据::month_num:{month_num:02d}-demand_load_df_simu: \n{demand_load_df_simu.head()} \nmonth_num:{month_num:02d}-demand_load_df_simu.shape: {demand_load_df_simu.shape}")
            logger.info(f"模型输入数据::month_num:{month_num:02d}-ele_price_df_simu: \n{ele_price_df_simu.head()} \nmonth_num:{month_num:02d}-ele_price_df_simu.shape: {ele_price_df_simu.shape}")
            logger.info(f"模型输入数据::month_num:{month_num:02d} end...")
            # ############################################################
            # 每个月调度策略
            # ############################################################
            logger.info(f"每月策略调度模型::month_num:{month_num:02d} start...")
            # max demand control line
            max_demand_control_line = demand_load_df_simu["value"].max()
            # policy optimization
            mp_input_list_ratio = [
                (month_num, ratio, demand_load_df, ele_price_df, devices_info, max_demand_control_line) 
                for ratio in range(model_cfgs["ratio_min"], model_cfgs["ratio_max"], 10)
            ]
            with mp.Pool(processes=8) as pool:
                save_result_dfs = pool.starmap(optimization, mp_input_list_ratio)
            logger.info(f"每月策略调度模型::month_num:{month_num:02d} end...")
            # ############################################################
            # 需量突破比例的收益测算
            # ############################################################
            logger.info(f"需量突破收益测算::month_num:{month_num:02d} start...")
            # 不同需量突破比例的收益测算
            # ------------------------------
            # simulation
            ratio_result_list = []
            mp_input_list_ratio = [
                (month_num, ratio, input_data, demand_load_df_simu, ele_price_df_simu, ratio_result_list, devices_info)
                for ratio in range(model_cfgs["ratio_min"], model_cfgs["ratio_max"], 10)
            ]
            with mp.Pool(processes=8) as pool:
                ratio_result_list = pool.starmap(simulation, mp_input_list_ratio)
            ratio_result_list = [item[0] for item in ratio_result_list]
            logger.info(f"需量突破收益测算::month_num:{month_num:02d}-ratio_result_list: \n{ratio_result_list}")
            # max ratio
            max_ratio_tuple = max(ratio_result_list, key=lambda x: x[1])
            max_ratio = max_ratio_tuple[0]
            # 最优需量突破比例测算
            # ------------------------------
            # strategy
            strategy_df, total_load_df, origin_balance, opt_balance = _simulation_ratio(
                input_data, month_num, max_ratio, devices_info, demand_load_df_simu, ele_price_df_simu
            )
            # result
            ori_max_demand = demand_load_df_simu["value"].max()
            opt_max_demand = total_load_df["total_load"].max()
            logger.info(f"需量突破收益测算::month_num:{month_num:02d}-调度后最大需量：{opt_max_demand}, 原始最大需量：{ori_max_demand}, 需量抬升成本: {(opt_max_demand - ori_max_demand) * 38.4}")
            logger.info(f"需量突破收益测算::month_num:{month_num:02d}-max_ratio: {(max_ratio / 1000) * 100}%, max_ratio 收益：{(origin_balance - opt_balance)}, 收益占比: {(origin_balance - opt_balance) / origin_balance}")
            logger.info(f"需量突破收益测算::month_num:{month_num:02d} end...")
            # 需量突破比例的收益测算-results
            # ------------------------------
            break_ratio_list.append(max_ratio)
            strategy_df_all.append(strategy_df)
        # output
        output_dict = {}
        output_dict["ratio"] = break_ratio_list
        output_dict["policy"] = strategy_df_all
        return {"output_dict": output_dict}




# 测试代码 main 函数
def main():
    # ##############################
    # model_cfgs
    # ##############################
    model_cfgs = {
        "exp_name": "estimate830",
        "route": "A",
        "ratio_min": 130,                  # 需量突破比例
        "ratio_max": 150,                  # 需量突破比例
        "devices_info": [{
            "transform_capacity": 63000,  # 转换容量
            "invertband": 0,              # 逆变
            "soc_redundant_ratio": 0,     # SOC冗余率
            "usable_depth": 0.9,         # 可用深度
            "charge_loss": 0.92,          # 充电效率
            "discharge_loss": 0.95,       # 放电效率
            "es_charge_max": 8920,        # 最大功率(放电)
            "es_charge_min": -8920,       # 最大功率(充电)
            "es_capacity_max": 17888,     # 设计容量(最大)
            "es_capacity_min": 0,         # 设计容量(最小)
        }]
    }
    # charge_loss_list = [i["charge_loss"] for i in model_cfgs["devices_info"]]
    # print(charge_loss_list)
    # row = 1
    # c_l_in_vec = np.array(charge_loss_list).reshape((row, 1))
    # print(c_l_in_vec)
    # ##############################
    # input data
    # ##############################
    input_data = {
        "optimization": {
            "demand_load": {
                "month_05": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/demand_load.csv"),
                # "month_07": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_07/demand_load.csv"),
                # "month_08": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_08/demand_load.csv"),
                # "month_09": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_09/demand_load.csv"),
            },
            "ele_price": {
                "month_05": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/ele_price.csv"),
                # "month_07": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_07/ele_price.csv"),
                # "month_08": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_08/ele_price.csv"),
                # "month_09": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_09/ele_price.csv"),
            },
        },
        "simulation": {
            "strategy": {
                "month_05": {},
                # "month_07": {},
                # "month_08": {},
                # "month_09": {},
            },
        }
    }
    for month_num in [5]:
        for ratio in range(model_cfgs["ratio_min"], model_cfgs["ratio_max"], 10):
            df_strategy = pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_{month_num:02d}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{ratio}.csv")
            input_data["simulation"]["strategy"][f"month_{month_num:02d}"][ratio] = df_strategy
    # ##############################
    # experiment
    # ##############################
    # model
    s_time = time.time()
    model = ModelMainClass(project="test", model="test", node="test", args=None)
    res = model.run(input_data=input_data, model_cfgs=model_cfgs)
    # output
    output = res["output_dict"]
    logger.info(f"output: \n{output}")
    
    total_time = time.time() - s_time
    logger.info(f"total_time: {total_time}")

if __name__ == "__main__":
    main()
