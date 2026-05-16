import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import time
import calendar

import pandas as pd
import numpy as np
import cvxpy as cp
import multiprocessing as mp

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
                constraints += [
                    soc_matrix[i, j] == e_r_vec[i] \
                    - cp.sum(e_c_in_matrix[i, :j+1]) * time_ratio * c_l_in_vec[i] \
                    - cp.sum(e_c_out_matrix[i, :j+1]) * time_ratio / c_l_out_vec[i]
                ]
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
            power_array_i = np.around(charge_array[device_i] + discharge_array[device_i], decimals=3)
            power_array_i = np.asarray(list(map(lambda x: 0.0 if abs(x) < 0.1 else x, power_array_i.tolist())))
            schedule_i_df = pd.DataFrame({"power_opt": power_array_i}, index=self.schedule_time_range)
            schedule_list.append(schedule_i_df)
        return schedule_list
    
    def run(self):
        profit, charge_array, discharge_array = self.modeling2solving()
        schedule_list = self.schedule_generate(charge_array, discharge_array)
        return schedule_list


class EssSimulationModel:
    
    def __init__(self, energy_storage_system_config: dict):
        self.transform_capacity = energy_storage_system_config["transform_capacity"]
        self.invert_band = energy_storage_system_config["invertband"]
        self.battery_capacity = energy_storage_system_config["es_capacity_max"]
        self.SOH = energy_storage_system_config["usable_depth"]
        self.soc_redundant_ratio = energy_storage_system_config["soc_redundant_ratio"]
        self.max_charge_power = energy_storage_system_config["es_charge_min"]
        self.max_discharge_power = energy_storage_system_config["es_charge_max"]
        self.charge_efficiency = energy_storage_system_config["charge_loss"]
        self.dicharge_efficiency = energy_storage_system_config["discharge_loss"]

    def one_step(self, time_lag, demand_load, command, soc):
        # 放电过程
        if command > 0:
            charge = command
            # 外部限制
            # 储能放电功率上限
            charge = min(charge, self.max_discharge_power)
            # 放电功率 不超过 需求负荷功率
            charge = min(charge, demand_load - self.invert_band)
            # 内部限制
            # 储能系统放电损失
            inner_energy_vari = (charge / self.dicharge_efficiency) * time_lag
            # 保底电量限制
            if (soc - inner_energy_vari) < (self.battery_capacity * self.soc_redundant_ratio):
                if soc < (self.battery_capacity * self.soc_redundant_ratio):
                    inner_energy_vari = 0
                else:
                    inner_energy_vari = soc - (self.battery_capacity * self.soc_redundant_ratio)
            # 反推放电功率
            charge = (inner_energy_vari / time_lag) * self.dicharge_efficiency
            soc = soc - inner_energy_vari
        # 充电过程
        elif command < 0:
            charge = command
            # 外部限制
            # 储能充电功率上限
            charge = max(charge, self.max_charge_power)
            # 包含充电功率在内的总功率 不超过 站点主变压器最大功率 
            if demand_load - charge > self.transform_capacity:
                charge = self.transform_capacity - demand_load
            # 内部限制
            # 储能系统充电损失
            inner_energy_vari = (charge * self.charge_efficiency) * time_lag
            # 储能器容量限制
            if soc - inner_energy_vari > (self.battery_capacity * self.SOH):
                inner_energy_vari = -max((self.battery_capacity * self.SOH) - soc, 0)
            # 反推放电功率
            charge = (inner_energy_vari / time_lag) / self.charge_efficiency
            soc = soc - inner_energy_vari
        # 待机过程
        elif command == 0:
            charge = 0
            inner_energy_vari = 0
            soc = soc
        
        return charge, inner_energy_vari, soc
        
    def simulation_process(self, demand_load, es_strategy, last_soc):
        es_charge_list = []
        es_soc_list = []
        es_charge_time_list = []
        es_soc_time_list = []
        
        time_i = None
        time_diff = None
        demand_load_i = None
        es_strategy_i = None
        soc_i = last_soc
        
        for index, row in demand_load.iterrows():
            if time_i:
                # 计算单步仿真时长
                time_diff = (index - time_i)
                time_diff_hour = time_diff.total_seconds() / (60 * 60)
                assert time_diff_hour > 0, "wrong time index sequence"
                es_charge_i, es_energy_i, es_soc_i = self.one_step(time_diff_hour, demand_load_i, es_strategy_i, soc_i)
                soc_i = es_soc_i
                es_charge_list.append(es_charge_i)
                es_charge_time_list.append(time_i)
            # 保存实时soc值
            es_soc_list.append(soc_i)
            es_soc_time_list.append(index)
            
            time_i = index
            demand_load_i = row["value"]
            es_strategy_i = es_strategy.loc[(es_strategy.index <= index)]["value"].iloc[-1]
        
        # 结尾步计算
        time_diff_hour = time_diff.total_seconds() / (60 * 60)
        es_charge_i, es_energy_i, es_soc_i = self.one_step(time_diff_hour, demand_load_i, es_strategy_i, soc_i)
        es_charge_list.append(es_charge_i)
        es_charge_time_list.append(time_i)
        
        soc_i = es_soc_i
        es_soc_list.append(soc_i)
        es_soc_time_list.append(time_i + time_diff)
        
        # 结果组织
        es_charge_df =  pd.DataFrame({"value": es_charge_list}, index = es_charge_time_list)
        total_load_df =  pd.DataFrame({
            "total_load": np.array(demand_load["value"]) - np.array(es_charge_list),
            "demand_load": demand_load["value"],
            "es_load": es_charge_list
        }, index = es_charge_time_list)
        es_soc_df =  pd.DataFrame({"value": es_soc_list}, index = es_soc_time_list)
        
        return es_charge_df, es_soc_df, total_load_df
    
    @staticmethod
    def revenue_calculation(demand_load, es_load, ele_price, max_demand_price):
        origin_balance = 0
        opt_balance = 0
        ori_max_load = 0
        opt_max_load = 0
        total_hours = 0
        time_i = None
        for index, row in demand_load.iterrows():
            if time_i:
                # 计算单步仿真时长
                time_diff = (index - time_i)
                time_diff_hour = time_diff.total_seconds() / (60 * 60)
                assert time_diff_hour > 0, "wrong time index sequence"
                
                origin_balance_i = demand_load_i * time_diff_hour * ele_price_i
                opt_balance_i = (demand_load_i - es_load_i) * time_diff_hour * ele_price_i
                
                ori_max_load = max(ori_max_load, demand_load_i)
                opt_max_load = max(opt_max_load, demand_load_i - es_load_i)
                
                origin_balance = origin_balance + origin_balance_i
                opt_balance = opt_balance + opt_balance_i
                
                total_hours = total_hours + time_diff_hour
            
            time_i = index
            demand_load_i = row["value"]
            es_load_i = es_load.loc[index, "value"]
            ele_price_i = ele_price.loc[(ele_price.index <= index)]["value"].iloc[-1]
        # 结尾步计算
        time_diff_hour = time_diff.total_seconds() / (60 * 60)
        origin_balance_i = demand_load_i * time_diff_hour * ele_price_i
        opt_balance_i = (demand_load_i - es_load_i) * time_diff_hour * ele_price_i

        ori_max_load = max(ori_max_load, demand_load_i)
        opt_max_load = max(opt_max_load, demand_load_i - es_load_i)
        
        origin_balance = origin_balance + origin_balance_i
        opt_balance = opt_balance + opt_balance_i
        
        total_hours = total_hours + time_diff_hour
        
        # 需量电费计算
        time_diff_month = total_hours / 24 / calendar.monthrange(time_i.year, time_i.month)[1]
        origin_balance += max_demand_price * ori_max_load * time_diff_month
        opt_balance += max_demand_price * opt_max_load * time_diff_month
            
        return origin_balance, opt_balance


