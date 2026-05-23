from typing import List, Dict

import numpy as np
import pandas as pd
import cvxpy as cp


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
                 noon_pv_to_battery_priority_weight: float = 10.0,
                 noon_pv_to_load_priority_weight: float = 0.0,
                 noon_pv_to_grid_priority_weight: float = 0.0,
                 noon_grid_to_battery_penalty_weight: float = 0.0,
                 charge_target_penalty_weight: float = 10.0,
                 discharge_target_penalty_weight: float = 10.0):
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
        self.noon_pv_to_battery_priority_weight = noon_pv_to_battery_priority_weight
        self.noon_pv_to_load_priority_weight = noon_pv_to_load_priority_weight
        self.noon_pv_to_grid_priority_weight = noon_pv_to_grid_priority_weight
        self.noon_grid_to_battery_penalty_weight = noon_grid_to_battery_penalty_weight
        self.charge_target_penalty_weight = charge_target_penalty_weight
        self.discharge_target_penalty_weight = discharge_target_penalty_weight

        if not (self.schedule_time_length == len(self.demand_load) == len(self.ele_prices) == len(self.pv_load)):
            raise ValueError("time, demand_load, ele_prices, and pv_load must have the same length")

    @staticmethod
    def _charge_allowed(ts: pd.Timestamp) -> bool:
        return (0 <= ts.hour < 6) or (12 <= ts.hour < 14)

    @staticmethod
    def _build_discharge_rule(schedule_time_range: List, ele_types: List) -> tuple[np.ndarray, np.ndarray]:
        time_range = pd.to_datetime(schedule_time_range)
        ele_types = pd.Series(ele_types, index=time_range).astype(str).str.strip()
        allowed = pd.Series(False, index=time_range)
        priority = pd.Series(0.0, index=time_range)
        high_types = {"高", "峰"}

        morning_mask = (time_range.hour >= 6) & (time_range.hour < 12)
        allowed.loc[morning_mask] = True
        priority.loc[morning_mask] = 1.0

        for _, day_types in ele_types.groupby(ele_types.index.normalize()):
            evening_types = day_types[(day_types.index.hour >= 16) & (day_types.index.hour < 24)]
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

    @staticmethod
    def _solve_with_fallback(prob: cp.Problem):
        # 当前问题是线性规划，优先尝试更适合大规模 LP 的 HIGHS；
        # 若本地不可用或求解失败，再回退到现有可用的 CLARABEL。
        solver_attempts = [cp.HIGHS, cp.CLARABEL]
        last_error = None
        for solver in solver_attempts:
            try:
                result = prob.solve(verbose=False, solver=solver)
            except Exception as exc:  # pragma: no cover - solver-specific failure path
                last_error = exc
                continue
            if prob.status in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
                return solver, result

        if last_error is not None:
            raise RuntimeError(f"PV optimization failed for solvers {solver_attempts}: {last_error}") from last_error
        raise RuntimeError(f"PV optimization failed with status: {prob.status} for solvers {solver_attempts}")

    def modeling2solving(self):
        # 设备数量
        row = self.devices_num
        column = self.schedule_time_length

        # 数据时间间隔(小时)
        time_ratio = self.freq_minutes / 60

        # 设备参数
        charge_eff = np.array(self.charge_loss_list).reshape((row, 1))
        discharge_eff = np.array(self.discharge_loss_list).reshape((row, 1))
        charge_max = np.array(self.es_charge_max_list).reshape((row, 1))
        soc_max = np.array(self.es_capacity_max_list).reshape((row, 1))
        soc_min = np.array(self.es_capacity_min_list).reshape((row, 1))
        current_soc = self.current_soc_list.reshape((row, 1))

        # ------------------------------
        # 决策变量
        # ------------------------------
        # 光伏去向三分：直接供负荷、充入储能、上网卖电。
        pv_to_load = cp.Variable(column, nonneg=True)
        pv_to_battery = cp.Variable(column, nonneg=True)
        pv_to_grid = cp.Variable(column, nonneg=True)
        # 电网购电也分为两部分：直接供负荷、给储能充电。
        grid_to_load = cp.Variable(column, nonneg=True)
        grid_to_battery = cp.Variable(column, nonneg=True)
        # 电池放电功率与时序 SOC。
        battery_discharge = cp.Variable((row, column), nonneg=True)
        soc = cp.Variable((row, column))
        # ------------------------------
        # 约束条件
        # ------------------------------
        # 聚合后的储能充电功率与总购电功率。
        battery_charge = pv_to_battery + grid_to_battery
        battery_discharge_agg = cp.sum(battery_discharge, axis=0)
        grid_import = grid_to_load + grid_to_battery

        constraints = [
            # 每个时段的光伏发电必须被完整分配。
            pv_to_load + pv_to_battery + pv_to_grid == self.pv_load,
            # 园区负荷只能由光伏直供、储能放电、电网购电三者满足。
            pv_to_load + battery_discharge_agg + grid_to_load == self.demand_load,
            # 单时段总充电功率受储能额定充电功率限制。
            battery_charge <= np.sum(self.es_charge_max_list),
            # 每台设备的放电功率上限。
            battery_discharge <= charge_max,
            # 关口变压器购电方向容量限制；光伏自用和光伏充储不占用购电方向容量。
            grid_import <= self.transform_capacity_list[0],
            # 关口变压器上网反送方向容量限制。
            pv_to_grid <= self.transform_capacity_list[0],
        ]

        for i in range(row):
            device_charge = battery_charge / row
            # SOC 改成递推建模：
            # 语义与原来的“初始 SOC + 前缀和”完全等价，但表达式规模从近似 O(T^2)
            # 降到 O(T)，这是当前性能修复的关键。
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
            constraints += [
                # 可用电量上下界：上界已经乘 usable_depth，下界通常为 0。
                soc[i, :] >= soc_min[i, 0],
                soc[i, :] <= soc_max[i, 0],
            ]

        discharge_allowed_mask, discharge_priority_vec = self._build_discharge_rule(
            self.schedule_time_range,
            self.ele_types,
        )
        for j, ts in enumerate(self.schedule_time_range):
            if self._charge_allowed(ts):
                # 两充两放的充电窗口：允许充电，禁止放电。
                constraints.append(battery_discharge_agg[j] == 0)
            elif discharge_allowed_mask[j]:
                # 放电窗口：允许放电，禁止来自光伏/电网的充电。
                constraints += [
                    pv_to_battery[j] == 0,
                    grid_to_battery[j] == 0,
                ]
            else:
                # 待机窗口：既不充电也不放电。
                constraints += [
                    pv_to_battery[j] == 0,
                    grid_to_battery[j] == 0,
                    battery_discharge_agg[j] == 0,
                ]

        charge_target_indices, discharge_target_indices = self._build_daily_soc_target_indices()
        soc_target_penalty = 0.0
        if self.charge_target_penalty_weight > 0 and charge_target_indices:
            charge_shortfall = cp.Variable((row, len(charge_target_indices)), nonneg=True)
            for k, target_idx in enumerate(charge_target_indices):
                constraints.append(soc[:, target_idx] + charge_shortfall[:, k] >= soc_max[:, 0])
            soc_target_penalty += self.charge_target_penalty_weight * cp.sum(charge_shortfall)
        if self.discharge_target_penalty_weight > 0 and discharge_target_indices:
            discharge_surplus = cp.Variable((row, len(discharge_target_indices)), nonneg=True)
            for k, target_idx in enumerate(discharge_target_indices):
                constraints.append(soc[:, target_idx] - discharge_surplus[:, k] <= soc_min[:, 0])
            soc_target_penalty += self.discharge_target_penalty_weight * cp.sum(discharge_surplus)
        # ------------------------------
        # 目标函数
        # ------------------------------
        # 客户净成本 = 购电电费 + 需量电费 - 光伏上网收入。
        # 最小化净成本，等价于最大化客户收益。
        energy_cost = time_ratio * grid_import @ self.ele_prices
        pv_sell_revenue = time_ratio * self.pv_sell_price * cp.sum(pv_to_grid)
        max_demand_cost = self.max_demand_price * cp.max(grid_import)
        net_cost = energy_cost + max_demand_cost - pv_sell_revenue

        if self.smooth_penalty_weight > 0 and column > 1:
            # 线性化相邻时段充放电功率跳变，作为很小的二级偏好。
            # 主目标仍是客户净成本；该项只在成本接近时压低脉冲式充放电。
            charge_delta = cp.Variable(column - 1, nonneg=True)
            discharge_delta = cp.Variable(column - 1, nonneg=True)
            constraints += [
                charge_delta >= battery_charge[1:] - battery_charge[:-1],
                charge_delta >= battery_charge[:-1] - battery_charge[1:],
                discharge_delta >= battery_discharge_agg[1:] - battery_discharge_agg[:-1],
                discharge_delta >= battery_discharge_agg[:-1] - battery_discharge_agg[1:],
            ]
            smooth_penalty = self.smooth_penalty_weight * (cp.sum(charge_delta) + cp.sum(discharge_delta))
        else:
            smooth_penalty = 0.0

        if self.discharge_priority_weight > 0:
            # 极小的线性奖励用于同收益场景下的放电排序：
            # 尖峰 > 尖后高峰 > 尖前高峰，避免晚间剩余 SOC 无法回补到尖前高峰。
            priority_reward = (
                self.discharge_priority_weight
                * time_ratio
                * cp.sum(cp.multiply(discharge_priority_vec, battery_discharge_agg))
            )
        else:
            priority_reward = 0.0

        noon_charge_mask = np.array([
            1.0 if 12 <= ts.hour < 14 else 0.0
            for ts in self.schedule_time_range
        ])
        noon_pv_dispatch_reward = time_ratio * (
            self.noon_pv_to_battery_priority_weight
            * cp.sum(cp.multiply(noon_charge_mask, pv_to_battery))
            + self.noon_pv_to_load_priority_weight
            * cp.sum(cp.multiply(noon_charge_mask, pv_to_load))
            + self.noon_pv_to_grid_priority_weight
            * cp.sum(cp.multiply(noon_charge_mask, pv_to_grid))
        )
        noon_grid_charge_penalty = (
            self.noon_grid_to_battery_penalty_weight
            * time_ratio
            * cp.sum(cp.multiply(noon_charge_mask, grid_to_battery))
        )

        obj = cp.Minimize(
            net_cost
            + smooth_penalty
            - priority_reward
            + soc_target_penalty
            - noon_pv_dispatch_reward
            + noon_grid_charge_penalty
        )
        # ------------------------------
        # 模型求解
        # ------------------------------
        prob = cp.Problem(obj, constraints)
        solver, result = self._solve_with_fallback(prob)

        return {
            "objective": result,
            "solver": solver,
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
