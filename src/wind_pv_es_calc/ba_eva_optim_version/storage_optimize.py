import numpy as np
import pandas as pd
from pyscipopt import Model, quicksum


def optimize_storage(
    price_curve,
    load_curve,
    transformer_rating,
    time_interval_hours=0.25,
    charge_efficiency=0.95,
    discharge_efficiency=0.95,
    min_depth_of_discharge=0.1,
    min_power_ratio=0.0,
    max_cycles_per_year=650,
    min_utilization=0.75,
    storage_cost_per_kwh=0.5,
    c_rate=0.5,
):
    """
    优化储能系统的额定功率和容量

    参数:
    price_curve: 电价曲线 (list/numpy array)
    load_curve: 用电曲线 (list/numpy array)
    transformer_rating: 变压器额定功率 (float)
    time_interval_hours: 时间间隔（小时），默认0.25（15分钟）
    charge_efficiency: 充电效率 (默认0.95)
    discharge_efficiency: 放电效率 (默认0.95)
    min_depth_of_discharge: 最小放电深度 (默认0.1)
    min_power_ratio: 最小功率比例 (默认0.0)
    max_cycles_per_year: 年最大循环次数 (默认650)
    min_utilization: 最小利用率 (默认0.75)
    storage_cost_per_kwh: 储能度电成本（元/kWh）
    c_rate: 功率容量比, 放电倍率 (默认0.5)

    返回:
    optimal_power: 最优储能额定功率
    optimal_capacity: 最优储能额定容量
    actual_utilization: 实际利用率
    charge_schedule: 充电计划
    discharge_schedule: 放电计划
    energy_level: 储能能量水平
    """
    # 时间周期数
    total_periods = len(price_curve)

    # 计算当前时间长度对应的最大循环次数
    hours_per_year = 365 * 24  # 一年的总小时数
    total_hours = total_periods * time_interval_hours  # 当前数据的总小时数
    max_cycles = int(max_cycles_per_year * max(total_hours / hours_per_year, 1.0))

    # 创建模型
    model = Model("Storage Optimization")

    # 设置求解时间限制
    model.setParam("limits/time", 200)
    # 设置可行性容差
    model.setParam("numerics/feastol", 1e-4)
    model.setParam("numerics/dualfeastol", 1e-4)

    # 创建变量字典
    P_ch = {}  # 充电功率（非负）
    P_dis = {}  # 放电功率（非负）
    E = {}  # 储能能量水平
    u_ch = {}  # 充电状态二进制变量
    u_dis = {}  # 放电状态二进制变量

    # 创建变量
    for t in range(total_periods):
        P_ch[t] = model.addVar(lb=0, name=f"P_ch_{t}")  # 充电功率
        P_dis[t] = model.addVar(lb=0, name=f"P_dis_{t}")  # 放电功率
        E[t] = model.addVar(lb=0, name=f"E_{t}")  # 储能能量水平
        u_ch[t] = model.addVar(vtype="B", name=f"u_ch_{t}")  # 充电状态
        u_dis[t] = model.addVar(vtype="B", name=f"u_dis_{t}")  # 放电状态

    Cap_rated = model.addVar(lb=0, name="Cap_rated")  # 额定容量
    P_rated = Cap_rated * c_rate  # 额定功率

    # 约束条件
    for t in range(total_periods):
        # 功率约束
        model.addCons(P_ch[t] <= P_rated)  # 充电功率限制
        model.addCons(P_dis[t] <= P_rated)  # 放电功率限制

        # 充放电互斥约束
        model.addCons(u_ch[t] + u_dis[t] <= 1)  # 不能同时充放电

        # 最小功率约束
        if min_power_ratio > 0:
            model.addCons(
                P_ch[t] >= P_rated * min_power_ratio * u_ch[t]
            )  # 充电时最小功率
            model.addCons(
                P_dis[t] >= P_rated * min_power_ratio * u_dis[t]
            )  # 放电时最小功率
        else:
            model.addCons(P_ch[t] <= P_rated * u_ch[t])  # 不工作时充电功率为0
            model.addCons(P_dis[t] <= P_rated * u_dis[t])  # 不工作时放电功率为0

        # 放电功率不能超过当前用电需求
        model.addCons(P_dis[t] <= load_curve[t])  # 放电功率不能超过负荷

        # 变压器容量约束
        model.addCons(load_curve[t] + P_ch[t] <= transformer_rating)

        # 储能能量水平约束
        if t == 0:
            # 初始状态约束：设为中等荷电状态
            model.addCons(E[t] == Cap_rated * 0.5)  # 初始状态设为50%容量
        else:
            model.addCons(
                E[t]
                == E[t - 1]
                + P_ch[t] * charge_efficiency * time_interval_hours
                - P_dis[t] * time_interval_hours / discharge_efficiency
            )

        # 储能容量约束（考虑DOD）
        model.addCons(E[t] <= Cap_rated)  # 不能超过额定容量
        model.addCons(E[t] >= Cap_rated * min_depth_of_discharge)  # 不能低于最小DOD

    # 确保结束时至少保留50%电量
    model.addCons(E[total_periods - 1] >= Cap_rated * 0.5)

    # 计算总放电量
    total_discharge = quicksum(
        P_dis[t] * time_interval_hours for t in range(total_periods)
    )

    # 利用率约束
    model.addCons(total_discharge >= max_cycles * Cap_rated * min_utilization)
    model.addCons(total_discharge <= max_cycles * Cap_rated)

    # 目标函数：最大化套利收益 - 储能成本
    # 套利收益部分
    weighted_prices = [
        price_curve[t] * time_interval_hours for t in range(total_periods)
    ]
    discharge_revenue = quicksum(
        weighted_prices[t] * P_dis[t] for t in range(total_periods)
    )
    charge_cost = quicksum(weighted_prices[t] * P_ch[t] for t in range(total_periods))
    # 储能成本部分：总放电量 * 度电成本
    storage_cost = quicksum(
        P_dis[t] * time_interval_hours * storage_cost_per_kwh
        for t in range(total_periods)
    )

    # 综合目标函数
    model.setObjective(discharge_revenue - charge_cost - storage_cost, "maximize")

    # 求解
    model.optimize()

    # 检查求解状态
    status = model.getStatus()
    print(f"求解状态: {status}")

    # 获取收益值
    income = model.getObjVal()

    # 获取结果
    optimal_power = model.getVal(P_rated)
    optimal_capacity = model.getVal(Cap_rated)

    # 计算实际利用率
    total_discharge_value = sum(
        model.getVal(P_dis[t]) * time_interval_hours for t in range(total_periods)
    )
    actual_utilization = (
        total_discharge_value / (max_cycles * optimal_capacity)
        if optimal_capacity > 0
        else 0
    )

    # 获取充放电计划和储能能量水平
    charge_schedule = [model.getVal(P_ch[t]) for t in range(total_periods)]
    discharge_schedule = [model.getVal(P_dis[t]) for t in range(total_periods)]
    energy_level = [model.getVal(E[t]) for t in range(total_periods)]

    def print_energy_statistics(
        model, total_periods, time_interval_hours, charge_efficiency=0.95
    ):
        """打印详细的电量统计信息"""
        print("\n-------------充放电量对比------------------\n")

        # 方法1：从功率计算
        total_charge_kwh = 0
        total_discharge_kwh = 0

        for t in range(total_periods):
            charge_power = model.getVal(P_ch[t])  # kW
            discharge_power = model.getVal(P_dis[t])  # kW

            total_charge_kwh += charge_power * time_interval_hours
            total_discharge_kwh += discharge_power * time_interval_hours

        print(f"从功率计算:")
        print(f"  总充电电量: {total_charge_kwh / 1000:.2f} MWh")
        print(f"  总放电电量: {total_discharge_kwh / 1000:.2f} MWh")
        print(
            f"  充放电效率: {total_discharge_kwh/total_charge_kwh*100:.2f}%"
            if total_charge_kwh > 0
            else "  充电电量为0"
        )

        # 方法2：从能量变化计算
        energy_levels = [model.getVal(E[t]) for t in range(total_periods)]
        charge_from_energy = 0
        discharge_from_energy = 0

        for t in range(1, total_periods):
            prev_energy = energy_levels[t - 1]
            curr_energy = energy_levels[t]

            if curr_energy > prev_energy:
                charge_amount = (curr_energy - prev_energy) / charge_efficiency
                charge_from_energy += charge_amount
            elif curr_energy < prev_energy:
                discharge_amount = (prev_energy - curr_energy) * discharge_efficiency
                discharge_from_energy += discharge_amount

        print(f"\n从能量变化计算:")
        print(f"  总充电电量: {charge_from_energy/1000:.2f} MWh")
        print(f"  总放电电量: {discharge_from_energy/1000:.2f} MWh")
        print(
            f"  充放电效率: {discharge_from_energy/charge_from_energy*100:.2f}%"
            if charge_from_energy > 0
            else "  充电电量为0"
        )

        # 验证一致性
        print(f"\n验证:")
        print(f"  功率法充电电量: {total_charge_kwh/1000:.2f} MWh")
        print(f"  能量法充电电量: {charge_from_energy/1000:.2f} MWh")
        print(f"  差异: {abs(total_charge_kwh - charge_from_energy)/1000:.4f} MWh")

        return total_charge_kwh, total_discharge_kwh

    charge_kwh, discharge_kwh = print_energy_statistics(
        model, total_periods, time_interval_hours, charge_efficiency
    )

    return (
        income,
        optimal_power,
        optimal_capacity,
        actual_utilization,
        charge_schedule,
        discharge_schedule,
        energy_level,
        charge_kwh,
        discharge_kwh,
    )


