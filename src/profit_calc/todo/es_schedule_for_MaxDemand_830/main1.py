import sys
from pathlib import Path
ROOT = str(Path.cwd())
if ROOT not in sys.path:
    sys.path.append(ROOT)
import copy
import time
import calendar
from typing import Dict

import pandas as pd
import numpy as np
import cvxpy as cp

# from model import BaseModelMainClass
# from api.profit_simulation.schemas.base import ProjectStatus
from model.model_packages.ProfitSimulation_WithMaxDemand.utils.time_process import generate_hourly_datetime_pairs, get_month_range
# from utils.cache import cache
from utils.log_util import logger


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


class ModelMainClass:#(BaseModelMainClass):
    
    def __init__(self, project, model, node, args: Dict) -> None:
        self.project = project
        self.model = model
        self.node = node
        self.args = args
    
    def preprocess_data(self, raw_df: pd.DataFrame, column_name: str="time", new_column_name: str="time", set_index: bool=False, rename: bool=False):
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
    
    def optimization(self, input_data, model_cfgs, devices_info, month_num):
        # ############################################################
        # 每个月调度策略
        # ############################################################
        logger.info(f"{'='*100}")
        logger.info(f"每月策略调度模型::month_num: {month_num:02d} start...")
        # ------------------------------
        # data(2 month)
        # ------------------------------
        # demand load
        demand_load_df = self.preprocess_data(input_data["history"]["demand_load"][f"month_{month_num:02d}"])
        logger.info(f"每月策略调度模型::demand_load_df.head(): \n{demand_load_df.head()} \ndemand_load_df.shape: {demand_load_df.shape}")
        # ele price
        ele_price_df = self.preprocess_data(input_data["history"]["ele_price"][f"month_{month_num:02d}"])
        logger.info(f"每月策略调度模型::ele_price_df.head(): \n{ele_price_df.head()} \nele_price_df.shape: {ele_price_df.shape}")
        # max demand control line
        max_demand_control_line = demand_load_df["value"].max()
        # ------------------------------
        # time periods
        # ------------------------------
        validation_day_list = generate_hourly_datetime_pairs(2025, month_num, 22)
        # ------------------------------
        # 模型
        # ------------------------------
        for ratio in range(model_cfgs["ratio_min"], model_cfgs["ratio_max"], 10):
            logger.info(f"{'='*80}")
            logger.info(f"每月策略调度模型::month_num-ratio: {month_num:02d}-{ratio} start...")
            # daily strategy
            # --------------
            days_strategy_list = []
            for time_pair in validation_day_list:
                logger.info(f"{'='*60}")
                logger.info(f"每月策略调度模型::month_num-ratio-time_pair: {month_num:02d}-{ratio}-{time_pair} start...")
                # calc max demand control line
                # --------------
                # chunk time
                vs_time, ve_time = time_pair[0], time_pair[1]
                # chunk demand load
                mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
                step_demand_load_df = demand_load_df.loc[mask]
                logger.info(f"每月策略调度模型::step_demand_load_df.shape: {step_demand_load_df.shape}")
                # chunk ele price
                mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
                step_ele_price_df = ele_price_df.loc[mask]
                logger.info(f"每月策略调度模型::step_ele_price_df.shape: {step_ele_price_df.shape}")
                # max demand control line
                max_demand_control_line_i = max_demand_control_line * (1 + ratio / 1000)
                logger.info(f"每月策略调度模型::ratio_break: {(ratio / 1000) * 100}% max_demand_control_line_i: {max_demand_control_line_i}")
                # scheduler
                # --------------
                scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(
                    schedule_time_range=step_demand_load_df["time"].to_list(), 
                    demand_load=step_demand_load_df["value"].to_list(), 
                    ele_prices=step_ele_price_df["value"].to_list(), 
                    ele_types=step_ele_price_df["type"].to_list(),
                    devices_info=devices_info,
                    current_soc_list=[0],
                    max_demand_line=max_demand_control_line_i,
                    is_slow_charge=False,
                )
                opt_list = scheduler_model.run()
                days_strategy_list.append(opt_list[0])
                logger.info(f"每月策略调度模型::month_num-ratio-time_pair: {month_num:02d}-{ratio}-{time_pair} end...")
            # result collect
            # --------------
            result_df = pd.concat(days_strategy_list)
            # result_df["time"] = result_df.index
            result_df.index.name = "time"
            result_df.reset_index(inplace=True, drop=False)
            # one month result process
            # --------------
            # time params
            save_range_start, save_range_end = get_month_range(month_num, 2025)
            logger.info(f"每月策略调度模型::save_range_start: {save_range_start}, save_range_end: {save_range_end}")
            # result filter
            mask = (result_df['time'] >= save_range_start) & (result_df['time'] < save_range_end)
            save_result_df = result_df.loc[mask]
            logger.info(f"每月策略调度模型::save_result_df: \n{save_result_df}")
            logger.info(f"每月策略调度模型::save_result_df.shape: {save_result_df.shape}")
            # demand load res
            mask = (demand_load_df['time'] >= save_range_start) & (demand_load_df['time'] < save_range_end)
            save_demand_load_df = demand_load_df.loc[mask]
            logger.info(f"每月策略调度模型::save_demand_load_df: \n{save_demand_load_df}")
            logger.info(f"每月策略调度模型::save_demand_load_df.shape: {save_demand_load_df.shape}")
            # ele price res
            mask = (ele_price_df['time'] >= save_range_start) & (ele_price_df['time'] < save_range_end)
            save_ele_price_df = ele_price_df.loc[mask]
            logger.info(f"每月策略调度模型::save_ele_price_df: \n{save_ele_price_df}")
            logger.info(f"每月策略调度模型::save_ele_price_df.shape: {save_ele_price_df.shape}")
            
            # TODO results save
            # --------------
            save_result_df.to_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{ratio}.csv", encoding="utf-8", index=False)
            save_demand_load_df.to_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/opt_result/demand_load.csv")
            save_ele_price_df.to_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/opt_result/ele_price.csv")
            logger.info(f"每月策略调度模型::month_num-ratio: {month_num:02d}-{ratio} end...")
            logger.info(f"{'='*80}")
        logger.info(f"每月策略调度模型::month_num: {month_num:02d} end...")
        logger.info(f"{'='*100}")

    def simulation(self, input_data, model_cfgs, devices_info, month_num):
        # ############################################################
        # 需量突破比例的收益测算
        # ############################################################
        logger.info(f"{'='*100}")
        logger.info(f"需量突破收益测算::month_num: {month_num:02d} start...")
        # ------------------------------
        # data(12 month)
        # ------------------------------
        # demand load
        demand_load_df = self.preprocess_data(input_data["future"]["demand_load"][f"month_{month_num:02d}"], set_index=True)
        logger.info(f"需量突破收益测算::demand_load_df.head(): \n{demand_load_df.head()} \ndemand_load_df.shape: {demand_load_df.shape}")
        # ele price
        ele_price_df = self.preprocess_data(input_data["future"]["ele_price"][f"month_{month_num:02d}"], set_index=True)
        logger.info(f"需量突破收益测算::ele_price_df.head(): \n{ele_price_df.head()} \nele_price_df.shape: {ele_price_df.shape}")
        # ------------------------------
        # 不同需量突破比例的收益测算
        # ------------------------------
        ratio_result_list = []
        for ratio in range(model_cfgs["ratio_min"], model_cfgs["ratio_max"], 10):
            logger.info(f"{'='*80}")
            logger.info(f"需量突破收益测算::month_num-ratio: {month_num:02d}-{ratio} start...")
            # strategy
            # --------------
            strategy_df = self.preprocess_data(input_data["future"]["strategy"][f"month_{month_num:02d}"][ratio], set_index=True, rename=True)
            logger.info(f"需量突破收益测算::strategy_df.head(): \n{strategy_df.head()} \nstrategy_df.shape: {strategy_df.shape}")
            # simulation
            # --------------
            simulation_model = EssSimulationModel(devices_info[0])
            es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(demand_load_df, strategy_df, last_soc=0)
            origin_balance, opt_balance = simulation_model.revenue_calculation(demand_load_df, es_charge_df, ele_price_df, max_demand_price=38.4)
            ratio_result_list.append((ratio, origin_balance - opt_balance))
            logger.info(f"需量突破收益测算::突破比例: {(ratio / 1000) * 100}%, 收益为: {origin_balance - opt_balance}")
            logger.info(f"需量突破收益测算::month_num-ratio: {month_num:02d}-{ratio} end...")
            logger.info(f"{'='*80}")
        # max ratio
        max_ratio_tuple = max(ratio_result_list, key=lambda x: x[1])
        max_ratio = max_ratio_tuple[0]
        logger.info(f"需量突破收益测算::max_ratio: {(max_ratio / 1000) * 100}")
        # ------------------------------
        # 测算
        # ------------------------------
        # strategy
        # --------------
        strategy_df = self.preprocess_data(input_data["future"]["strategy"][f"month_{month_num:02d}"][max_ratio], set_index=True, rename=True)
        logger.info(f"需量突破收益测算::strategy_df.head(): \n{strategy_df.head()} \nstrategy_df.shape: {strategy_df.shape}")
        # simulation
        # --------------
        simulation_model = EssSimulationModel(devices_info[0])
        es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(demand_load_df, strategy_df, 0)
        origin_balance, opt_balance = simulation_model.revenue_calculation(demand_load_df, es_charge_df, ele_price_df, max_demand_price=38.4)
        logger.info(f"需量突破收益测算::测算方式一  收益：{(origin_balance - opt_balance)}, 收益占比：{(origin_balance - opt_balance) / origin_balance}")
        # result
        # --------------
        # TODO
        ori_max_demand = demand_load_df["value"].max()
        opt_max_demand = total_load_df["total_load"].max()
        logger.info(f"需量突破收益测算::调度后最大需量：{opt_max_demand}, 原始最大需量：{ori_max_demand}, 需量抬升成本: {(opt_max_demand - ori_max_demand) * 38.4}")
        logger.info(f"需量突破收益测算::month_num: {month_num:02d} end...")
        logger.info(f"{'='*100}")
        
        return max_ratio, strategy_df

    def run(self, input_data: Dict, model_cfgs: Dict):
        # experiment params
        devices_info = model_cfgs["devices_info"]

        # output collector
        break_ratio_list = []
        strategy_df_all = []

        # experiment
        logger.info(f"{'='*120}")
        logger.info(f"model start...")
        for month_num in range(5, 6):
            # policy optimization
            self.optimization(input_data, model_cfgs, devices_info, month_num)
            
            for month_num in [5]:
                demand_load_df = pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/opt_result/demand_load.csv")
                ele_price_df = pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/opt_result/ele_price.csv")
                input_data["future"]["demand_load"][f"month_{month_num:02d}"] = demand_load_df
                input_data["future"]["ele_price"][f"month_{month_num:02d}"] = ele_price_df
                for ratio in range(model_cfgs["ratio_min"], model_cfgs["ratio_max"], 10):
                    df_strategy = pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_{month_num:02d}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{ratio}.csv")
                    input_data["future"]["strategy"][f"month_{month_num:02d}"][ratio] = df_strategy
            
            # simulation
            max_break_ratio, strategy_df = self.simulation(input_data, model_cfgs, devices_info, month_num)
            # result collect
            break_ratio_list.append(max_break_ratio)
            strategy_df_all.append(strategy_df)
        logger.info(f"model end...")
        logger.info(f"{'='*120}")
        
        # output
        output_dict = {}
        output_dict["policy"] = strategy_df_all
        output_dict["ratio"] = break_ratio_list
        return {"output_dict": output_dict}




