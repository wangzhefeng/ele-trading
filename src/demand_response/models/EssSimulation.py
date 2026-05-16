# python libraries
import calendar

import pandas as pd
import numpy as np


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
            assert demand_load < self.transform_capacity, "wrong transformer capacity"
            if demand_load - charge > self.transform_capacity:
                charge = - (self.transform_capacity - demand_load)
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

        # 电价计算
        return origin_balance, opt_balance
