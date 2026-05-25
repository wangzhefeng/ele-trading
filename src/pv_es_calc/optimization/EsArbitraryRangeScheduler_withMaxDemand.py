from __future__ import annotations

from typing import Dict, List

import cvxpy as cp
import numpy as np
import pandas as pd


VERSION_PARAMETERS = {
    "v1": {
        "dispatch_mode": "lp",
        "noon_pv_to_battery_priority_weight": 0.0,
        "noon_pv_to_load_priority_weight": 0.0,
        "noon_pv_to_grid_priority_weight": 0.0,
        "noon_grid_to_battery_penalty_weight": 0.0,
        "noon_pv_to_grid_penalty_weight": 0.0,
    },
    "v2": {
        "dispatch_mode": "lp",
        "noon_pv_to_battery_priority_weight": 10.0,
        "noon_pv_to_load_priority_weight": 0.0,
        "noon_pv_to_grid_priority_weight": 0.0,
        "noon_grid_to_battery_penalty_weight": 0.0,
        "noon_pv_to_grid_penalty_weight": 0.0,
    },
    "v3": {
        "dispatch_mode": "lp",
        "noon_pv_to_battery_priority_weight": 0.0,
        "noon_pv_to_load_priority_weight": 10.0,
        "noon_pv_to_grid_priority_weight": 0.0,
        "noon_grid_to_battery_penalty_weight": 0.0,
        "noon_pv_to_grid_penalty_weight": 0.0,
    },
    "v4": {
        "dispatch_mode": "lp",
        "noon_pv_to_battery_priority_weight": 0.0,
        "noon_pv_to_load_priority_weight": 0.0,
        "noon_pv_to_grid_priority_weight": 0.0,
        "noon_grid_to_battery_penalty_weight": 0.0,
        "noon_pv_to_grid_penalty_weight": 10.0,
    },
    "v5": {
        "dispatch_mode": "rule",
        "noon_pv_to_battery_priority_weight": 0.0,
        "noon_pv_to_load_priority_weight": 0.0,
        "noon_pv_to_grid_priority_weight": 0.0,
        "noon_grid_to_battery_penalty_weight": 0.0,
        "noon_pv_to_grid_penalty_weight": 0.0,
    },
}


