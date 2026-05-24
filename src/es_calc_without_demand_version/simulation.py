import sys
from pathlib import Path

ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
from datetime import datetime
import multiprocessing as mp

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from models.simulation.EssSimulation_withoutMaxDemand import EssSimulationModel

plt.rcParams["font.sans-serif"] = ["SimHei"]  # 用来正常显示中文标签
plt.rcParams["axes.unicode_minus"] = False  # 用来显示负号


def get_monthly_max_load(df: pd.DataFrame):
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
    if "total_load" not in df.columns:
        raise KeyError("DataFrame must have a 'load' column.")

    # 检查 index 是否为 DatetimeIndex
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame index must be a DatetimeIndex.")

    # 使用 resample 方法按月分组，并获取每个月 'load' 列的最大值
    # 'M' 表示按月的末尾进行分组
    monthly_total_load_max = df["total_load"].resample("M").max()
    monthly_demand_load_max = df["demand_load"].resample("M").max()
    monthly_diff = monthly_total_load_max - monthly_demand_load_max

    # 将结果转换为列表并返回
    return monthly_total_load_max.tolist(), monthly_demand_load_max.tolist()


def one_process(
    es_scale,
    route_num_str,
    max_demand_price,
    save_range_start,
    save_range_end,
    exp_name,
    strategy_dir,
):
    # params
    es_info = {
        "transform_capacity": 8883000,
        "invertband": 0,
        "soc_redundant_ratio": 0,
        "usable_depth": 0.90,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": es_scale,
        "es_charge_min": -es_scale,
        "es_capacity_max": es_scale * 2,
        "es_capacity_min": 0,
    }
    # data
    print(f"save_range_start~save_range_end: {save_range_start}~{save_range_end}")
    node_name = f"route_{route_num_str}"
    demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load.csv")
    demand_load_df["time"] = pd.to_datetime(demand_load_df["time"])
    demand_load_df.set_index("time", inplace=True)
    demand_load_df = demand_load_df[
        (demand_load_df.index >= save_range_start)
        & (demand_load_df.index < save_range_end)
    ]
    demand_load_df["value"] = pd.to_numeric(demand_load_df["value"], errors="coerce")

    ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])
    ele_price_df.set_index("time", inplace=True)
    ele_price_df = ele_price_df[
        (ele_price_df.index >= save_range_start) & (ele_price_df.index < save_range_end)
    ]

    strategy_df = pd.read_csv(
        f"./data/{exp_name}/{node_name}/opt_result/{strategy_dir}/schedule_result_scale_10_{es_scale}.csv"
    )
    strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
    strategy_df["time"] = pd.to_datetime(strategy_df["time"])
    strategy_df.set_index("time", inplace=True)
    strategy_df = strategy_df[
        (strategy_df.index >= save_range_start) & (strategy_df.index < save_range_end)
    ]

    # model
    simulation_model = EssSimulationModel(es_info)
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(
        demand_load_df, strategy_df, 0
    )
    # total_load_df.to_csv("total_load.csv", index=False, encoding="utf-8")
    origin_balance, opt_balance = simulation_model.revenue_calculation(
        demand_load_df, es_charge_df, ele_price_df, max_demand_price
    )

    opt_max_demand_load_list, ori_max_demand_load_list = get_monthly_max_load(
        total_load_df
    )
    opt_max_demand_cost = max_demand_price * sum(opt_max_demand_load_list)
    ori_max_demand_cost = max_demand_price * sum(ori_max_demand_load_list)

    max_demand_rise_cost = opt_max_demand_cost - ori_max_demand_cost

    revenue = origin_balance - opt_balance - max_demand_rise_cost

    total_energy = demand_load_df["value"].sum()

    ori_cost = origin_balance + ori_max_demand_cost
    opt_cost = opt_balance + opt_max_demand_cost

    es_charge_df["price"] = ele_price_df["value"]
    es_charge_df["balance"] = es_charge_df["value"] * es_charge_df["price"]
    charge_energy = -es_charge_df.loc[es_charge_df["value"] < 0, "value"].sum()
    discharge_energy = es_charge_df.loc[es_charge_df["value"] > 0, "value"].sum()
    charge_balance = -es_charge_df.loc[es_charge_df["balance"] < 0, "balance"].sum()
    discharge_balance = es_charge_df.loc[es_charge_df["balance"] > 0, "balance"].sum()

    return (
        es_scale,
        route_num_str,
        revenue,
        max_demand_rise_cost,
        total_energy,
        ori_cost,
        opt_cost,
        charge_energy,
        discharge_energy,
        charge_balance,
        discharge_balance,
    )


if __name__ == "__main__":
    exp_name = "hongtaiyang"
    print("start!", exp_name)

    # params
    save_range_start = datetime(2025, 10, 1, 0, 0, 0)
    save_range_end = datetime(2025, 11, 1, 0, 0, 0)
    es_scale_list = list(range(150, 1450, 50))
    route_list = ["A"]
    # max_demand_price = 41.6
    # max_demand_price = 38.3
    # max_demand_price = 48.0
    max_demand_price = 33.7
    strategy_dir = "es_scale_experiment_optim_withoutDemand"

    # model
    mp_input_list = [
        (
            x,
            y,
            max_demand_price,
            save_range_start,
            save_range_end,
            exp_name,
            strategy_dir,
        )
        for x in es_scale_list
        for y in route_list
    ]
    mp_result_list = []
    with mp.Pool(processes=8) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)

    # result
    result_df_dict = {}
    for route_i in route_list:
        route_name = f"route_{route_i}"
        result_df_dict[route_name] = pd.DataFrame(
            data=np.nan,
            index=es_scale_list,
            columns=[
                "revenue",
                "max_demand_rise_cost",
                "ori_energy",
                "ori_cost",
                "opt_cost",
                "charge_energy",
                "discharge_energy",
                "charge_balance",
                "discharge_balance",
            ],
        )

    for result_i in mp_result_list:
        scale_i = result_i[0]
        node_name = f"route_{result_i[1]}"
        result_df_dict[node_name].loc[scale_i, "revenue"] = result_i[2]
        result_df_dict[node_name].loc[scale_i, "max_demand_rise_cost"] = result_i[3]
        result_df_dict[node_name].loc[scale_i, "ori_energy"] = result_i[4]
        result_df_dict[node_name].loc[scale_i, "ori_cost"] = result_i[5]
        result_df_dict[node_name].loc[scale_i, "opt_cost"] = result_i[6]
        result_df_dict[node_name].loc[scale_i, "charge_energy"] = result_i[7]
        result_df_dict[node_name].loc[scale_i, "discharge_energy"] = result_i[8]
        result_df_dict[node_name].loc[scale_i, "charge_balance"] = result_i[9]
        result_df_dict[node_name].loc[scale_i, "discharge_balance"] = result_i[10]

    for k, v in result_df_dict.items():
        v.to_csv(
            f"./data/{exp_name}/{k}/opt_result/estimate_result_scale_all_optim_withoutDemand_10.csv"
        )
