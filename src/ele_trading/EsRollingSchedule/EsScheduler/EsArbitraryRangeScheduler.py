import pandas as pd
import numpy as np
import cvxpy as cp


class EsArbitraryRangeScheduler_withMaxDemand_evenCharge:
    # TODO 拆分设备参数和运行数据
    def __init__(self, 
                 schedule_time_range: list, 
                 demand_load, 
                 ele_prices, 
                 ele_types, 
                 devices_info, 
                 current_soc_list,
                 max_demand_price,
                 days_in_month):
        self.schedule_time_range = schedule_time_range
        self.schedule_time_length = len(self.schedule_time_range)
        
        self.demand_load = demand_load
        self.opt_length = len(demand_load)
        
        self.ele_prices = ele_prices
        self.ele_types = ele_types
        self.current_soc_list = current_soc_list
        # 储能电池设备参数
        self.devices_num = len(devices_info)
        self.charge_loss_list = [i["charge_loss"] for i in devices_info]
        self.discharge_loss_list = [i["discharge_loss"] for i in devices_info]
        self.es_charge_max_list = [i["es_charge_max"] for i in devices_info]
        self.es_discharge_max_list = [i["es_charge_min"] for i in devices_info]
        self.es_capacity_max_list = [i["es_capacity_max"] * i["usable_depth"] for i in devices_info]
        self.es_capacity_min_list = [i["es_capacity_max"] * i["soc_redundant_ratio"] for i in devices_info]
        # 需量电价, 单位：元/kW
        self.max_demand_price = max_demand_price
        self.days_in_month = days_in_month
    
    def modeling2solving(self):
        # ------------------------------
        # 模型参数
        # ------------------------------
        # 输入数据参数
        row = self.devices_num
        column = self.opt_length
        Tcut = self.schedule_time_length
        #设备参数
        c_l_in_vec = np.array(self.charge_loss_list).reshape((row, 1))
        c_l_out_vec = np.array(self.discharge_loss_list).reshape((row, 1))
        e_c_max_vec = np.array(self.es_charge_max_list).reshape((row, 1))
        e_c_min_vec = np.array(self.es_discharge_max_list).reshape((row, 1))
        e_s_max_vec = np.array(self.es_capacity_max_list).reshape((row, 1))
        e_s_min_vec = np.array(self.es_capacity_min_list).reshape((row, 1))
        #充放电模式参数
        lamda_dv = 0.0001  # 深谷
        lamda_v = 0.0001  # 谷时
        lamda_f = 0.0001  # 平时
        lamda_p = -3 * lamda_v  # 峰时
        lamda_tp = 2 * lamda_p  # 尖峰
        lamda_amortize = 0.01  # 缓充
        # 数据时间间隔(小时)
        time_ratio = 5/60
        # ------------------------------
        # 输入定量
        # ------------------------------
        d = np.array(self.demand_load)
        p = np.array(self.ele_prices)
        e_r_vec = np.array(self.current_soc_list)
        # ------------------------------
        # 定义决策变量
        # ------------------------------
        # 定义设备级变量
        e_c_in_matrix = cp.Variable((row, column))
        e_c_out_matrix = cp.Variable((row, column))
        soc_matrix = cp.Variable((row, column))
        # 定义节点级变量
        e_c_in_agg_vec = cp.sum(e_c_in_matrix, axis=0)
        e_c_out_agg_vec = cp.sum(e_c_out_matrix, axis=0)
        soc_agg_vec = cp.sum(soc_matrix, axis=0)
        # ------------------------------
        # 目标函数
        # ------------------------------
        profit = time_ratio * (e_c_in_agg_vec[:Tcut] + e_c_out_agg_vec[:Tcut]) @ p[:Tcut] + \
                 time_ratio * (e_c_in_agg_vec[Tcut:] + e_c_out_agg_vec[Tcut:]) @ p[Tcut:] * (self.days_in_month - 1) + \
                 self.max_demand_price * cp.min(e_c_in_agg_vec)
        profit = profit - lamda_amortize * cp.norm(e_c_in_agg_vec)

        for j in range(column):
            if self.ele_types[j] == "峰":
                profit = profit + lamda_p * soc_agg_vec[j]
            elif self.ele_types[j] == "尖峰":
                profit = profit + lamda_tp * soc_agg_vec[j]

        obj = cp.Maximize(profit)
        # ------------------------------
        # 设置约束条件
        # ------------------------------
        constraints = []
        # 充电功率和实时电量匹配
        for i in range(row):
            for j in range(0, Tcut):
                constraints += [soc_matrix[i, j] == e_r_vec[i] \
                                - cp.sum(e_c_in_matrix[i, :j+1]) * time_ratio * c_l_in_vec[i] \
                                - cp.sum(e_c_out_matrix[i, :j+1]) * time_ratio / c_l_out_vec[i]]
            for j in range(Tcut, column):
                constraints += [soc_matrix[i, j] == e_s_min_vec[i, 0] \
                                - cp.sum(e_c_in_matrix[i, Tcut:j+1]) * time_ratio * c_l_in_vec[i] \
                                - cp.sum(e_c_out_matrix[i, Tcut:j+1]) * time_ratio / c_l_out_vec[i]]
        # 放电功率小于需量
        constraints += [e_c_out_agg_vec <= d]
        # 储能系统每个时段的充放电功率限制
        constraints += [e_c_out_matrix <= e_c_max_vec]
        constraints += [e_c_out_matrix >= 0]
        constraints += [e_c_in_matrix <= 0]
        constraints += [e_c_in_matrix >= e_c_min_vec]
        # 储能器容量限制
        constraints += [soc_matrix >= e_s_min_vec]
        constraints += [soc_matrix <= e_s_max_vec]
        # 峰谷平时段充放电矫正
        for j in range(column):
            if self.ele_types[j] == "谷":
                constraints += [e_c_out_agg_vec[j] == 0]
            elif self.ele_types[j] == "深谷":
                constraints += [e_c_out_agg_vec[j] == 0]    
            elif self.ele_types[j] == "峰":
                constraints += [e_c_in_agg_vec[j] == 0]
            elif self.ele_types[j] == "尖峰":
                constraints += [e_c_in_agg_vec[j] == 0]
            elif self.ele_types[j] == "平":
                constraints += [e_c_out_agg_vec[j] == 0]
        # ------------------------------
        # 模型求解
        # ------------------------------
        prob = cp.Problem(obj, constraints)
        result = prob.solve(verbose = True, solver = cp.CLARABEL)
        
        return result, e_c_in_matrix.value, e_c_out_matrix.value
    
    def schedule_generate(self, charge_array, discharge_array):
        schedule_list = []
        for device_i in range(self.devices_num):
            # 充放电功率
            power_array_i = charge_array[device_i] + discharge_array[device_i]
            power_array_i = np.around(power_array_i, decimals=3)
            # 去除小于0.1的功率
            for j in range(len(power_array_i)):
                if abs(power_array_i[j]) < 0.1:
                    power_array_i[j] = 0
            # 生成调度策略输出结果
            schedule_i_df = pd.DataFrame({
                "power_opt": power_array_i[:self.schedule_time_length]
            }, index=self.schedule_time_range)
            # 储能器调度策略结果收集
            schedule_list.append(schedule_i_df)
        
        return schedule_list
    
    def run(self):
        profit, charge_array, discharge_array = self.modeling2solving()
        schedule_list = self.schedule_generate(charge_array, discharge_array)
        
        return schedule_list
