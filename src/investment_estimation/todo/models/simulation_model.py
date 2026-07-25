from __future__ import annotations

import calendar
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(slots=True)
class BESSSimulationConfig:
    transform_capacity: float
    battery_capacity_kwh: float
    max_charge_power_kw: float
    max_discharge_power_kw: float
    charge_efficiency: float
    discharge_efficiency: float
    usable_depth: float = 1.0
    soc_redundant_ratio: float = 0.0
    invert_band_kw: float = 0.0

    @classmethod
    def from_legacy_dict(cls, energy_storage_system_config: dict[str, Any]) -> "BESSSimulationConfig":
        return cls(
            transform_capacity=float(energy_storage_system_config["transform_capacity"]),
            battery_capacity_kwh=float(energy_storage_system_config["es_capacity_max"]),
            max_charge_power_kw=abs(float(energy_storage_system_config["es_charge_min"])),
            max_discharge_power_kw=float(energy_storage_system_config["es_charge_max"]),
            charge_efficiency=float(energy_storage_system_config["charge_loss"]),
            discharge_efficiency=float(energy_storage_system_config["discharge_loss"]),
            usable_depth=float(energy_storage_system_config.get("usable_depth", 1.0)),
            soc_redundant_ratio=float(energy_storage_system_config.get("soc_redundant_ratio", 0.0)),
            invert_band_kw=float(energy_storage_system_config.get("invertband", 0.0)),
        )