def optim_one_process(month_num, ratio, route, devices_info, demand_load_df, ele_price_df, ):
    print(f"{'='*80}\n每月策略调度模型::month_num-route-ratio: {month_num:02d}-{route}-{ratio} start...")
    # time periods
    # --------------
    validation_day_list = generate_hourly_datetime_pairs(2025, month_num, 22)
    # daily strategy
    # --------------
    days_strategy_list = []
    #! ------------------------------------------
    #! for 循环
    #! ------------------------------------------
    for time_pair in validation_day_list:
        # print(f"{'='*60}\n每月策略调度模型::month_num-route-ratio-time_pair: {month_num}-{route}-{ratio}-{time_pair} start...")
        # calc max demand control line
        # --------------
        # chunk time
        vs_time, ve_time = time_pair[0], time_pair[1]
        # chunk demand load
        mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
        step_demand_load_df = demand_load_df.loc[mask]
        # print(f"每月策略调度模型::step_demand_load_df.shape: {step_demand_load_df.shape}")
        # chunk ele price
        mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
        step_ele_price_df = ele_price_df.loc[mask]
        # print(f"每月策略调度模型::step_ele_price_df.shape: {step_ele_price_df.shape}")
        # max demand control line
        max_demand_control_line_i = step_demand_load_df["value"].max() * (1 + ratio/1000)
        # print(f"每月策略调度模型::max_demand_control_line_i: {max_demand_control_line_i}")
        # scheduler
        # --------------
        scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(
            step_demand_load_df["time"].to_list(), 
            step_demand_load_df["value"].to_list(), 
            step_ele_price_df["value"].to_list(), 
            step_ele_price_df["type"].to_list(),
            devices_info,
            [0],
            max_demand_control_line_i,
            True
        )
        opt_list = scheduler_model.run()
        days_strategy_list.append(opt_list[0])
        # print(f"每月策略调度模型::opt_list: \n{opt_list}")
        # print(f"每月策略调度模型::days_strategy_list: \n{days_strategy_list}")
        # print(f"每月策略调度模型::month_num-route-ratio-time_pair: {month_num}-{route}-{ratio}-{time_pair} end...\n{'='*60}")
    # result collect
    # --------------
    result_df = pd.concat(days_strategy_list)
    result_df["time"] = result_df.index
    # print(f"每月策略调度模型::result_df: \n{result_df} \nresult_df.shape: {result_df.shape}")
    # one month result process
    # --------------
    # time params
    save_range_start, save_range_end = get_month_range(month_num, 2025)
    # print(f"每月策略调度模型::save_range_start: {save_range_start}, save_range_end: {save_range_end}")
    # result filter
    mask = (result_df['time'] >= save_range_start) & (result_df['time'] < save_range_end)
    save_result_df = result_df.loc[mask]
    # print(f"每月策略调度模型::save_result_df.shape: {save_result_df.shape}")
    # demand load res
    mask = (demand_load_df['time'] >= save_range_start) & (demand_load_df['time'] < save_range_end)
    save_demand_load_df = demand_load_df.loc[mask]
    # print(f"每月策略调度模型::save_demand_load_df.shape: {save_demand_load_df.shape}")
    # ele price res
    mask = (ele_price_df['time'] >= save_range_start) & (ele_price_df['time'] < save_range_end)
    save_ele_price_df = ele_price_df.loc[mask]
    # print(f"每月策略调度模型::save_ele_price_df.shape: {save_ele_price_df.shape}")
    # TODO results save
    # --------------
    # save_result_df.to_csv(f"./data/{exp_name}/{node_name}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{ratio}.csv")
    # save_demand_load_df.to_csv(f"./data/{exp_name}/{node_name}/opt_result/demand_load.csv")
    # save_ele_price_df.to_csv(f"./data/{exp_name}/{node_name}/opt_result/ele_price.csv")
    # print(f"每月策略调度模型::month_num-route-ratio: {month_num:02d}-{route}-{ratio} end...\n{'='*80}")