def resample_to_hourly(df, time_col="日期时间"):
    """将15分钟数据重采样为1小时数据"""
    df = df.set_index(time_col)

    # 重采样规则
    resampled = df.resample("1h").agg(
        {"电能价格": "mean", "Load": "sum"}  # 电价取平均值  # 负荷取累计值（kWh）
    )

    return resampled.reset_index()


if __name__ == "__main__":
    # 处理数据，需要改下路径
    price_df = pd.read_csv("data/内蒙项目/merged_df.csv", parse_dates=["日期时间"])
    load_df = pd.read_excel(
        "data/内蒙项目/兴达_processed.xlsx",
        skiprows=96,
        names=["time", "load"],
        parse_dates=["time"],
    )
    # 过滤异常值
    filter_mask = (
        (load_df["time"] >= "2025-02-19 00:00:00")
        & (load_df["time"] <= "2025-02-19 23:45:00")
    ) | (
        (load_df["time"] >= "2025-08-16 00:00:00")
        & (load_df["time"] <= "2025-08-16 23:45:00")
    )
    load_df = load_df[~filter_mask]
    load_df["period"] = list(range(96)) * (len(load_df) // 96)
    load_curve = load_df.groupby("period")["load"].mean()
    price_df["Load"] = np.array(load_curve.tolist() * (len(price_df) // 96))
    resampled_df = resample_to_hourly(price_df)

    # 打印电价和负荷曲线的最大最小值
    price_curve = resampled_df["电能价格"].values / 1000  # 单位为元/kWh
    load_curve = resampled_df["Load"].values
    print("price max: {}, price min: {}.".format(price_curve.max(), price_curve.min()))
    print("load max: {}, load min: {}.".format(load_curve.max(), load_curve.min()))

    transformer_rating = int(load_curve.max() * 2.0)  # 变压器容量(KVA)
    storage_cost_per_kwh = 0.65  # 元/kWh
    time_interval_hours = 1.0  # 时间间隔（小时）

    # 放电比例，即C率
    for min_utilization in [0.5, 0.6, 0.7]:
        for c_rate in [0.25, 0.5, 1.0]:
            print("\n-------------限定条件------------------\n")
            print(f"变压器容量: {transformer_rating} KVA")
            print(f"储能度电成本: {storage_cost_per_kwh} 元/KWh")
            print(f"最小利用率: {min_utilization*100:.2f}%")
            print(f"功率容量比: {c_rate}C")
            # 求解
            (
                income,
                optimal_power,
                optimal_capacity,
                actual_utilization,
                charge_schedule,
                discharge_schedule,
                energy_level,
                charge_kwh,
                discharge_kwh,
            ) = optimize_storage(
                price_curve,
                load_curve,
                transformer_rating,
                time_interval_hours=time_interval_hours,
                charge_efficiency=0.95,
                discharge_efficiency=0.95,
                min_depth_of_discharge=0.1,
                min_power_ratio=0.1,
                max_cycles_per_year=650,
                min_utilization=min_utilization,
                storage_cost_per_kwh=storage_cost_per_kwh,
                c_rate=c_rate,
            )
            print("\n-------------最终结果------------------\n")
            print(f"最优储能额定功率: {optimal_power/1000:.2f} MW")
            print(f"最优储能额定容量: {optimal_capacity/1000:.2f} MWh")
            print(f"最小利用率: {min_utilization:.2%}")
            print(f"实际利用率: {actual_utilization:.2%}")
            print(f"充电电量: {charge_kwh/1000:.2f} MWh")
            print(f"放电电量: {discharge_kwh/1000:.2f} MWh")
            print(f"套利净收益: {income/10000:.2f} 万元")