# 测试代码 main 函数
def main():
    # ##############################
    # model_cfgs
    # ##############################
    model_cfgs = {
        # experiment params
        "exp_name": "estimate830",
        "route": "A",
        "ratio_min": 10, 
        "ratio_max": 240,
        # Energy Storage params
        "devices_info": [{
            "transform_capacity": 63000,  # 变压器容量
            "invertband": 0,              # 防逆流功率
            "soc_redundant_ratio": 0,     # 保电比例
            "usable_depth": 0.9,          # 可用深度
            "charge_loss": 0.92,          # 充电效率
            "discharge_loss": 0.95,       # 放电效率
            "es_charge_max": 8920,        # 最大功率
            "es_charge_min": -8920,       # 最大功率
            "es_capacity_max": 17888,     # 设计容量
            "es_capacity_min": 0,         # 设计容量
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
        "history": {
            "demand_load": {
                # "month_01": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_01/demand_load.csv"),
                # "month_02": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_02/demand_load.csv"),
                # "month_03": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_03/demand_load.csv"),
                # "month_04": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_04/demand_load.csv"),
                "month_05": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/demand_load.csv"),
                # "month_06": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_06/demand_load.csv"),
                # "month_07": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_07/demand_load.csv"),
                # "month_08": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_08/demand_load.csv"),
                # "month_09": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_09/demand_load.csv"),
                # "month_10": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_10/demand_load.csv"),
                # "month_11": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_11/demand_load.csv"),
                # "month_12": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_12/demand_load.csv"),
            },
            "ele_price": {
                # "month_01": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_01/ele_price.csv"),
                # "month_02": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_02/ele_price.csv"),
                # "month_03": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_03/ele_price.csv"),
                # "month_04": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_04/ele_price.csv"),
                "month_05": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/ele_price.csv"),
                # "month_06": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_06/ele_price.csv"),
                # "month_07": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_07/ele_price.csv"),
                # "month_08": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_08/ele_price.csv"),
                # "month_09": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_09/ele_price.csv"),
                # "month_10": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_10/ele_price.csv"),
                # "month_11": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_11/ele_price.csv"),
                # "month_12": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_12/ele_price.csv"),
            },
        },
        "future": {
            "demand_load": {
                # "month_01": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_01/opt_result/demand_load.csv"),
                # "month_02": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_02/opt_result/demand_load.csv"),
                # "month_03": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_03/opt_result/demand_load.csv"),
                # "month_04": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_04/opt_result/demand_load.csv"),
                "month_05": None,
                # "month_06": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_06/opt_result/demand_load.csv"),
                # "month_07": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_07/opt_result/demand_load.csv"),
                # "month_08": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_08/opt_result/demand_load.csv"),
                # "month_09": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_09/opt_result/demand_load.csv"),
                # "month_10": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_10/opt_result/demand_load.csv"),
                # "month_11": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_11/opt_result/demand_load.csv"),
                # "month_12": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_12/opt_result/demand_load.csv"),
            },
            "ele_price": {
                # "month_01": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_01/opt_result/ele_price.csv"),
                # "month_02": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_02/opt_result/ele_price.csv"),
                # "month_03": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_03/opt_result/ele_price.csv"),
                # "month_04": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_04/opt_result/ele_price.csv"),
                # "month_05": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_05/opt_result/ele_price.csv"),
                "month_05": None,
                # "month_06": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_06/opt_result/ele_price.csv"),
                # "month_07": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_07/opt_result/ele_price.csv"),
                # "month_08": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_08/opt_result/ele_price.csv"),
                # "month_09": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_09/opt_result/ele_price.csv"),
                # "month_10": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_10/opt_result/ele_price.csv"),
                # "month_11": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_11/opt_result/ele_price.csv"),
                # "month_12": pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_12/opt_result/ele_price.csv"),
            },
            "strategy": {
                # "month_01": {},
                # "month_02": {},
                # "month_03": {},
                # "month_04": {},
                "month_05": {},
                # "month_06": {},
                # "month_07": {},
                # "month_08": {},
                # "month_09": {},
                # "month_10": {},
                # "month_11": {},
                # "month_12": {},
            },
        }
    }
    # for month_num in [5]:
    #     for ratio in range(model_cfgs["ratio_min"], model_cfgs["ratio_max"], 10):
    #         df_strategy = pd.read_csv(f"./data/{model_cfgs['exp_name']}/route_A_{month_num:02d}/opt_result/ratio_experiment_dod97/schedule_result_fixline_up{ratio}.csv")
    #         input_data["future"]["strategy"][f"month_{month_num:02d}"][ratio] = df_strategy
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
