import pandas as pd
import numpy as np
import cvxpy as cp
import copy
import calendar
import multiprocessing as mp
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from utils.time_process import generate_hourly_datetime_pairs, get_month_range, generate_day_pairs

plt.rcParams['font.sans-serif']=['SimHei']    # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False    # 用来显示负号

exp_name = "estimate1016"
node_name = "route_A"
max_demand_price = 34.2

class EsArbitraryRangeScheduler_withMaxDemand:
    def __init__(self, 
                 schedule_time_range,
                 demand_load, 
                 ele_prices, 
                 ele_types, 
                 devices_info, 
                 current_soc_list, 
                 MaxDemandPrice, 
                 is_slow_charge = False):
        self.schedule_time_range = schedule_time_range
        self.schedule_time_length = len(self.schedule_time_range)
        self.demand_load = demand_load
        self.ele_prices = ele_prices
        self.ele_types = ele_types
        self.devices_num = len(devices_info)
        self.is_slow_charge = is_slow_charge
        self.current_soc_list = current_soc_list
        self.charge_loss_list = [i["charge_loss"] for i in devices_info]
        self.discharge_loss_list = [i["discharge_loss"] for i in devices_info]
        self.es_charge_max_list = [i["es_charge_max"] for i in devices_info]
        self.es_discharge_max_list = [i["es_charge_min"] for i in devices_info]
        self.es_capacity_max_list = [i["es_capacity_max"] * i["usable_depth"] for i in devices_info]
        self.es_capacity_min_list = [i["es_capacity_min"] for i in devices_info]
        self.max_demand_price = MaxDemandPrice
    
    def modeling2solving(self):
        row = self.devices_num
        column = self.schedule_time_length

        #设备参数
        c_l_in_vec = np.array(self.charge_loss_list).reshape((row, 1))
        c_l_out_vec = np.array(self.discharge_loss_list).reshape((row, 1))
        e_c_max_vec = np.array(self.es_charge_max_list).reshape((row, 1))
        e_c_min_vec = np.array(self.es_discharge_max_list).reshape((row, 1))
        e_s_max_vec = np.array(self.es_capacity_max_list).reshape((row, 1))
        e_s_min_vec = np.array(self.es_capacity_min_list).reshape((row, 1))
        
        #充放电模式参数
        lamda_v = 0.0001
        lamda_f = 0.0001
        lamda_p = -3 * lamda_v
        lamda_tp = 2 * lamda_p

        lamda_amortize = 0.001

        time_ratio = 15/60

        # 输入定量
        d = np.array(self.demand_load)
        p = np.array(self.ele_prices)
        e_r_vec = np.array(self.current_soc_list)

        # 定义设备级变量
        e_c_in_matrix = cp.Variable((row, column))
        e_c_out_matrix = cp.Variable((row, column))
        soc_matrix = cp.Variable((row, column))
        # 定义节点级变量
        e_c_in_agg_vec = cp.sum(e_c_in_matrix, axis=0)
        e_c_out_agg_vec = cp.sum(e_c_out_matrix, axis=0)
        soc_agg_vec = cp.sum(soc_matrix, axis=0)

        # 目标函数
        profit = time_ratio * (e_c_in_agg_vec + e_c_out_agg_vec) @ p - self.max_demand_price * cp.max(d - e_c_in_agg_vec - e_c_out_agg_vec)
        
        for j in range(column):
            if self.ele_types[j] == "谷":
                profit = profit + lamda_v * soc_agg_vec[j]
            elif self.ele_types[j] == "峰":
                profit = profit + lamda_p * soc_agg_vec[j]
            elif self.ele_types[j] == "尖峰":
                profit = profit + lamda_tp * soc_agg_vec[j]
            elif self.ele_types[j] == "平":
                profit = profit + lamda_f * soc_agg_vec[j]


        obj = cp.Maximize(profit)

        # 设置约束条件
        constraints = []

        # 充电功率和实时电量匹配
        for i in range(row):
            for j in range(column):
                constraints += [soc_matrix[i, j] == e_r_vec[i] \
                                - cp.sum(e_c_in_matrix[i, :j+1]) * time_ratio * c_l_in_vec[i] \
                                - cp.sum(e_c_out_matrix[i, :j+1]) * time_ratio / c_l_out_vec[i]]

        # 放电功率小于需量
        constraints += [e_c_out_agg_vec <= cp.pos(d)]

        # 储能系统每个时段的充放电功率限制
        constraints += [e_c_out_matrix <= e_c_max_vec]
        constraints += [e_c_out_matrix >= 0]
        constraints += [e_c_in_matrix <= 0]
        constraints += [e_c_in_matrix >= e_c_min_vec]

        # 对电量损耗的保底电量限制
        # （此条限制在滚动策略中无法保证满足，建议在EMS中进行设置）
        # constraints += [soc >= e_s_max * 0.01]

        # 储能器容量限制
        constraints += [soc_matrix >= e_s_min_vec]
        constraints += [soc_matrix <= e_s_max_vec]

        # 峰谷平时段充放电矫正
        for j in range(column):
            if self.ele_types[j] == "谷":
                constraints += [e_c_out_agg_vec[j] == 0]
            elif self.ele_types[j] == "峰":
                constraints += [e_c_in_agg_vec[j] == 0]
            elif self.ele_types[j] == "尖峰":
                constraints += [e_c_in_agg_vec[j] == 0]
            elif self.ele_types[j] == "平":
                constraints += [e_c_out_agg_vec[j] == 0]

        prob = cp.Problem(obj, constraints)
        result = prob.solve(verbose = False, solver = cp.CLARABEL)
        return result, e_c_in_matrix.value, e_c_out_matrix.value
    
    def schedule_generate(self, charge_array, discharge_array):
        schedule_list = []
        
        for device_i in range(self.devices_num):
            power_array_i = charge_array[device_i] + discharge_array[device_i]
            power_array_i = np.around(power_array_i, decimals=3)
            
            for j in range(len(power_array_i)):
                if abs(power_array_i[j]) < 0.1:
                    power_array_i[j] = 0
            
            schedule_i_df = pd.DataFrame({"time": self.schedule_time_range, "power_opt": power_array_i})
            schedule_i_df.set_index("time", inplace = True)
            schedule_list.append(schedule_i_df)
        return schedule_list
    
    def run(self):
        profit, charge_array, discharge_array = self.modeling2solving()
        schedule_list = self.schedule_generate(charge_array, discharge_array)
        
        return schedule_list

