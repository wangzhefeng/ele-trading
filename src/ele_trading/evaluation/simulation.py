from __future__ import annotations

import calendar
import warnings

warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np


class EssSimulationModel:
    """储能仿真回放模型。

    回放优化策略，考虑物理约束（变压器容量、SOC、逆变器死区），
    计算实际执行的充放电功率和经济收益。
    """

    def __init__(
        self,
        transform_capacity: float,
        battery_capacity: float,
        soc_min: float = 0.0,
        soc_max: float | None = None,
        soc_redundant_ratio: float = 0.0,
        max_charge_power: float = 0.0,
        max_discharge_power: float = 0.0,
        charge_efficiency: float = 0.92,
        discharge_efficiency: float = 0.95,
        invert_band: float = 0.0,
        include_demand_charge: bool = False,
    ):
        self.transform_capacity = transform_capacity
        self.invert_band = invert_band
        self.battery_capacity = battery_capacity
        self.soc_max = soc_max if soc_max is not None else battery_capacity
        self.soc_redundant_ratio = soc_redundant_ratio
        self.max_charge_power = max_charge_power
        self.max_discharge_power = max_discharge_power
        self.charge_efficiency = charge_efficiency
        self.discharge_efficiency = discharge_efficiency
        self.include_demand_charge = include_demand_charge

    def one_step(self, time_lag: float, demand_load: float, command: float, soc: float):
        """单步仿真：根据指令和物理约束计算实际充放电功率。"""
        if command > 0:
            charge = command
            charge = min(charge, self.max_discharge_power)
            charge = min(charge, demand_load - self.invert_band)
            inner_energy_vari = (charge / self.discharge_efficiency) * time_lag
            if (soc - inner_energy_vari) < (self.battery_capacity * self.soc_redundant_ratio):
                if soc < (self.battery_capacity * self.soc_redundant_ratio):
                    inner_energy_vari = 0
                else:
                    inner_energy_vari = soc - (self.battery_capacity * self.soc_redundant_ratio)
            charge = (inner_energy_vari / time_lag) * self.discharge_efficiency
            soc = soc - inner_energy_vari
        elif command < 0:
            charge = command
            charge = max(charge, self.max_charge_power)
            assert demand_load < self.transform_capacity, "wrong transformer capacity"
            if demand_load - charge > self.transform_capacity:
                charge = -(self.transform_capacity - demand_load)
            inner_energy_vari = (charge * self.charge_efficiency) * time_lag
            if soc - inner_energy_vari > self.soc_max:
                inner_energy_vari = -(self.soc_max - soc)
            charge = (inner_energy_vari / time_lag) / self.charge_efficiency
            soc = soc - inner_energy_vari
        else:
            charge = 0
            inner_energy_vari = 0

        return charge, inner_energy_vari, soc

    def simulation_process(self, demand_load: pd.DataFrame, es_strategy: pd.DataFrame, last_soc: float):
        """回放策略，返回实际充放电、SOC、总负荷。"""
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
                time_diff = (index - time_i)
                time_diff_hour = time_diff.total_seconds() / (60 * 60)
                assert time_diff_hour > 0, "wrong time index sequence"
                es_charge_i, es_energy_i, es_soc_i = self.one_step(
                    time_diff_hour, demand_load_i, es_strategy_i, soc_i
                )
                soc_i = es_soc_i
                es_charge_list.append(es_charge_i)
                es_charge_time_list.append(time_i)
            es_soc_list.append(soc_i)
            es_soc_time_list.append(index)

            time_i = index
            demand_load_i = row["value"]
            es_strategy_i = es_strategy.loc[(es_strategy.index <= index)]["value"].iloc[-1]

        time_diff_hour = time_diff.total_seconds() / (60 * 60)
        es_charge_i, es_energy_i, es_soc_i = self.one_step(
            time_diff_hour, demand_load_i, es_strategy_i, soc_i
        )
        es_charge_list.append(es_charge_i)
        es_charge_time_list.append(time_i)

        soc_i = es_soc_i
        es_soc_list.append(soc_i)
        es_soc_time_list.append(time_i + time_diff)

        es_charge_df = pd.DataFrame({"value": es_charge_list}, index=es_charge_time_list)
        total_load_df = pd.DataFrame({
            "total_load": np.array(demand_load["value"]) - np.array(es_charge_list),
            "demand_load": demand_load["value"],
            "es_load": es_charge_list,
        }, index=es_charge_time_list)
        es_soc_df = pd.DataFrame({"value": es_soc_list}, index=es_soc_time_list)

        return es_charge_df, es_soc_df, total_load_df

    def revenue_calculation(
        self,
        demand_load: pd.DataFrame,
        es_load: pd.DataFrame,
        ele_price: pd.DataFrame,
        max_demand_price: float,
    ):
        """计算原始 vs 优化后的电费成本。"""
        origin_balance = 0
        opt_balance = 0
        ori_max_load = 0
        opt_max_load = 0
        total_hours = 0
        time_i = None

        for index, row in demand_load.iterrows():
            if time_i:
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

        time_diff_hour = time_diff.total_seconds() / (60 * 60)
        origin_balance_i = demand_load_i * time_diff_hour * ele_price_i
        opt_balance_i = (demand_load_i - es_load_i) * time_diff_hour * ele_price_i

        ori_max_load = max(ori_max_load, demand_load_i)
        opt_max_load = max(opt_max_load, demand_load_i - es_load_i)

        origin_balance = origin_balance + origin_balance_i
        opt_balance = opt_balance + opt_balance_i

        total_hours = total_hours + time_diff_hour

        if self.include_demand_charge:
            time_diff_month = total_hours / 24 / calendar.monthrange(time_i.year, time_i.month)[1]
            origin_balance += max_demand_price * ori_max_load * time_diff_month
            opt_balance += max_demand_price * opt_max_load * time_diff_month

        return origin_balance, opt_balance