class EssSimulationModel:
    """回放净功率策略，得到实际充放电、SOC 与成本相关时序。"""

    def __init__(
        self,
        energy_storage_system_config: BESSSimulationConfig | dict[str, Any],
        include_demand_charge: bool = False,
    ) -> None:
        if isinstance(energy_storage_system_config, dict):
            config = BESSSimulationConfig.from_legacy_dict(energy_storage_system_config)
        else:
            config = energy_storage_system_config
        self.config = config
        self.transform_capacity = config.transform_capacity
        self.invert_band = config.invert_band_kw
        self.battery_capacity = config.battery_capacity_kwh
        self.soh = config.usable_depth
        self.soc_redundant_ratio = config.soc_redundant_ratio
        self.max_charge_power = config.max_charge_power_kw
        self.max_discharge_power = config.max_discharge_power_kw
        self.charge_efficiency = config.charge_efficiency
        self.discharge_efficiency = config.discharge_efficiency
        self.include_demand_charge = include_demand_charge

    def one_step(self, time_lag: float, demand_load: float, command: float, soc: float):
        if command > 0:
            power = min(command, self.max_discharge_power)
            power = min(power, max(demand_load - self.invert_band, 0.0))
            inner_energy_vari = (power / self.discharge_efficiency) * time_lag
            soc_floor = self.battery_capacity * self.soc_redundant_ratio
            if (soc - inner_energy_vari) < soc_floor:
                if soc < soc_floor:
                    inner_energy_vari = 0.0
                else:
                    inner_energy_vari = soc - soc_floor
            power = (inner_energy_vari / time_lag) * self.discharge_efficiency if time_lag > 0 else 0.0
            soc = soc - inner_energy_vari
            return power, inner_energy_vari, soc

        if command < 0:
            power = max(command, -self.max_charge_power)
            if np.isfinite(self.transform_capacity):
                assert demand_load < self.transform_capacity, "wrong transformer capacity"
                if demand_load - power > self.transform_capacity:
                    power = -(self.transform_capacity - demand_load)
            inner_energy_vari = (power * self.charge_efficiency) * time_lag
            soc_ceiling = self.battery_capacity * self.soh
            if soc - inner_energy_vari > soc_ceiling:
                inner_energy_vari = -max(soc_ceiling - soc, 0.0)
            power = (inner_energy_vari / time_lag) / self.charge_efficiency if time_lag > 0 else 0.0
            soc = soc - inner_energy_vari
            return power, inner_energy_vari, soc

        return 0.0, 0.0, soc

    def simulation_process(
        self,
        demand_load: pd.DataFrame,
        es_strategy: pd.DataFrame,
        last_soc: float,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        es_charge_list: list[float] = []
        es_soc_list: list[float] = []
        es_charge_time_list: list[pd.Timestamp] = []
        es_soc_time_list: list[pd.Timestamp] = []

        time_i = None
        time_diff = None
        demand_load_i = None
        es_strategy_i = None
        soc_i = last_soc

        for index, row in demand_load.iterrows():
            if time_i is not None:
                time_diff = index - time_i
                time_diff_hour = time_diff.total_seconds() / 3600
                assert time_diff_hour > 0, "wrong time index sequence"
                es_charge_i, _, es_soc_i = self.one_step(
                    time_diff_hour, float(demand_load_i), float(es_strategy_i), float(soc_i)
                )
                soc_i = es_soc_i
                es_charge_list.append(es_charge_i)
                es_charge_time_list.append(time_i)
            es_soc_list.append(float(soc_i))
            es_soc_time_list.append(index)

            time_i = index
            demand_load_i = row["value"]
            es_strategy_i = es_strategy.loc[(es_strategy.index <= index)]["value"].iloc[-1]

        if time_diff is None:
            raise ValueError("simulation_process requires at least two time points")

        time_diff_hour = time_diff.total_seconds() / 3600
        es_charge_i, _, es_soc_i = self.one_step(
            time_diff_hour, float(demand_load_i), float(es_strategy_i), float(soc_i)
        )
        es_charge_list.append(es_charge_i)
        es_charge_time_list.append(time_i)

        soc_i = es_soc_i
        es_soc_list.append(float(soc_i))
        es_soc_time_list.append(time_i + time_diff)

        es_charge_df = pd.DataFrame({"value": es_charge_list}, index=es_charge_time_list)
        total_load_df = pd.DataFrame(
            {
                "total_load": np.array(demand_load["value"], dtype=float) - np.array(es_charge_list, dtype=float),
                "demand_load": demand_load["value"].astype(float).to_numpy(),
                "es_load": es_charge_list,
            },
            index=es_charge_time_list,
        )
        es_soc_df = pd.DataFrame({"value": es_soc_list}, index=es_soc_time_list)
        return es_charge_df, es_soc_df, total_load_df

    def revenue_calculation(
        self,
        demand_load: pd.DataFrame,
        es_load: pd.DataFrame,
        ele_price: pd.DataFrame,
        max_demand_price: float,
    ) -> tuple[float, float]:
        origin_balance = 0.0
        opt_balance = 0.0
        ori_max_load = 0.0
        opt_max_load = 0.0
        total_hours = 0.0
        time_i = None

        for index, row in demand_load.iterrows():
            if time_i is not None:
                time_diff = index - time_i
                time_diff_hour = time_diff.total_seconds() / 3600
                assert time_diff_hour > 0, "wrong time index sequence"

                origin_balance_i = float(demand_load_i) * time_diff_hour * float(ele_price_i)
                opt_balance_i = (float(demand_load_i) - float(es_load_i)) * time_diff_hour * float(ele_price_i)

                ori_max_load = max(ori_max_load, float(demand_load_i))
                opt_max_load = max(opt_max_load, float(demand_load_i) - float(es_load_i))

                origin_balance += origin_balance_i
                opt_balance += opt_balance_i
                total_hours += time_diff_hour

            time_i = index
            demand_load_i = row["value"]
            es_load_i = es_load.loc[index, "value"]
            ele_price_i = ele_price.loc[(ele_price.index <= index)]["value"].iloc[-1]

        time_diff_hour = time_diff.total_seconds() / 3600
        origin_balance_i = float(demand_load_i) * time_diff_hour * float(ele_price_i)
        opt_balance_i = (float(demand_load_i) - float(es_load_i)) * time_diff_hour * float(ele_price_i)

        ori_max_load = max(ori_max_load, float(demand_load_i))
        opt_max_load = max(opt_max_load, float(demand_load_i) - float(es_load_i))

        origin_balance += origin_balance_i
        opt_balance += opt_balance_i
        total_hours += time_diff_hour

        if self.include_demand_charge:
            time_diff_month = total_hours / 24 / calendar.monthrange(time_i.year, time_i.month)[1]
            origin_balance += max_demand_price * ori_max_load * time_diff_month
            opt_balance += max_demand_price * opt_max_load * time_diff_month

        return origin_balance, opt_balance
