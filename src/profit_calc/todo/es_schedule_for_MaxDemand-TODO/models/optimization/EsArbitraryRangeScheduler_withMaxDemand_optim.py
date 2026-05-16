import numpy as np
import pandas as pd
import cvxpy as cp


class EsArbitraryRangeScheduler_withMaxDemand:
    def __init__(self, 
                 schedule_time_range: list,
                 demand_load, 
                 ele_prices, 
                 ele_types, 
                 devices_info, 
                 current_soc_list,
                 max_demand_price):
        self.schedule_time_range = schedule_time_range
        self.schedule_time_length = len(self.schedule_time_range)
        self.demand_load = demand_load
        self.ele_prices = ele_prices
        self.ele_types = ele_types
        self.devices_num = len(devices_info)
        self.current_soc_list = current_soc_list
        self.charge_loss_list = [i["charge_loss"] for i in devices_info]
        self.discharge_loss_list = [i["discharge_loss"] for i in devices_info]
        self.es_charge_max_list = [i["es_charge_max"] for i in devices_info]
        self.es_discharge_max_list = [i["es_charge_min"] for i in devices_info]
        self.es_capacity_max_list = [i["es_capacity_max"] * i["usable_depth"] for i in devices_info]
        self.es_capacity_min_list = [i["es_capacity_min"] for i in devices_info]
        self.max_demand_price = max_demand_price
    
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
        lamda_dv = 0.0001
        lamda_v = 0.0001
        lamda_f = 0.0001
        lamda_p = -3 * lamda_v
        lamda_tp = 2 * lamda_p
        lamda_amortize = 0.001
        time_ratio = 60/60
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
        profit = time_ratio * (e_c_in_agg_vec + e_c_out_agg_vec) @ p - \
            self.max_demand_price * cp.max(d - e_c_in_agg_vec - e_c_out_agg_vec)

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
        # for j in range(column):
        #     if self.ele_types[j] == "谷":
        #         constraints += [e_c_out_agg_vec[j] == 0]
        #     elif self.ele_types[j] == "深谷":
        #         constraints += [e_c_out_agg_vec[j] == 0]
        #     elif self.ele_types[j] == "峰":
        #         constraints += [e_c_in_agg_vec[j] == 0]
        #     elif self.ele_types[j] == "尖峰":
        #         constraints += [e_c_in_agg_vec[j] == 0]
        #     elif self.ele_types[j] == "平":
        #         constraints += [e_c_out_agg_vec[j] == 0]

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
