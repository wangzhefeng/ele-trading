import pandas as pd
import numpy as np
import cvxpy as cp
import multiprocessing as mp
from datetime import datetime, timedelta
from time_process import generate_hourly_datetime_pairs, get_month_range


class EsArbitraryRangeScheduler_withMaxDemand:
    # TODO 拆分设备参数和运行数据
    def __init__(self, 
                 schedule_time_range: list, 
                 demand_load, 
                 ele_prices, 
                 ele_types, 
                 devices_info, 
                 current_soc_list, 
                 max_demand_line,
                 is_slow_charge: bool = False):
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
        self.max_demand_line = max(max_demand_line, max(demand_load))
    
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

        time_ratio = 5/60

        # 输入定量
        d = np.array(self.demand_load)
        p = np.array(self.ele_prices)
        # e_r_vec = np.array([self.current_soc_list[i] / 100 * e_s_max_vec[i] for i in range(row)])
        # e_r_vec = np.array([self.current_soc_list[i] * e_s_max_vec[i] for i in range(row)])
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
        profit = time_ratio * (e_c_in_agg_vec + e_c_out_agg_vec) @ p

        if self.is_slow_charge:
            profit = profit - lamda_amortize * cp.norm(e_c_in_agg_vec)

            for j in range(column):
                if self.ele_types[j] == "峰":
                    profit = profit + lamda_p * soc_agg_vec[j]
                elif self.ele_types[j] == "尖峰":
                    profit = profit + lamda_tp * soc_agg_vec[j]
        else:
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
        constraints += [e_c_out_agg_vec <= d]

        # 总功率小于最大需量控制线
        constraints += [d - e_c_in_agg_vec <= self.max_demand_line]

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
            
            schedule_i_df = pd.DataFrame({"power_opt": power_array_i}, index=self.schedule_time_range)
            schedule_list.append(schedule_i_df)
        return schedule_list
    
    def run(self):
        profit, charge_array, discharge_array = self.modeling2solving()
        schedule_list = self.schedule_generate(charge_array, discharge_array)
        
        return schedule_list


exp_name = "estimate830"
month_num = 9
node_name = "route_B_{:02d}".format(month_num)

demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load.csv")
demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])

max_demand_control_line = demand_load_df["value"].max()

devices_info = [{"usable_depth": 0.97,
                "charge_loss": 0.92,
                "discharge_loss": 0.95,
                "es_charge_max": 9000,
                "es_charge_min": -9000,
                "es_capacity_max": 18000,
                "es_capacity_min": 0}]



def one_process(ratio, demand_load_df, ele_price_df, month_num, devices_info, max_demand_control_line):
    validation_day_list = generate_hourly_datetime_pairs(2025, month_num, 22)
    days_strategy_list = []
    for time_pair in validation_day_list:
        vs_time = time_pair[0]
        ve_time = time_pair[1]
        mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
        step_demand_load_df = demand_load_df.loc[mask]
        mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
        step_ele_price_df = ele_price_df.loc[mask]
        max_demand_control_line_i = max_demand_control_line * (1 + ratio/1000)
        scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(step_demand_load_df["time"].to_list(), 
                                                              step_demand_load_df["value"].to_list(), 
                                                              step_ele_price_df["value"].to_list(), 
                                                              step_ele_price_df["type"].to_list(),
                                                              devices_info,
                                                              [0],
                                                              max_demand_control_line_i,
                                                              True)
        opt_list = scheduler_model.run()
        days_strategy_list.append(opt_list[0])

    result_df = pd.concat(days_strategy_list)
    result_df["time"] = result_df.index
    
    save_range_start, save_range_end = get_month_range(month_num, 2025)
    
    mask = (result_df['time'] >= save_range_start) & (result_df['time'] < save_range_end)
    save_result_df = result_df.loc[mask]
    mask = (demand_load_df['time'] >= save_range_start) & (demand_load_df['time'] < save_range_end)
    save_demand_load_df = demand_load_df.loc[mask]
    mask = (ele_price_df['time'] >= save_range_start) & (ele_price_df['time'] < save_range_end)
    save_ele_price_df = ele_price_df.loc[mask]
    
    return ratio, save_result_df, save_demand_load_df, save_ele_price_df



if __name__ == '__main__':
    print("start!", exp_name, "month:", month_num, "node:", node_name)
    mp_input_list = [
        (ratio, demand_load_df, ele_price_df, month_num, devices_info, max_demand_control_line) for ratio in range(10, 240, 10)
    ]
    mp_result_list = []

    with mp.Pool(processes=6) as pool:
        mp_result_list = pool.starmap(one_process, mp_input_list)
    
    for result_i in mp_result_list:
        save_ratio = result_i[0]
        result_i[1].to_csv(f"./data/{exp_name}/{node_name}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{save_ratio}.csv")
        result_i[2].to_csv(f"./data/{exp_name}/{node_name}/opt_result/demand_load.csv")
        result_i[3].to_csv(f"./data/{exp_name}/{node_name}/opt_result/ele_price.csv")