def simul_one_process(month_num, ratio, route, devices_info, demand_load_df, ele_price_df, exp_name, node_name, ratio_result_list):
    # print(f"{'='*80}\n需量突破收益测算::month_num-route-ratio: {month_num:02d}-{route}-{ratio} start...")
    # strategy
    # --------------
    strategy_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{ratio}.csv")
    strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
    strategy_df['time'] = pd.to_datetime(strategy_df['time'])
    strategy_df.set_index('time', inplace=True)
    # print(f"\n需量突破收益测算::strategy_df.head(): \n{strategy_df.head()} \nstrategy_df.shape: {strategy_df.shape}")
    # simulation
    # --------------
    simulation_model = EssSimulationModel(devices_info[0])
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(demand_load_df, strategy_df, last_soc=0)
    origin_balance, opt_balance = simulation_model.revenue_calculation(demand_load_df, es_charge_df, ele_price_df, max_demand_price=38.4)
    ratio_result_list.append((ratio, origin_balance - opt_balance))
    # print(f"需量突破收益测算::突破比例: {ratio}%, 收益为: {origin_balance - opt_balance}")
    # print(f"需量突破收益测算::month_num-route-ratio: {month_num:02d}-{route}-{ratio} end...\n{'='*80}")

    return ratio_result_list


