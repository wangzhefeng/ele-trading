import pandas as pd
import numpy as np
import cvxpy as cp
import multiprocessing as mp
import copy
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from time_process import generate_hourly_datetime_pairs, get_month_range, generate_day_pairs

plt.rcParams['font.sans-serif']=['SimHei']    # 用来正常显示中文标签
plt.rcParams['axes.unicode_minus'] = False    # 用来显示负号

exp_name = "keda"
max_demand_price_dict = {"A": 48}

class EsArbitraryRangeScheduler_withMaxDemand:
    """
    定量
        d: 需量功率
        e_c：储能充放功率（正为放电，负为充电)
        p：电价
        e_r_1：目前储能器001的剩余电量
        e_r_2：目前储能器002的剩余电量
        e_l：每个时段损耗电量
        c_l: 充放电功率损失
        e_c_max：充放电功率上限
        e_c_min：充放电功率下限
        e_s_max: 储能器soc上限
        e_s_min：储能器soc下限
        lamda_v: 谷电时段矫正系数
        lamda_p: 峰电时段矫正系数
        lamda_f: 平电时段矫正系数
    变量
        e_c_1 储能系统001对外的充放电功率 kW
        e_c_2 储能系统002对外的充放电功率 kW
        soc_1 储能系统001电量 kWh soc_2 储能系统002电量 kWh
    """
    def __init__(self, 
                 schedule_time_range: list,
                 demand_load, 
                 ele_prices, 
                 ele_types, 
                 devices_info, 
                 current_soc_list,
                 max_demand_price,
                #  max_demand_line,
                 is_slow_charge = False):
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
        self.max_demand_price = max_demand_price
        # self.max_demand_line = max(max_demand_line, max(demand_load))
    
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
        time_ratio = 15/60

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
                constraints += [
                    soc_matrix[i, j] == e_r_vec[i] \
                    - cp.sum(e_c_in_matrix[i, :j+1]) * time_ratio * c_l_in_vec[i] \
                    - cp.sum(e_c_out_matrix[i, :j+1]) * time_ratio / c_l_out_vec[i]
                ]
        # 放电功率小于需量
        constraints += [e_c_out_agg_vec <= cp.pos(d)]
        # 总功率小于最大需量控制线
        # constraints += [d - e_c_in_agg_vec <= self.max_demand_line]
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
            # elif self.ele_types[j] == "深谷":
            #     constraints += [e_c_out_agg_vec[j] == 0]
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
            power_array_i = np.around(charge_array[device_i] + discharge_array[device_i], decimals=3)
            power_array_i = np.asarray(list(map(lambda x: 0.0 if abs(x) < 0.1 else x, power_array_i.tolist())))
            schedule_i_df = pd.DataFrame({"power_opt": power_array_i}, index=self.schedule_time_range)
            schedule_list.append(schedule_i_df)
        return schedule_list
    
    def run(self):
        profit, charge_array, discharge_array = self.modeling2solving()
        schedule_list = self.schedule_generate(charge_array, discharge_array)
        return schedule_list


def flat_valley_price_diff(ele_price_df):
    flat_ele_price_df = copy.deepcopy(ele_price_df)
    if flat_ele_price_df["type"].isin(["谷"]).any():
        v_index = flat_ele_price_df[flat_ele_price_df["type"] == "谷"].index[-1]
    else:
        v_index = -1
    if flat_ele_price_df["type"].isin(["深谷"]).any():
        dv_index = flat_ele_price_df[flat_ele_price_df["type"] == "深谷"].index[-1]
    else:
        dv_index = -1
    
    flat_price_index = max(v_index, dv_index)
    flat_price = flat_ele_price_df.loc[flat_price_index, "value"]
    
    flat_ele_price_df.loc[flat_ele_price_df['type'] == '谷', 'value'] = flat_price
    flat_ele_price_df.loc[flat_ele_price_df['type'] == '深谷', 'value'] = flat_price
    
    return flat_ele_price_df