def generate_monthly_timestamps(start_dt, end_dt):
    if start_dt > end_dt:
        raise ValueError("开始时间不能晚于结束时间")
    
    result = []
    
    # 将开始时间归一化到当月的第一天
    current_start = start_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    while current_start < end_dt:
        # 计算当前月份的结束时间（下个月第一天的0点）
        # 方法：先找到下个月1号
        if current_start.month == 12:
            next_month_start = current_start.replace(year=current_start.year + 1, month=1, day=1)
        else:
            next_month_start = current_start.replace(month=current_start.month + 1, day=1)
        
        current_end = next_month_start
        
        # 如果当前月份的开始时间小于结束时间，则添加到结果中
        # 注意：这里我们添加的是从 current_start 到 current_end 的区间
        # 但需要确保这个区间与用户的 start_dt 和 end_dt 范围有交集
        interval_start = max(current_start, start_dt)  # 实际有效的开始时间
        interval_end = min(current_end, end_dt)        # 实际有效的结束时间
        
        # 只有当有效区间存在时才添加
        if interval_start < interval_end:
            result.append((interval_start, interval_end))
        
        # 移动到下一个月
        current_start = next_month_start
    
    return result
  
devices_info = [{"usable_depth": 0.95,
                "charge_loss": 0.92,
                "discharge_loss": 0.95,
                "es_charge_max": 12500,
                "es_charge_min": -12500,
                "es_capacity_max": 25000,
                "es_capacity_min": 0}]

demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load.csv")
demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])

def one_process(vs_time, ve_time):
    mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
    step_demand_load_df = demand_load_df.loc[mask]
    mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
    step_ele_price_df = ele_price_df.loc[mask]
    
    scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(step_demand_load_df["time"].to_list(), 
                                                              step_demand_load_df["value"].to_list(), 
                                                              step_ele_price_df["value"].to_list(), 
                                                              step_ele_price_df["type"].to_list(),
                                                              devices_info,
                                                              [0],
                                                              max_demand_price)
    opt_list = scheduler_model.run()
    
    return opt_list

if __name__ == '__main__':
    print("start!", exp_name)
    save_range_start = datetime(2024, 3, 1, 0, 0, 0)
    save_range_end = datetime(2025, 3, 1, 0, 0, 0)
    mp_input_list = generate_monthly_timestamps(save_range_start, save_range_end)
    mp_result_list = []

    with mp.Pool(processes=12) as pool:
            mp_result_list = pool.starmap(one_process, mp_input_list)
    
    days_strategy_list = []
    for result_i in mp_result_list:
        days_strategy_list.append(result_i[0])
    result_df = pd.concat(days_strategy_list)
    result_df.to_csv(f"./data/{exp_name}/{node_name}/opt_result/schedule_result_month_opt_dod95.csv")