def run(month_num, exp_name, route, devices_info):
    # ##############################
    # experiment
    # ##############################
    # print(f"{'='*120}\nroute: {route} start...")
    #! ------------------------------------------
    #! for 循环
    #! ------------------------------------------
    # for month_num in range(5, 6):
    # ------------------------------
    # params 
    # ------------------------------
    node_name = f"route_{route}_{month_num:02d}"
    
    
    # ############################################################
    # 每个月调度策略
    # ############################################################
    # print(f"{'='*100}\n每月策略调度模型::month_num-route: {month_num:02d}-{route} start...")
    # ------------------------------
    # data(12 month)
    # ------------------------------
    # demand load
    demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load.csv")
    demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
    # print(f"\n每月策略调度模型::demand_load_df.head(): \n{demand_load_df.head()} \ndemand_load_df.shape: {demand_load_df.shape}")
    # ele price
    ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
    ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])
    # print(f"\n每月策略调度模型::ele_price_df.head(): \n{ele_price_df.head()} \nele_price_df.shape: {ele_price_df.shape}")
    # ------------------------------
    # 模型
    # ------------------------------
    #! ------------------------------------------
    #! for 循环
    #! ------------------------------------------
    #TODO method 1
    # for ratio in range(10, 240, 10):
    #     optim_one_process(month_num, ratio, route, devices_info, demand_load_df, ele_price_df)
    #TODO method 2
    mp_input_list_ratio = [(month_num, ratio, route, devices_info, demand_load_df, ele_price_df) for ratio in range(10, 240, 10)]
    with mp.Pool(processes=16) as pool:
        pool.starmap(optim_one_process, mp_input_list_ratio)
    
    # print(f"每月策略调度模型::month_num-route: {month_num:02d}-{route} end...\n{'='*100}") 
    
    
    # ############################################################
    # 需量突破比例的收益测算
    # ############################################################
    # print(f"{'='*100}\n需量突破收益测算::month_num-route: {month_num:02d}-{route} start...")
    # ------------------------------
    # data(12 month)
    # ------------------------------
    # demand load
    demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/demand_load.csv")
    demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
    demand_load_df.set_index('time', inplace=True)
    # print(f"\n需量突破收益测算::demand_load_df.head(): \n{demand_load_df.head()} \ndemand_load_df.shape: {demand_load_df.shape}")
    # ele price
    ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/ele_price.csv")
    ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])
    ele_price_df.set_index('time', inplace=True)
    # print(f"\n需量突破收益测算::ele_price_df.head(): \n{ele_price_df.head()} \nele_price_df.shape: {ele_price_df.shape}")
    # ------------------------------
    # 不同需量突破比例的收益测算
    # ------------------------------
    ratio_result_list = []
    #! ------------------------------------------
    #! for 循环
    #! ------------------------------------------
    #TODO method 1
    # for ratio in range(10, 240, 10):
    #     ratio_result_list = simul_one_process(month_num, ratio, route, devices_info, demand_load_df, ele_price_df, exp_name, node_name, ratio_result_list)
    #TODO method 2
    mp_input_list_ratio = [(month_num, ratio, route, devices_info, demand_load_df, ele_price_df, exp_name, node_name, ratio_result_list) for ratio in range(10, 240, 10)]
    with mp.Pool(processes=16) as pool:
        ratio_result_list = pool.starmap(simul_one_process, mp_input_list_ratio)
    ratio_result_list_v2 = []
    for item in ratio_result_list:
        ratio_result_list_v2.append(item[0])
    print(f"ratio_result_list_v2: \n{ratio_result_list_v2}")
    # max ratio
    max_ratio_tuple = max(ratio_result_list_v2, key=lambda x: x[1])
    max_ratio = max_ratio_tuple[0]
    print(f"需量突破收益测算::max_ratio: {max_ratio}")
    # ------------------------------
    # 测算
    # ------------------------------
    # strategy
    # --------------
    strategy_df = pd.read_csv(f"./data/{exp_name}/{node_name}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{max_ratio}.csv")
    strategy_df.rename(columns={"power_opt": "value"}, inplace=True)
    strategy_df['time'] = pd.to_datetime(strategy_df['time'])
    strategy_df.set_index('time', inplace=True)
    print(f"需量突破收益测算::strategy_df.head(): \n{strategy_df.head()} \nstrategy_df.shape: {strategy_df.shape}")
    # simulation
    # --------------
    simulation_model = EssSimulationModel(devices_info[0])
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(demand_load_df, strategy_df, 0)
    origin_balance, opt_balance = simulation_model.revenue_calculation(demand_load_df, es_charge_df, ele_price_df, 38.4)
    print("需量突破收益测算::测算方式一  收益：", (origin_balance - opt_balance), "收益占比：", (origin_balance - opt_balance) / origin_balance)
    # result
    # --------------
    ori_max_demand = demand_load_df["value"].max()
    opt_max_demand = total_load_df["total_load"].max()
    print("需量突破收益测算::调度后最大需量：", opt_max_demand, "原始最大需量：", ori_max_demand, "需量抬升成本", (opt_max_demand - ori_max_demand) * 38.4)
    print(f"需量突破收益测算::month_num-route: {month_num:02d}-{route} end...\n{'='*100}")
    
    print(f"route: {route} end...\n{'='*120}")
    
    return max_ratio, strategy_df