class EsArbitraryRangeScheduler_withMaxDemand:
    def __init__(
        self,
        schedule_time_range: List,
        demand_load: List,
        ele_prices: List,
        ele_types: List,
        pv_load: List,
        devices_info: List[Dict],
        current_soc_list: List[float],
        max_demand_price: float,
        freq_minutes: int,
        method_version: str = "v4",
        pv_sell_price: float = 0.319438,
        smooth_penalty_weight: float = 1e-4,
        discharge_priority_weight: float = 1e-6,
        charge_target_penalty_weight: float = 10.0,
        discharge_target_penalty_weight: float = 10.0,
        **version_weights,
    ):
        version_params = self.version_parameters(method_version)
        for key, value in version_weights.items():
            if key in version_params and value is not None:
                version_params[key] = value

        self.method_version = method_version
        self.dispatch_mode = version_params["dispatch_mode"]
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
        self.es_capacity_max_list = [
            i["es_capacity_max"] * i["usable_depth"] for i in devices_info
        ]
        self.es_capacity_min_list = [i["es_capacity_min"] for i in devices_info]
        self.max_demand_price = max_demand_price
        self.freq_minutes = freq_minutes
        self.pv_sell_price = pv_sell_price
        self.smooth_penalty_weight = smooth_penalty_weight
        self.discharge_priority_weight = discharge_priority_weight
        self.charge_target_penalty_weight = charge_target_penalty_weight
        self.discharge_target_penalty_weight = discharge_target_penalty_weight
        self.noon_pv_to_battery_priority_weight = version_params[
            "noon_pv_to_battery_priority_weight"
        ]
        self.noon_pv_to_load_priority_weight = version_params[
            "noon_pv_to_load_priority_weight"
        ]
        self.noon_pv_to_grid_priority_weight = version_params[
            "noon_pv_to_grid_priority_weight"
        ]
        self.noon_grid_to_battery_penalty_weight = version_params[
            "noon_grid_to_battery_penalty_weight"
        ]
        self.noon_pv_to_grid_penalty_weight = version_params[
            "noon_pv_to_grid_penalty_weight"
        ]

        if not (
            self.schedule_time_length
            == len(self.demand_load)
            == len(self.ele_prices)
            == len(self.pv_load)
        ):
            raise ValueError(
                "time, demand_load, ele_prices, and pv_load must have the same length"
            )

    @staticmethod
    def version_parameters(method_version: str) -> dict:
        if method_version not in VERSION_PARAMETERS:
            raise ValueError(f"unsupported method_version: {method_version}")
        return dict(VERSION_PARAMETERS[method_version])

    @staticmethod
    def _charge_allowed(ts: pd.Timestamp) -> bool:
        return (0 <= ts.hour < 6) or (12 <= ts.hour < 14)

    @staticmethod
    def _discharge_allowed(ts: pd.Timestamp) -> bool:
        return (6 <= ts.hour < 12) or (16 <= ts.hour < 24)

    @staticmethod
    def _build_discharge_rule(
        schedule_time_range: List, ele_types: List
    ) -> tuple[np.ndarray, np.ndarray]:
        time_range = pd.to_datetime(schedule_time_range)
        ele_types = pd.Series(ele_types, index=time_range).astype(str).str.strip()
        allowed = pd.Series(False, index=time_range)
        priority = pd.Series(0.0, index=time_range)
        high_types = {"高", "峰"}

        morning_mask = (time_range.hour >= 6) & (time_range.hour < 12)
        allowed.loc[morning_mask] = True
        priority.loc[morning_mask] = 1.0

        for _, day_types in ele_types.groupby(ele_types.index.normalize()):
            evening_types = day_types[
                (day_types.index.hour >= 16) & (day_types.index.hour < 24)
            ]
            if evening_types.empty:
                continue

            sharp_times = evening_types[evening_types == "尖"].index
            if len(sharp_times) == 0:
                no_sharp_high = evening_types[evening_types.isin(high_types)].index
                allowed.loc[no_sharp_high] = True
                priority.loc[no_sharp_high] = 2.0
                continue

            first_sharp_time = sharp_times.min()
            last_sharp_time = sharp_times.max()
            allowed.loc[sharp_times] = True
            priority.loc[sharp_times] = 3.0

            pre_sharp_high = evening_types[
                (evening_types.index < first_sharp_time)
                & evening_types.isin(high_types)
            ]
            allowed.loc[pre_sharp_high.index] = True
            priority.loc[pre_sharp_high.index] = 1.0

            post_sharp_high = evening_types[
                (evening_types.index > last_sharp_time)
                & evening_types.isin(high_types)
            ]
            allowed.loc[post_sharp_high.index] = True
            priority.loc[post_sharp_high.index] = 2.0

        return allowed.to_numpy(dtype=bool), priority.to_numpy(dtype=float)

    def _build_daily_soc_target_indices(self) -> tuple[list[int], list[int]]:
        charge_target_indices = []
        discharge_target_indices = []
        indexed_times = pd.Series(
            range(self.schedule_time_length), index=self.schedule_time_range
        )

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

    @staticmethod
    def _solve_with_fallback(prob: cp.Problem):
        solver_attempts = [cp.HIGHS, cp.CLARABEL]
        last_error = None
        for solver in solver_attempts:
            try:
                result = prob.solve(verbose=False, solver=solver)
            except Exception as exc:  # pragma: no cover
                last_error = exc
                continue
            if prob.status in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
                return solver, result
        if last_error is not None:
            raise RuntimeError(f"PV optimization failed: {last_error}") from last_error
        raise RuntimeError(f"PV optimization failed with status: {prob.status}")

    def modeling2solving(self):
        if self.dispatch_mode == "rule":
            return self._rule_dispatch()
        return self._lp_dispatch()

    def _lp_dispatch(self):
        row = self.devices_num
        column = self.schedule_time_length
        time_ratio = self.freq_minutes / 60
        charge_eff = np.array(self.charge_loss_list).reshape((row, 1))
        discharge_eff = np.array(self.discharge_loss_list).reshape((row, 1))
        charge_max = np.array(self.es_charge_max_list).reshape((row, 1))
        soc_max = np.array(self.es_capacity_max_list).reshape((row, 1))
        soc_min = np.array(self.es_capacity_min_list).reshape((row, 1))
        current_soc = self.current_soc_list.reshape((row, 1))

        pv_to_load = cp.Variable(column, nonneg=True)
        pv_to_battery = cp.Variable(column, nonneg=True)
        pv_to_grid = cp.Variable(column, nonneg=True)
        grid_to_load = cp.Variable(column, nonneg=True)
        grid_to_battery = cp.Variable(column, nonneg=True)
        battery_discharge = cp.Variable((row, column), nonneg=True)
        soc = cp.Variable((row, column))

        battery_charge = pv_to_battery + grid_to_battery
        battery_discharge_agg = cp.sum(battery_discharge, axis=0)
        grid_import = grid_to_load + grid_to_battery

        constraints = [
            pv_to_load + pv_to_battery + pv_to_grid == self.pv_load,
            pv_to_load + battery_discharge_agg + grid_to_load == self.demand_load,
            battery_charge <= np.sum(self.es_charge_max_list),
            battery_discharge <= charge_max,
            grid_import <= self.transform_capacity_list[0],
            pv_to_grid <= self.transform_capacity_list[0],
        ]

        for i in range(row):
            device_charge = battery_charge / row
            constraints.append(
                soc[i, 0]
                == current_soc[i, 0]
                + device_charge[0] * time_ratio * charge_eff[i, 0]
                - battery_discharge[i, 0] * time_ratio / discharge_eff[i, 0]
            )
            for j in range(1, column):
                constraints.append(
                    soc[i, j]
                    == soc[i, j - 1]
                    + device_charge[j] * time_ratio * charge_eff[i, 0]
                    - battery_discharge[i, j] * time_ratio / discharge_eff[i, 0]
                )
            constraints += [soc[i, :] >= soc_min[i, 0], soc[i, :] <= soc_max[i, 0]]

        discharge_allowed_mask, discharge_priority_vec = self._build_discharge_rule(
            self.schedule_time_range, self.ele_types
        )
        for j, ts in enumerate(self.schedule_time_range):
            if self._charge_allowed(ts):
                constraints.append(battery_discharge_agg[j] == 0)
            elif discharge_allowed_mask[j]:
                constraints += [pv_to_battery[j] == 0, grid_to_battery[j] == 0]
            else:
                constraints += [
                    pv_to_battery[j] == 0,
                    grid_to_battery[j] == 0,
                    battery_discharge_agg[j] == 0,
                ]

        soc_target_penalty = self._soc_target_penalty(soc, soc_max, soc_min, constraints)
        energy_cost = time_ratio * grid_import @ self.ele_prices
        pv_sell_revenue = time_ratio * self.pv_sell_price * cp.sum(pv_to_grid)
        max_demand_cost = self.max_demand_price * cp.max(grid_import)
        net_cost = energy_cost + max_demand_cost - pv_sell_revenue
        smooth_penalty = self._smooth_penalty(
            battery_charge, battery_discharge_agg, column, constraints
        )
        priority_reward = (
            self.discharge_priority_weight
            * time_ratio
            * cp.sum(cp.multiply(discharge_priority_vec, battery_discharge_agg))
            if self.discharge_priority_weight > 0
            else 0.0
        )
        noon_mask = np.array([1.0 if 12 <= ts.hour < 14 else 0.0 for ts in self.schedule_time_range])
        noon_pv_dispatch_reward = time_ratio * (
            self.noon_pv_to_battery_priority_weight * cp.sum(cp.multiply(noon_mask, pv_to_battery))
            + self.noon_pv_to_load_priority_weight * cp.sum(cp.multiply(noon_mask, pv_to_load))
            + self.noon_pv_to_grid_priority_weight * cp.sum(cp.multiply(noon_mask, pv_to_grid))
        )
        noon_grid_charge_penalty = (
            self.noon_grid_to_battery_penalty_weight
            * time_ratio
            * cp.sum(cp.multiply(noon_mask, grid_to_battery))
        )
        noon_pv_grid_penalty = (
            self.noon_pv_to_grid_penalty_weight
            * time_ratio
            * cp.sum(cp.multiply(noon_mask, pv_to_grid))
        )

        obj = cp.Minimize(
            net_cost
            + smooth_penalty
            - priority_reward
            + soc_target_penalty
            - noon_pv_dispatch_reward
            + noon_grid_charge_penalty
            + noon_pv_grid_penalty
        )
        prob = cp.Problem(obj, constraints)
        self._solve_with_fallback(prob)
        return {
            "pv_to_load": pv_to_load.value,
            "pv_to_battery": pv_to_battery.value,
            "pv_to_grid": pv_to_grid.value,
            "grid_to_load": grid_to_load.value,
            "grid_to_battery": grid_to_battery.value,
            "battery_charge": battery_charge.value,
            "battery_discharge": battery_discharge_agg.value,
            "grid_import": grid_import.value,
            "soc": np.sum(soc.value, axis=0),
        }

    def _soc_target_penalty(self, soc, soc_max, soc_min, constraints):
        row = self.devices_num
        charge_indices, discharge_indices = self._build_daily_soc_target_indices()
        penalty = 0.0
        if self.charge_target_penalty_weight > 0 and charge_indices:
            shortfall = cp.Variable((row, len(charge_indices)), nonneg=True)
            for k, idx in enumerate(charge_indices):
                constraints.append(soc[:, idx] + shortfall[:, k] >= soc_max[:, 0])
            penalty += self.charge_target_penalty_weight * cp.sum(shortfall)
        if self.discharge_target_penalty_weight > 0 and discharge_indices:
            surplus = cp.Variable((row, len(discharge_indices)), nonneg=True)
            for k, idx in enumerate(discharge_indices):
                constraints.append(soc[:, idx] - surplus[:, k] <= soc_min[:, 0])
            penalty += self.discharge_target_penalty_weight * cp.sum(surplus)
        return penalty

    def _smooth_penalty(self, battery_charge, battery_discharge_agg, column, constraints):
        if self.smooth_penalty_weight <= 0 or column <= 1:
            return 0.0
        charge_delta = cp.Variable(column - 1, nonneg=True)
        discharge_delta = cp.Variable(column - 1, nonneg=True)
        constraints += [
            charge_delta >= battery_charge[1:] - battery_charge[:-1],
            charge_delta >= battery_charge[:-1] - battery_charge[1:],
            discharge_delta >= battery_discharge_agg[1:] - battery_discharge_agg[:-1],
            discharge_delta >= battery_discharge_agg[:-1] - battery_discharge_agg[1:],
        ]
        return self.smooth_penalty_weight * (cp.sum(charge_delta) + cp.sum(discharge_delta))

    def _rule_dispatch(self):
        time_ratio = self.freq_minutes / 60
        charge_eff = np.array(self.charge_loss_list, dtype=float)
        discharge_eff = np.array(self.discharge_loss_list, dtype=float)
        charge_max = np.array(self.es_charge_max_list, dtype=float)
        soc_max = np.array(self.es_capacity_max_list, dtype=float)
        soc_min = np.array(self.es_capacity_min_list, dtype=float)
        device_soc = np.clip(self.current_soc_list.astype(float), soc_min, soc_max)

        pv_to_load = np.minimum(self.pv_load, self.demand_load)
        pv_to_battery = np.zeros(self.schedule_time_length)
        pv_to_grid = np.maximum(self.pv_load - pv_to_load, 0.0)
        grid_to_load = np.maximum(self.demand_load - pv_to_load, 0.0)
        grid_to_battery = np.zeros(self.schedule_time_length)
        battery_charge = np.zeros(self.schedule_time_length)
        battery_discharge = np.zeros(self.schedule_time_length)
        soc = np.zeros(self.schedule_time_length)

        for j, ts in enumerate(self.schedule_time_range):
            if self._charge_allowed(ts):
                charge_by_device = np.minimum(
                    charge_max,
                    np.maximum((soc_max - device_soc) / (charge_eff * time_ratio), 0.0),
                )
                battery_charge[j] = float(charge_by_device.sum())
                grid_to_battery[j] = battery_charge[j]
                device_soc = np.minimum(soc_max, device_soc + charge_by_device * time_ratio * charge_eff)
            elif self._discharge_allowed(ts):
                remaining_load = grid_to_load[j]
                discharge_by_device = np.zeros(self.devices_num)
                for i in range(self.devices_num):
                    power = min(
                        charge_max[i],
                        max((device_soc[i] - soc_min[i]) * discharge_eff[i] / time_ratio, 0.0),
                        remaining_load,
                    )
                    discharge_by_device[i] = power
                    remaining_load -= power
                    if remaining_load <= 1e-9:
                        break
                battery_discharge[j] = float(discharge_by_device.sum())
                grid_to_load[j] = max(grid_to_load[j] - battery_discharge[j], 0.0)
                device_soc = np.maximum(soc_min, device_soc - discharge_by_device * time_ratio / discharge_eff)
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
        return self.schedule_generate(self.modeling2solving())
