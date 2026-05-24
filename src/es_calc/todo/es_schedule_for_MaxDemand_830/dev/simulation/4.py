import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import time

import pandas as pd
from EssSimulation import EssSimulationModel


# ##############################
# experiment params
# ##############################
# experiment params
exp_name = "estimate830"
# Energy Storage params
es_info = {
    "transform_capacity": 63000,
    "invertband": 0,
    "soc_redundant_ratio": 0,
    "usable_depth": 0.97,
    "charge_loss": 0.92,
    "discharge_loss": 0.95,
    "es_charge_max": 9000,
    "es_charge_min": -9000,
    "es_capacity_max": 18000,
    "es_capacity_min": 0,
}
# ##############################
# experiment
# ##############################
for month_num in range(9, 10):
    for route in ["B"]:  # "B"
        print(f"{'='*40}\nmonth_num-route: {month_num}-{route} start...\n{'='*40}")
        # ------------------------------
        # params 
        # ------------------------------
        node_name = f"route_{route}_{month_num:02d}"
        print(f"node_name: {node_name}")
        
        # ------------------------------
        # data
        # ------------------------------
        # demand load
        demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/demand_load.csv")
        demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
        demand_load_df.set_index('time', inplace=True)
        print(f"\ndemand_load_df.head(): \n{demand_load_df.head()} \ndemand_load_df.shape: {demand_load_df.shape}")
        # ele price
        ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/ele_price.csv")
        ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])
        ele_price_df.set_index('time', inplace=True)
        print(f"\nele_price_df.head(): \n{ele_price_df.head()} \nele_price_df.shape: {ele_price_df.shape}")
        # ------------------------------
        # 不同需量突破比例的收益测算
        # ------------------------------
        ratio_result_list = []
        for ratio in range(10, 240, 10):
            print(f"{'='*30}\nmonth_num-route-ratio: {month_num}-{route}-{ratio}\n{'='*30}")
            
            # strategy
            # --------------
            strategy_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{ratio}.csv")
            strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
            strategy_df['time'] = pd.to_datetime(strategy_df['time'])
            strategy_df.set_index('time', inplace=True)
            print(f"\nstrategy_df.head(): \n{strategy_df.head()} \nstrategy_df.shape: {strategy_df.shape}")
            
            # simulation
            # --------------
            simulation_model = EssSimulationModel(es_info)
            es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(demand_load_df, strategy_df, 0)
            origin_balance, opt_balance = simulation_model.revenue_calculation(demand_load_df, es_charge_df, ele_price_df, 38.4)
            ratio_result_list.append((ratio, origin_balance - opt_balance))
            print(f"突破比例: {ratio}%, 收益为: {origin_balance - opt_balance}")
        # max ratio
        max_ratio_tuple = max(ratio_result_list, key=lambda x: x[1])
        max_ratio = max_ratio_tuple[0]
        print(f"max_ratio: {max_ratio}")
        # ------------------------------
        # 测算
        # ------------------------------
        # demand load
        demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/demand_load.csv")
        demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
        demand_load_df.set_index('time', inplace=True)
        # print(f"demand_load_df.head(): \n{demand_load_df.head()} \ndemand_load_df.shape: {demand_load_df.shape}")

        # strategy
        strategy_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{max_ratio}.csv")
        strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
        strategy_df['time'] = pd.to_datetime(strategy_df['time'])
        strategy_df.set_index('time', inplace=True)
        # print(f"strategy_df.head(): \n{strategy_df.head()} \nstrategy_df.shape: {strategy_df.shape}")

        # ele price
        ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/ele_price.csv")
        ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])
        ele_price_df.set_index('time', inplace=True)
        # print(f"ele_price_df.head(): \n{ele_price_df.head()} \nele_price_df.shape: {ele_price_df.shape}")

        # simulation
        simulation_model = EssSimulationModel(es_info)
        es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(demand_load_df, strategy_df, 0)
        origin_balance, opt_balance = simulation_model.revenue_calculation(demand_load_df, es_charge_df, ele_price_df, 38.4)
        print("测算方式一  收益：", (origin_balance - opt_balance), "收益占比：", (origin_balance - opt_balance) / origin_balance)

        ori_max_demand = demand_load_df["value"].max()
        opt_max_demand = total_load_df["total_load"].max()
        print("调度后最大需量：", opt_max_demand, "原始最大需量：", ori_max_demand, "需量抬升成本", (opt_max_demand - ori_max_demand) * 38.4)



# 测试代码 main 函数
def main():
    pass

if __name__ == "__main__":
    main()
