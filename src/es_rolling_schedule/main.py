import calendar
from typing import Dict, List
from datetime import datetime, timedelta

import pandas as pd

from .BaseAbstractModel.main import BaseModelMainClass
from utils.log_util import logger
from .Utils.time_index_process import (
    process_time_index, 
    generate_days, 
    generate_5mins, 
    end_of_that_day, 
    start_of_this_es_cycle, 
    end_of_this_es_cycle
)
from .Utils.ele_price_process import flat_valley_price_diff
from .EsScheduler.EsArbitraryRangeScheduler import (
    EsArbitraryRangeScheduler_withMaxDemand_evenCharge
)
from .PostHandle.post_handle import post_handler_lingang, shortTerm_maxDemand_match


class ModelMainClass(BaseModelMainClass):
    def __init__(self, project, model, node, args: Dict) -> None:
        self.project = project
        self.model = model
        self.node = node
        self.args = args

    def preprocess_node_data(self, input_data, start_time, end_time):
        # 各类数据的时间index标准化处理
        data_time_range = generate_5mins(start_time, end_time)
        # ------------------------------
        # 必要数据
        # ------------------------------
        # 负荷数据
        df_load = process_time_index(input_data["df_load"], "count_data_time")
        
        # 电价数据-15分钟
        assert len(input_data["df_price"]) == 1152, "wrong ele price info"
        df_price = process_time_index(input_data["df_price"], "time")
        df_price = df_price.reindex(data_time_range, method="ffill")
        # ------------------------------
        # 非必要数据
        # ------------------------------
        # 日期/排工信息 数据- 15分钟
        if "df_data" in input_data.keys():
            df_date = process_time_index(input_data["df_date"], "date")
            df_date = df_date.reindex(data_time_range, method="ffill")
        
        # 气象数据-15分钟
        if "df_weather" in input_data.keys():
            df_weather = process_time_index(input_data["df_weather"], "ts")
            df_weather = df_weather.reindex(data_time_range, method="ffill")
        # ------------------------------
        # 拼接不同来源的数据，对齐时间
        # ------------------------------
        df_all = pd.DataFrame(index = data_time_range)

        df_all["demandLoad"] = df_all.index.map(df_load["predict_value"])
        df_all["elePrice"] = df_all.index.map(df_price["price"])
        df_all["eleType"] = df_all.index.map(df_price["property"])
        if "df_data" in input_data.keys():
            df_all["dateType"] = df_all.index.map(df_date["date_type"])
        if "df_weather" in input_data.keys():
            df_all["tt2"] = df_all.index.map(df_weather["pred_tt2"])

        # 将所有数值字符串转化为数值
        columns_to_convert = ["demandLoad", "elePrice"]
        if "df_data" in input_data.keys():
            columns_to_convert.append("dateType")
        if "df_weather" in input_data.keys():
            columns_to_convert.append("tt2")
        df_all[columns_to_convert] = df_all[columns_to_convert].apply(pd.to_numeric, errors='coerce')
        # ------------------------------
        # 缺失值/异常值处理
        # ------------------------------
        # 插值法处理缺失值
        if df_all["demandLoad"].isna().any():
            df_all["demandLoad"] = df_all["demandLoad"].interpolate(method='linear')
        # 处理负值
        df_all["demandLoad"] = df_all["demandLoad"].apply(lambda x: 0 if x < 0 else x)

        logger.info(f"project: {self.project}, model: {self.model}, node: {self.node}::df has nan or not\n{df_all.isna().any()}")
        logger.info(f"project: {self.project}, model: {self.model}, node: {self.node}::first df after process \n{df_all.iloc[:10]}")
        logger.info(f"project: {self.project}, model: {self.model}, node: {self.node}::last df after process \n{df_all.iloc[-10:]}")
        
        return df_all

    def preprocess_devices_data(self, input_data, devices_info) -> List:
        """
        处理设备数据

        Args:
            input_data (Dict): {
                "df_soc": None,
            }
            devices_info (List[Dict]): [{
                "soc_redundant_ratio": 0.05,
                "usable_depth": 0.90,
            }, {
                "soc_redundant_ratio": 0.05,
                "usable_depth": 0.90,
            }]

        Returns:
            List: [{"soc_df": None}, {"soc_df": None}]
        """
        result_list = []
        for device_i, device in enumerate(devices_info):
            # 实时SOC数据
            df_soc = process_time_index(input_data["df_soc"][device_i], "ts")
            df_soc["soc"] = df_soc["point_value"]
            df_soc = df_soc[["soc"]]
            df_soc["soc"] = df_soc["soc"].apply(pd.to_numeric, errors='coerce')
            df_soc['soc'] = df_soc['soc'].clip(lower=device["soc_redundant_ratio"], upper=device["usable_depth"])

            result_list.append({"soc_df": df_soc})

        return result_list

    def run(self, input_data: Dict, model_cfgs: Dict, devices_info_list: List[Dict], time_range: Dict):
        """
        input_data: InputData
        model_cfgs: Dict
        """
        # logger.info(f"{'='*50}")
        # logger.info(f"input_data: \n{input_data}")
        # logger.info(f"model_cfgs: \n{model_cfgs}")
        # logger.info(f"devices_info_list: \n{devices_info_list}")
        # logger.info(f"time_range: \n{time_range}")
        # logger.info(f"{'='*50}")
        # ##############################
        # 时间标准化
        # ##############################
        data_start_time = datetime.strptime(time_range["start_time"], "%Y-%m-%d %H:%M:%S")
        data_end_time = datetime.strptime(time_range["end_time"], "%Y-%m-%d %H:%M:%S")
        now_time = datetime.strptime(time_range["now_time"], "%Y-%m-%d %H:%M:%S")
        # ##############################
        # 数据预处理
        # ##############################
        node_df = self.preprocess_node_data(input_data, data_start_time, data_end_time)
        devices_soc_list = self.preprocess_devices_data(input_data, devices_info_list)
        # logger.info(f"devices_soc_list: \n{devices_soc_list[0]['soc_df'].sort_index()}")
        # ##############################
        # 全优化周期结果预定义
        # ##############################
        # 第一优化周期时段
        # -----------------
        opt_start_time_1st = now_time + timedelta(minutes=5)
        opt_end_time_1st = end_of_this_es_cycle(opt_start_time_1st, model_cfgs["es_cycle_division_hour"])
        # 第二优化周期时段
        # -----------------
        opt_start_time_2nd = opt_end_time_1st
        opt_end_time_2nd = end_of_this_es_cycle(opt_start_time_2nd, model_cfgs["es_cycle_division_hour"])
        # 全优化周期时段输出结果收集器
        # -----------------
        opt_time_range_all = generate_5mins(opt_start_time_1st, opt_end_time_2nd)
        df_result = {}
        for device_i in devices_info_list:
            df_result[device_i["e_c_opt_node_id"]] = pd.DataFrame(index=opt_time_range_all, columns=['value'])
        # ##############################
        # 第一优化周期
        # ##############################
        opt_time_range_1st = generate_5mins(opt_start_time_1st, opt_end_time_1st)
        
        # 第一优化周期数据筛选(从当前时间到下一优化周期开始时间)
        df_opt = node_df.loc[opt_time_range_1st]
        df_opt = flat_valley_price_diff(df_opt)
        
        # 第一优化周期完整数据筛选
        datum_start_time = start_of_this_es_cycle(opt_start_time_1st, model_cfgs["es_cycle_division_hour"])
        datum_end_time = end_of_this_es_cycle(opt_start_time_1st, model_cfgs["es_cycle_division_hour"])
        datum_time_range = generate_5mins(datum_start_time, datum_end_time)
        df_datum = node_df.loc[datum_time_range]
        df_datum = flat_valley_price_diff(df_datum)
         
        # 最近的 SOC 电量数据
        soc_list = []
        for i in range(len(devices_soc_list)):
            device_soc_df = devices_soc_list[i]["soc_df"]
            # 筛选 SOC 数据
            soc_df_i = device_soc_df.loc[device_soc_df.index < (opt_start_time_1st + timedelta(minutes = 2)), "soc"]
            # 检查 SOC 数据是否实时
            soc_time_lag = opt_start_time_1st - soc_df_i.index.max()
            assert abs(soc_time_lag.total_seconds()) < 16 * 60, "non-real-time SOC"
            # 取最新 SOC 电量(SOC电量 = SOC百分比 * 电池容量)
            soc_df = device_soc_df.loc[device_soc_df.index.max(), "soc"] * devices_info_list[i]["es_capacity_max"]
            # 收集每个设备的最新 SOC 电量
            soc_list.append(soc_df)
        
        # 模型输入数据
        step_demandLoad_list = df_opt["demandLoad"].to_list()  # 负荷数据
        step_demandLoad_list.extend(df_datum["demandLoad"].to_list())
        step_elePrice_value_list = df_opt["elePrice"].to_list()  # 电价数据
        step_elePrice_value_list.extend(df_datum["elePrice"].to_list())
        step_eleType_list = df_opt["eleType"].to_list()  # 电价类型
        step_eleType_list.extend(df_datum["eleType"].to_list())
        days_in_month = calendar.monthrange(opt_end_time_1st.year, opt_end_time_1st.month)[1]  # 月天数
        # 模型
        es_scheduler = EsArbitraryRangeScheduler_withMaxDemand_evenCharge(
            opt_time_range_1st,  # 优化时段
            step_demandLoad_list,  # 负荷数据
            step_elePrice_value_list,  # 电价数据
            step_eleType_list,  # 电价类型
            devices_info_list,  # 设备参数
            soc_list,  # 每个设备的最新 SOC 电量
            float(input_data["demand_ele_price"]),  # 需量电价
            days_in_month,  # 月天数
        )
        opt_list = es_scheduler.run()
        for i, device_i in enumerate(devices_info_list):
            df_result[device_i["e_c_opt_node_id"]].loc[opt_list[i].index, "value"] = opt_list[i]["power_opt"]
        # ##############################
        # 第二优化周期
        # ##############################
        opt_time_range_2nd = generate_5mins(opt_start_time_2nd, opt_end_time_2nd)
        
        # 第二优化周期数据筛选(从当前时间到下一优化周期开始时间)
        df_opt = node_df.loc[opt_time_range_2nd]
        df_opt = flat_valley_price_diff(df_opt)
        
        # 第二优化周期完整数据筛选
        datum_start_time = start_of_this_es_cycle(opt_start_time_2nd, model_cfgs["es_cycle_division_hour"])
        datum_end_time = end_of_this_es_cycle(opt_start_time_2nd, model_cfgs["es_cycle_division_hour"])
        datum_time_range = generate_5mins(datum_start_time, datum_end_time)
        df_datum = node_df.loc[datum_time_range]
        df_datum = flat_valley_price_diff(df_datum)
        
        # SOC 数据(22:00电池剩余的电量, soc_redundant_ratio=0.05)
        soc_list = [i["es_capacity_max"] * i["soc_redundant_ratio"] for i in devices_info_list]

        # 模型输入数据
        step_demandLoad_list = df_opt["demandLoad"].to_list()
        step_demandLoad_list.extend(df_datum["demandLoad"].to_list())
        step_elePrice_value_list = df_opt["elePrice"].to_list()
        step_elePrice_value_list.extend(df_datum["elePrice"].to_list())
        step_eleType_list = df_opt["eleType"].to_list()
        step_eleType_list.extend(df_datum["eleType"].to_list())
        days_in_month = calendar.monthrange(opt_end_time_2nd.year, opt_end_time_2nd.month)[1]
        
        # 模型
        es_scheduler = EsArbitraryRangeScheduler_withMaxDemand_evenCharge(
            opt_time_range_2nd,
            step_demandLoad_list,
            step_elePrice_value_list,
            step_eleType_list,
            devices_info_list,
            soc_list,
            float(input_data["demand_ele_price"]),
            days_in_month,
        )
        opt_list = es_scheduler.run()
        for i, device_i in enumerate(devices_info_list):
            df_result[device_i["e_c_opt_node_id"]].loc[opt_list[i].index, "value"] = opt_list[i]["power_opt"]
        # ##############################
        # 后处理
        # ##############################
        # df_result = shortTerm_maxDemand_match(now_time, node_df, input_data["df_load_true"], df_result, devices)
        df_opt = node_df.loc[opt_time_range_all]
        df_opt = flat_valley_price_diff(df_opt)
        df_result = post_handler_lingang(df_opt, df_result, devices_soc_list[0]["soc_df"], devices_info_list)

        return {"df_result": df_result, "df_strategy": df_result}




# 测试代码 main 函数
def main():
    input_data = {
        "demand_ele_price": 38.4,
        "df_demand": None,
        "df_price": None,
        "df_date": None,
        "df_weather": None,
        "df_soc": None,
    }
    model_cfg = {
        "es_cycle_division_hour": 22,
    }

if __name__ == "__main__":
    main()
