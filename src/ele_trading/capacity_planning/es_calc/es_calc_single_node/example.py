"""
单节点储能调度算法统一模块使用示例。

通过 version 参数切换算法版本:
  - "without_demand": 纯峰谷套利基线（不含需量优化）
  - "basic":          单节点基础版（近似需量惩罚 + 平滑项）
  - "optim":          单节点激进版（精确需量建模 + 变压器容量约束）

数据来源: data/profit_calc/zijie/route_A/
"""
from datetime import datetime

import pandas as pd

from src.es_calc.es_calc_single_node import get_profile, EsArbitraryRangeScheduler, PipelineParams


def run_single_scale_example():
    """加载 zijie 实际数据，运行单个 es_scale 的调度。"""
    version = "optim"
    profile = get_profile(version)

    es_scale = 1000
    devices_info = [{
        "usable_depth": 0.90,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": es_scale,
        "es_charge_min": -es_scale,
        "es_capacity_max": es_scale * 2,
        "es_capacity_min": 0,
    }]

    # 加载 zijie 实际数据（取第一个月）
    demand_load_df = pd.read_csv("./data/profit_calc/zijie/route_A/demand_load.csv")
    demand_load_df["time"] = pd.to_datetime(demand_load_df["time"])
    ele_price_df = pd.read_csv("./data/profit_calc/zijie/route_A/ele_price.csv")
    ele_price_df["time"] = pd.to_datetime(ele_price_df["time"])

    start_time = datetime(2025, 4, 1, 0, 0, 0)
    end_time = datetime(2025, 5, 1, 0, 0, 0)
    mask = (demand_load_df["time"] >= start_time) & (demand_load_df["time"] < end_time)
    step_demand = demand_load_df.loc[mask]
    mask = (ele_price_df["time"] >= start_time) & (ele_price_df["time"] < end_time)
    step_price = ele_price_df.loc[mask]

    scheduler = EsArbitraryRangeScheduler(
        schedule_time_range=step_demand["time"].to_list(),
        demand_load=step_demand["value"].to_list(),
        ele_prices=step_price["value"].to_list(),
        ele_types=step_price["type"].to_list(),
        devices_info=devices_info,
        current_soc_list=[0],
        max_demand_price=40.8,
        freq_minutes=60,
        profile=profile,
        transform_capacity=63000,
    )

    schedule_list = scheduler.run()
    print(f"version={version}, es_scale={es_scale}")
    print(schedule_list[0].head())


def run_pipeline_example():
    """多进程批量调度示例配置。"""
    version = "optim"
    profile = get_profile(version)

    params = PipelineParams(
        exp_name="profit_calc/zijie",
        start_time=datetime(2025, 4, 1, 0, 0, 0),
        end_time=datetime(2026, 4, 1, 0, 0, 0),
        freq_minutes=60,
        es_scale_list=list(range(0, 21000, 1000)),
        node_name_list=["route_A"],
        max_demand_price=40.8,
        transform_capacity=63000,
        num_processes=8,
        strategy_dir=f"es_scale_experiment_{version}",
    )

    print(f"version={version}, profile={profile}")
    print(f"exp={params.exp_name}, scales={params.es_scale_list}")


if __name__ == "__main__":
    run_single_scale_example()
    run_pipeline_example()