def generate_month_ranges(start_time, end_time):
    if start_time >= end_time:
        return []
    
    result = []
    # 将当前时间定位到 start_time 所在月的第一天 00:00:00
    current = start_time.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current < end_time:
        # 计算下一个月的第一天，即当前月的结束时间点 (代表 "24:00:00")
        if current.month == 12:
            next_month_start = current.replace(
                year=current.year + 1, month=1, day=1, 
                hour=0, minute=0, second=0, microsecond=0
            )
        else:
            next_month_start = current.replace(
                month=current.month + 1, day=1,
                hour=0, minute=0, second=0, microsecond=0
            )
        # 将 (本月开始, 下月开始) 添加到结果列表
        # 这里的 next_month_start 逻辑上代表 "本月最后一天的24:00:00"
        result.append((current, next_month_start))
        # 当前时间跳到下一个月
        current = next_month_start
        # 如果下一个月已经大于等于 end_time，则停止循环
        if current >= end_time:
            break
    
    return result

def one_process(es_scale, route_num_str):
    devices_info = [{
        "usable_depth": 0.9,
        "charge_loss": 0.92,
        "discharge_loss": 0.95,
        "es_charge_max": es_scale,
        "es_charge_min": -es_scale,
        "es_capacity_max": 1305,
        "es_capacity_min": 0
    }]
    # devices_info = [{"usable_depth": 0.9,
    #                 "charge_loss": 0.92,
    #                 "discharge_loss": 0.95,
    #                 "es_charge_max": es_scale,
    #                 "es_charge_min": -es_scale,
    #                 "es_capacity_max": es_scale * 2,
    #                 "es_capacity_min": 0}]
    
    node_name = f"route_{route_num_str}"
    demand_load_df = pd.read_csv(f"./data/{exp_name}/{node_name}/demand_load_debug_month_01.csv")
    demand_load_df['time'] = pd.to_datetime(demand_load_df['time'])
    ele_price_df = pd.read_csv(f"./data/{exp_name}/{node_name}/ele_price.csv")
    ele_price_df['time'] = pd.to_datetime(ele_price_df['time'])

    max_demand_price = max_demand_price_dict[route_num_str]

    start_time = datetime(2023, 1, 1, 0, 0, 0)
    end_time = datetime(2023, 2, 1, 0, 0, 0)
    validation_day_list = generate_month_ranges(start_time, end_time)

    days_strategy_list = []
    for time_pair in validation_day_list:
        vs_time = time_pair[0]
        ve_time = time_pair[1]
        mask = (demand_load_df['time'] >= vs_time) & (demand_load_df['time'] < ve_time)
        step_demand_load_df = demand_load_df.loc[mask]
        mask = (ele_price_df['time'] >= vs_time) & (ele_price_df['time'] < ve_time)
        step_ele_price_df = ele_price_df.loc[mask]
        # step_ele_price_df = flat_valley_price_diff(step_ele_price_df)
        scheduler_model = EsArbitraryRangeScheduler_withMaxDemand(step_demand_load_df["time"].to_list(), 
                                                            step_demand_load_df["value"].to_list(), 
                                                            step_ele_price_df["value"].to_list(), 
                                                            step_ele_price_df["type"].to_list(),
                                                            devices_info,
                                                            [0],
                                                            max_demand_price)
        opt_list = scheduler_model.run()
        days_strategy_list.append(opt_list[0])

    result_df = pd.concat(days_strategy_list)
    result_df["time"] = result_df.index

    result_df.to_csv(f"./data/{exp_name}/{node_name}/opt_result/es_scale_experiment/schedule_result_scale_{es_scale}_online_False.csv")
    
    return es_scale, node_name

if __name__ == '__main__':
    task_start_time = datetime.now()
    print("start!", exp_name, "task start time:", task_start_time.strftime('%Y-%m-%d %H:%M:%S'))
    es_scale_list = list(range(625, 626, 1))
    print(es_scale_list)
    route_list = ["A"]
    mp_input_list = [(x, y) for x in es_scale_list for y in route_list]
    mp_result_list = []

    with mp.Pool(processes=1) as pool:
            mp_result_list = pool.starmap(one_process, mp_input_list)

    task_end_time = datetime.now()
    task_elapsed_time = task_end_time - task_start_time
    print("Done!", "task end time:", task_end_time.strftime('%Y-%m-%d %H:%M:%S'), "elapsed time:", task_elapsed_time.total_seconds())