# 测试代码 main 函数
def main():
    # experiment params
    exp_name = "estimate830"
    route = "A"
    # Energy Storage params
    devices_info = [{
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
    }]
    
    # input
    mp_input_list_month = [(month_num, exp_name, route, devices_info) for month_num in [5, 7, 8, 9]]
    
    # run
    s_time = time.time()
    for mp_input in mp_input_list_month:
        # max_ratio, strategy_df = run(mp_input[0], mp_input[1], mp_input[2], mp_input[3])
        run(mp_input[0], mp_input[1], mp_input[2], mp_input[3])
    print(f"total_time: {time.time() - s_time}")

    # run
    # s_time = time.time()
    # with mp.Pool(processes=6) as pool:
    #     # res = pool.starmap(run, mp_input_list_month)
    #     pool.starmap(run, mp_input_list_month)
    # print(f"total_time: {time.time() - s_time}")

    # print(f"res: \n{res}")
    # print(f"res[0][0]: {res[0][0]} \nres[0][1]: \n{res[0][1]}")
    # print(f"res[1][0]: {res[1][0]} \nres[1][1]: \n{res[1][1]}")
    # print(f"res[2][0]: {res[2][0]} \nres[2][1]: \n{res[2][1]}")
    # print(f"res[3][0]: {res[3][0]} \nres[3][1]: \n{res[3][1]}")

if __name__ == "__main__":
    main()
