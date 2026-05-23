from typing import List, Dict

import numpy as np
import pandas as pd


class EsArbitraryRangeScheduler_withMaxDemand:

    def __init__(self,
                 schedule_time_range: List,
                 demand_load: List,
                 ele_prices: List,
                 ele_types: List,
                 pv_load: List,
                 devices_info: List[Dict],
                 current_soc_list: List[float],
                 max_demand_price: float,
                 freq_minutes: int,
                 pv_sell_price: float = 0.319438,
                 smooth_penalty_weight: float = 1e-4,
                 discharge_priority_weight: float = 1e-6,
                 charge_target_penalty_weight: float = 10.0,
                 discharge_target_penalty_weight: float = 10.0,
                 noon_pv_to_grid_penalty_weight: float = 10.0):
        self.schedule_time_range = pd.to_datetime(schedule_time_range)
        self.schedule_time_length = len(self.schedule_time_range)
        self.demand_load = np.array(demand_load, dtype=float)
        self.ele_prices = np.array(ele_prices, dtype=float)
        self.ele_types = ele_types
        self.pv_load = np.array(pv_load, dtype=float)
        self.devices_num = len(devices_info)
        self.current_soc_list = np.array(current_soc_list, dtype=float)
        self.transform_capacity_list = [i["transform_capacity"] for i in devices_info]
        self.charge_loss_list = [i["charge_loss"] for i in devices_info]
        self.discharge_loss_list = [i["discharge_loss"] for i in devices_info]
        self.es_charge_max_list = [i["es_charge_max"] for i in devices_info]
        self.es_capacity_max_list = [i["es_capacity_max"] * i["usable_depth"] for i in devices_info]
        self.es_capacity_min_list = [i["es_capacity_min"] for i in devices_info]
        self.max_demand_price = max_demand_price
        self.freq_minutes = freq_minutes
        self.pv_sell_price = pv_sell_price
        self.smooth_penalty_weight = smooth_penalty_weight
        self.discharge_priority_weight = discharge_priority_weight
        self.charge_target_penalty_weight = charge_target_penalty_weight
        self.discharge_target_penalty_weight = discharge_target_penalty_weight
        self.noon_pv_to_grid_penalty_weight = noon_pv_to_grid_penalty_weight

        if not (self.schedule_time_length == len(self.demand_load) == len(self.ele_prices) == len(self.pv_load)):
            raise ValueError("time, demand_load, ele_prices, and pv_load must have the same length")

    @staticmethod
    def _charge_allowed(ts: pd.Timestamp) -> bool:
        return (0 <= ts.hour < 6) or (12 <= ts.hour < 14)

    @staticmethod
    def _discharge_allowed(ts: pd.Timestamp) -> bool:
        return (6 <= ts.hour < 12) or (16 <= ts.hour < 24)

    @staticmethod
    def _build_discharge_rule(schedule_time_range: List, ele_types: List) -> tuple[np.ndarray, np.ndarray]:
        time_range = pd.to_datetime(schedule_time_range)
        allowed = np.array([
            EsArbitraryRangeScheduler_withMaxDemand._discharge_allowed(ts)
            for ts in time_range
        ], dtype=bool)
        return allowed, np.zeros(len(time_range), dtype=float)

    @staticmethod
    def _build_discharge_allowed_mask(schedule_time_range: List, ele_types: List) -> np.ndarray:
        allowed, _ = EsArbitraryRangeScheduler_withMaxDemand._build_discharge_rule(
            schedule_time_range,
            ele_types,
        )
        return allowed

    def _build_daily_soc_target_indices(self) -> tuple[list[int], list[int]]:
        charge_target_indices = []
        discharge_target_indices = []
        indexed_times = pd.Series(range(self.schedule_time_length), index=self.schedule_time_range)

        for _, day_indices in indexed_times.groupby(indexed_times.index.normalize()):
            day_times = day_indices.index
            for start_hour, end_hour in ((0, 6), (12, 14)):
                mask = (day_times.hour >= start_hour) & (day_times.hour < end_hour)
                if mask.any():
                    charge_target_indices.append(int(day_indices.loc[mask].iloc[-1]))
            for start_hour, end_hour in ((6, 12), (16, 24)):
                mask = (day_times.hour >= start_hour) & (day_times.hour < end_hour)
                if mask.any():
                    discharge_target_indices.append(int(day_indices.loc[mask].iloc[-1]))

        return charge_target_indices, discharge_target_indices

    def modeling2solving(self):
        time_ratio = self.freq_minutes / 60

        charge_eff = np.array(self.charge_loss_list, dtype=float)
        discharge_eff = np.array(self.discharge_loss_list, dtype=float)
        charge_max = np.array(self.es_charge_max_list, dtype=float)
        soc_max = np.array(self.es_capacity_max_list, dtype=float)
        soc_min = np.array(self.es_capacity_min_list, dtype=float)
        device_soc = np.clip(self.current_soc_list.astype(float), soc_min, soc_max)

        pv_to_load = np.minimum(self.pv_load, self.demand_load)
        pv_to_battery = np.zeros(self.schedule_time_length, dtype=float)
        pv_to_grid = np.maximum(self.pv_load - pv_to_load, 0.0)
        grid_to_load = np.maximum(self.demand_load - pv_to_load, 0.0)
        grid_to_battery = np.zeros(self.schedule_time_length, dtype=float)
        battery_charge = np.zeros(self.schedule_time_length, dtype=float)
        battery_discharge = np.zeros(self.schedule_time_length, dtype=float)
        soc = np.zeros(self.schedule_time_length, dtype=float)

        for j, ts in enumerate(self.schedule_time_range):
            if self._charge_allowed(ts):
                charge_power_by_device = np.minimum(
                    charge_max,
                    np.maximum((soc_max - device_soc) / (charge_eff * time_ratio), 0.0),
                )
                battery_charge[j] = float(charge_power_by_device.sum())
                grid_to_battery[j] = battery_charge[j]
                device_soc = np.minimum(
                    soc_max,
                    device_soc + charge_power_by_device * time_ratio * charge_eff,
                )
            elif self._discharge_allowed(ts):
                remaining_load = grid_to_load[j]
                discharge_power_by_device = np.zeros(self.devices_num, dtype=float)
                for i in range(self.devices_num):
                    device_available_power = min(
                        charge_max[i],
                        max((device_soc[i] - soc_min[i]) * discharge_eff[i] / time_ratio, 0.0),
                        remaining_load,
                    )
                    discharge_power_by_device[i] = device_available_power
                    remaining_load -= device_available_power
                    if remaining_load <= 1e-9:
                        break
                battery_discharge[j] = float(discharge_power_by_device.sum())
                grid_to_load[j] = max(grid_to_load[j] - battery_discharge[j], 0.0)
                device_soc = np.maximum(
                    soc_min,
                    device_soc - discharge_power_by_device * time_ratio / discharge_eff,
                )
            soc[j] = float(device_soc.sum())

        return {
            "pv_to_load": pv_to_load,
            "pv_to_battery": pv_to_battery,
            "pv_to_grid": pv_to_grid,
            "grid_to_load": grid_to_load,
            "grid_to_battery": grid_to_battery,
            "battery_charge": battery_charge,
            "battery_discharge": battery_discharge,
            "grid_import": grid_to_load + grid_to_battery,
            "soc": soc,
        }

    def schedule_generate(self, solution):
        battery_charge = np.asarray(solution["battery_charge"])
        battery_discharge = np.asarray(solution["battery_discharge"])
        value = np.around(battery_discharge - battery_charge, decimals=3)
        value = np.where(np.abs(value) < 0.1, 0.0, value)

        schedule_df = pd.DataFrame(
            {
                "value": value,
                "pv_to_load": solution["pv_to_load"],
                "pv_to_battery": solution["pv_to_battery"],
                "pv_to_grid": solution["pv_to_grid"],
                "grid_to_load": solution["grid_to_load"],
                "grid_to_battery": solution["grid_to_battery"],
                "battery_charge": battery_charge,
                "battery_discharge": battery_discharge,
                "grid_import": solution["grid_import"],
                "soc": solution["soc"],
                "net_load_after_dispatch": solution["grid_import"],
            },
            index=self.schedule_time_range,
        )

        for col in schedule_df.columns:
            schedule_df[col] = np.where(np.abs(schedule_df[col]) < 1e-6, 0.0, schedule_df[col])

        return [schedule_df]

    def run(self):
        solution = self.modeling2solving()
        return self.schedule_generate(solution)
