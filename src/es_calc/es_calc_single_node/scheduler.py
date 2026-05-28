from typing import List, Dict

import numpy as np
import pandas as pd
import cvxpy as cp

from .config import AlgorithmProfile


class EsArbitraryRangeScheduler:

    def __init__(
        self,
        schedule_time_range: List,
        demand_load: List,
        ele_prices: List,
        ele_types: List,
        devices_info: List[Dict],
        current_soc_list: List[float],
        max_demand_price: float,
        freq_minutes: int,
        profile: AlgorithmProfile,
        transform_capacity: float = 0.0,
    ):
        self.schedule_time_range = schedule_time_range
        self.schedule_time_length = len(self.schedule_time_range)
        self.demand_load = demand_load
        self.ele_prices = ele_prices
        self.ele_types = ele_types
        self.devices_num = len(devices_info)
        self.current_soc_list = current_soc_list
        self.max_demand_price = max_demand_price
        self.freq_minutes = freq_minutes
        self.profile = profile
        self.transform_capacity = transform_capacity

        if profile.demand_peak_guard_constraint:
            self.demand_load_max = max(demand_load)

        self.charge_loss_list = [i["charge_loss"] for i in devices_info]
        self.discharge_loss_list = [i["discharge_loss"] for i in devices_info]
        self.es_charge_max_list = [i["es_charge_max"] for i in devices_info]
        self.es_discharge_max_list = [i["es_charge_min"] for i in devices_info]
        self.es_capacity_max_list = [i["es_capacity_max"] * i["usable_depth"] for i in devices_info]
        self.es_capacity_min_list = [i["es_capacity_min"] for i in devices_info]

    def modeling2solving(self):
        row = self.devices_num
        column = self.schedule_time_length

        c_l_in_vec = np.array(self.charge_loss_list).reshape((row, 1))
        c_l_out_vec = np.array(self.discharge_loss_list).reshape((row, 1))
        e_c_max_vec = np.array(self.es_charge_max_list).reshape((row, 1))
        e_c_min_vec = np.array(self.es_discharge_max_list).reshape((row, 1))
        e_s_max_vec = np.array(self.es_capacity_max_list).reshape((row, 1))
        e_s_min_vec = np.array(self.es_capacity_min_list).reshape((row, 1))

        time_ratio = self.freq_minutes / 60

        d = np.array(self.demand_load)
        p = np.array(self.ele_prices)
        e_r_vec = np.array(self.current_soc_list)

        e_c_in_matrix = cp.Variable((row, column))
        e_c_out_matrix = cp.Variable((row, column))
        soc_matrix = cp.Variable((row, column))

        e_c_in_agg_vec = cp.sum(e_c_in_matrix, axis=0)
        e_c_out_agg_vec = cp.sum(e_c_out_matrix, axis=0)

        # 目标函数
        net_power = e_c_in_agg_vec + e_c_out_agg_vec
        energy_term = self.profile.objective_energy_multiplier * time_ratio * net_power @ p

        demand_term = 0.0
        if self.profile.demand_charge_type == "approx_min_charge":
            demand_term = self.max_demand_price * cp.min(e_c_in_agg_vec)
        elif self.profile.demand_charge_type == "exact_max_net":
            demand_term = -self.max_demand_price * cp.max(d - net_power)

        smoothing_term = 0.0
        if self.profile.smoothing_enabled:
            lamda_amortize = 0.001
            smoothing_term = -lamda_amortize * cp.norm(e_c_in_agg_vec)

        profit = energy_term + demand_term + smoothing_term
        obj = cp.Maximize(profit)

        # 约束条件
        constraints = []

        # SOC 动态约束
        for i in range(row):
            for j in range(column):
                constraints += [
                    soc_matrix[i, j] == e_r_vec[i]
                    - cp.sum(e_c_in_matrix[i, :j + 1]) * time_ratio * c_l_in_vec[i]
                    - cp.sum(e_c_out_matrix[i, :j + 1]) * time_ratio / c_l_out_vec[i]
                ]

        # 放电功率小于等于用电功率
        constraints += [e_c_out_agg_vec <= cp.pos(d)]

        # 需量峰值保护约束
        if self.profile.demand_peak_guard_constraint:
            constraints += [e_c_in_agg_vec >= cp.pos(d) - self.demand_load_max]

        # 变压器容量约束
        if self.profile.transformer_capacity_constraint:
            constraints += [d - e_c_in_agg_vec <= self.transform_capacity]

        # 充放电功率限制
        constraints += [e_c_out_matrix <= e_c_max_vec]
        constraints += [e_c_out_matrix >= 0]
        constraints += [e_c_in_matrix <= 0]
        constraints += [e_c_in_matrix >= e_c_min_vec]

        # 储能容量限制
        constraints += [soc_matrix >= e_s_min_vec]
        constraints += [soc_matrix <= e_s_max_vec]

        # 求解
        prob = cp.Problem(obj, constraints)
        result = prob.solve(verbose=False, solver=cp.CLARABEL)
        return result, e_c_in_matrix.value, e_c_out_matrix.value

    def schedule_generate(self, charge_array, discharge_array):
        schedule_list = []
        for device_i in range(self.devices_num):
            power_array_i = np.around(charge_array[device_i] + discharge_array[device_i], decimals=3)
            power_array_i = np.asarray(list(map(lambda x: 0.0 if abs(x) < 0.1 else x, power_array_i.tolist())))
            schedule_i_df = pd.DataFrame({
                "value": power_array_i
            }, index=self.schedule_time_range)
            schedule_list.append(schedule_i_df)
        return schedule_list

    def run(self):
        profit, charge_array, discharge_array = self.modeling2solving()
        schedule_list = self.schedule_generate(charge_array, discharge_array)
        return schedule_list
