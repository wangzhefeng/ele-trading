import numpy as np
import pandas as pd
from datetime import timedelta

from utils.log_util import logger


def __determine_mode(block_df):
    """
    根据数据块中的type序列确定mode类型
    
    参数:
    block_df: 单个数据块的DataFrame
    
    返回:
    str: mode类型
    """
    # 提取type序列
    types = block_df['type'].tolist()
    # 尖峰时刻的索引
    spike_indices = [i for i, t in enumerate(types) if t == '尖']
    # 高峰时刻的索引
    peak_indices = [i for i, t in enumerate(types) if t == '峰']
    # 情况1: 只有峰
    if spike_indices == [] and peak_indices != []:
        return "峰"
    # 情况2: 只有尖
    if peak_indices == [] and spike_indices != []:
        return "尖"
    # 情况3和4: 既有峰又有尖
    first_peak = min(peak_indices) if peak_indices else float('inf')
    first_spike = min(spike_indices) if spike_indices else float('inf')
    last_peak = max(peak_indices) if peak_indices else -1
    last_spike = max(spike_indices) if spike_indices else -1
    # 情况3: 峰出现在尖之前
    if first_peak < first_spike and last_peak < first_spike:
        return "峰尖"
    # 情况4: 尖出现在峰之前
    if first_spike < first_peak and last_spike < first_peak:
        return "尖峰"
    # 情况5: 尖出现在峰之间（峰开始，中间有尖，然后又是峰）
    if (first_peak < first_spike and last_peak > last_spike):
        return "峰尖峰"
    # 其他复杂情况（理论上不应该出现，因为数据块是连续的峰/尖）
    return "其他"

def filter_discharge_time_period(node_i, df_opt):
    """
    提取连续放电时段
    """
    # 确保数据按时间排序
    df_opt = df_opt.sort_index(ascending=True)
    # 标记峰/尖数据行
    df_opt['is_peak_spike'] = df_opt['type'].isin(['峰', '尖'])
    # 创建分组标识：连续峰/尖数据块
    df_opt['block_id'] = (df_opt['is_peak_spike'] != df_opt['is_peak_spike'].shift()).cumsum()
    logger.info(f"{node_i} debug::df_opt: \n{df_opt}")
    # 只保留峰/尖数据块
    peak_spike_blocks = []
    for block_id, block_df in df_opt.groupby('block_id'):
        # 检查该块是否全部为峰或尖
        if block_df['is_peak_spike'].all():
            # 确定mode类型
            block_df = block_df.copy()
            block_df['mode'] = __determine_mode(block_df)
            # 移除辅助列
            result_block = block_df.drop(['is_peak_spike', 'block_id'], axis=1)
            peak_spike_blocks.append(result_block)
    
    return df_opt, peak_spike_blocks

def __flat_battery_cold_process(node_i, block, df_opt):
    """
    峰时充电时段之前的一个小时不充电
    """
    # 调度优化策略开始时间
    strategy_start_time = min(pd.to_datetime(df_opt.index).tolist())
    logger.info(f"\t{node_i} debug::strategy_start_time: {strategy_start_time}")
    # 电池冷却期开始、结束时间
    year, month, day  = block.index[0].year, block.index[0].month, block.index[0].day
    cold_start_time = pd.Timestamp(f"{year}-{month}-{day} 18:00:00")
    cold_end_time = pd.Timestamp(f"{year}-{month}-{day} 18:59:59")
    # 电池冷却期时间索引
    if strategy_start_time <= cold_start_time:
        cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=cold_start_time, end=cold_end_time, freq="5min"))
    elif strategy_start_time > cold_start_time and strategy_start_time <= cold_end_time:
        cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=strategy_start_time, end=cold_end_time, freq="5min"))
    else:
        cold_indexs = []
    logger.info(f"\t{node_i} debug::cold_indexs: \n{cold_indexs}")
    # 数据处理
    df_opt.loc[cold_indexs, "value"] = 0.0

    return df_opt

def __peak_battery_cold_process(node_i, block, df_opt):
    """
    峰时充电时段之前的一个小时不充电
    """
    # 调度优化策略开始时间
    strategy_start_time = min(pd.to_datetime(df_opt.index).tolist())
    logger.info(f"\t{node_i} debug::strategy_start_time: {strategy_start_time}")
    # 电池冷却期开始、结束时间
    year, month, day  = block.index[0].year, block.index[0].month, block.index[0].day
    cold_start_time = pd.Timestamp(f"{year}-{month}-{day} 10:00:00")
    cold_end_time = pd.Timestamp(f"{year}-{month}-{day} 10:59:59")
    # 电池冷却期时间索引
    if strategy_start_time <= cold_start_time:
        cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=cold_start_time, end=cold_end_time, freq="5min"))
    elif strategy_start_time > cold_start_time and strategy_start_time <= cold_end_time:
        cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=strategy_start_time, end=cold_end_time, freq="5min"))
    else:
        cold_indexs = []
    logger.info(f"\t{node_i} debug::cold_indexs: \n{cold_indexs}")
    # 数据处理
    df_opt.loc[cold_indexs, "value"] = 0.0

    return df_opt

def discharge_post_process(node_i, df_opt, peak_spike_blocks, discharge_power_max):
    """
    放电时段放电功率重新分配
    """
    logger.info(f"{node_i} debug::共找到 {len(peak_spike_blocks)} 个连续的峰/尖数据块：")
    for i, block in enumerate(peak_spike_blocks, 1):
        logger.info(f"{node_i} debug::数据块 {i}:")
        logger.info(f"{node_i} {'-' * 20}")
        logger.info(f"\t{node_i} debug::Mode: {block['mode'].iloc[0]}")
        logger.info(f"\t{node_i} debug::时间范围: {block.index.min()}~{block.index.max()}")
        logger.info(f"\t{node_i} debug::数据行数: {len(block)}")
        logger.info(f"\t{node_i} debug::数据类型分布: {block['type'].value_counts().to_dict()}")
        logger.info(f"\t{node_i} debug::前5行数据: \n{block.head()}")
        logger.info(f"\t{node_i} debug::Type序列: {block['type'].tolist()}")
        # 如果是峰、尖、尖峰，强制以电池最大功率放电
        if block['mode'].iloc[0] in ["峰", "尖", "尖峰"]:
            target_indexs = block.index  # 峰、尖、尖峰时段时间索引
            df_opt.loc[target_indexs, "value"] = discharge_power_max  # 以最大放电功率放电
        # 如果是峰尖、峰尖峰则需要计算充电量进行放电分配
        elif block['mode'].iloc[0] in ["峰尖", "峰尖峰"]:
            # ------------------------------
            # 计算高峰、尖峰时段放电功率、放电量
            # ------------------------------
            # 尖峰
            target_indexs_spike = block.loc[block["type"] == "尖", :].index
            target_indexs_spike_nonan = block.loc[(block["type"] == "尖") & (block["value"] > 0), :].index
            time_spike = (max(target_indexs_spike_nonan) - min(target_indexs_spike_nonan)) / np.timedelta64(1, 'h') if not target_indexs_spike_nonan.empty else 0
            power_spike = block.loc[target_indexs_spike_nonan, "value"].mean() if not target_indexs_spike_nonan.empty else 0
            ele_spike = power_spike * time_spike
            logger.info(f"\t{node_i} debug::time_spike: {time_spike}h power_spike: {power_spike}kW ele_spike: {ele_spike}kWh")
            # 尖峰后的高峰
            target_indexs_peak_b1 = block.loc[(block["type"] == "峰") & (block.index > max(target_indexs_spike)), :].index
            target_indexs_peak_b1_nonan = block.loc[(block["type"] == "峰") & (block.index > max(target_indexs_spike)) & (block["value"] > 0), :].index
            time_peak_p1 = (max(target_indexs_peak_b1_nonan) - min(target_indexs_peak_b1_nonan)) / np.timedelta64(1, 'h') if not target_indexs_peak_b1_nonan.empty else 0
            power_peak_p1 = block.loc[target_indexs_peak_b1_nonan, "value"].mean() if not target_indexs_peak_b1_nonan.empty else 0
            ele_peak_p1 = power_peak_p1 * time_peak_p1
            logger.info(f"\t{node_i} debug::time_peak_p1: {time_peak_p1}h power_peak_p1: {power_peak_p1}kW ele_peak_p1: {ele_peak_p1}kWh")
            # 尖峰前的高峰
            target_indexs_peak_b2 = block.loc[(block["type"] == "峰") & (block.index < min(target_indexs_spike)), :].index
            target_indexs_peak_b2_nonan = block.loc[(block["type"] == "峰") & (block.index < min(target_indexs_spike)) & (block["value"] > 0), :].index
            time_peak_p2 = (max(target_indexs_peak_b2_nonan) - min(target_indexs_peak_b2_nonan)) / np.timedelta64(1, 'h') if not target_indexs_peak_b2_nonan.empty else 0
            power_peak_p2 = block.loc[target_indexs_peak_b2_nonan, "value"].mean() if not target_indexs_peak_b2_nonan.empty else 0
            ele_peak_p2 = power_peak_p2 * time_peak_p2
            logger.info(f"\t{node_i} debug::time_peak_p2: {time_peak_p2}h power_peak_p2: {power_peak_p2}kW ele_peak_p2: {ele_peak_p2}kWh")
            # ------------------------------
            # 计算当前放电状态
            # ------------------------------
            # 峰-尖-峰完整间长度
            time_spike_init = (max(target_indexs_spike) - min(target_indexs_spike)) / np.timedelta64(1, 'h') if not target_indexs_spike.empty else 0
            time_peak_p1_init = (max(target_indexs_peak_b1) - min(target_indexs_peak_b1)) / np.timedelta64(1, 'h') if not target_indexs_peak_b1.empty else 0
            time_peak_p2_init = (max(target_indexs_peak_b2) - min(target_indexs_peak_b2)) / np.timedelta64(1, 'h') if not target_indexs_peak_b2.empty else 0
            # 预测功率负荷
            power_spike_mean = df_opt.loc[target_indexs_spike, "value_pred"].replace(0, np.nan).mean(skipna=True) if not target_indexs_spike.empty else 0
            power_peak_1_mean = df_opt.loc[target_indexs_peak_b1, "value_pred"].replace(0, np.nan).mean(skipna=True) if not target_indexs_peak_b1.empty else 0
            power_peak_2_mean = df_opt.loc[target_indexs_peak_b2, "value_pred"].replace(0, np.nan).mean(skipna=True) if not target_indexs_peak_b2.empty else 0
            # 预测充电电量
            ele_spike_pred = power_spike_mean * time_spike_init
            ele_peak_p1_pred = power_peak_1_mean * time_peak_p1_init
            ele_peak_p2_pred = power_peak_2_mean * time_peak_p2_init
            # 待充电总电量
            total_ele = ele_spike + ele_peak_p1 + ele_peak_p2
            # 充电总量与尖峰面积差
            remain_ele_spike = total_ele - ele_spike_pred
            # 充电总量与尖峰+高峰1面积差
            remain_ele_spike_p1 = total_ele - ele_spike_pred - ele_peak_p1_pred
            # 充电总量与尖峰+高峰1+高峰2面积差
            remain_ele_spike_p1_p2 = total_ele - ele_spike_pred - ele_peak_p1_pred - ele_peak_p2_pred
            logger.info(f"\t{node_i} debug::total_ele: {total_ele}kWh")
            logger.info(f"\t{node_i} debug::remain_ele_spike: {remain_ele_spike}kWh")
            logger.info(f"\t{node_i} debug::remain_ele_spike_p1: {remain_ele_spike_p1}kWh")
            logger.info(f"\t{node_i} debug::remain_ele_spike_p1_p2: {remain_ele_spike_p1_p2}kWh")
            # ------------------------------
            # 充电功率重新分配
            # ------------------------------
            if remain_ele_spike <= 0:
                # 尖峰
                df_opt.loc[target_indexs_spike, "value"] = discharge_power_max
                # 高峰 1
                if not target_indexs_peak_b1.empty:
                    df_opt.loc[target_indexs_peak_b1, "value"] = discharge_power_max
                # 高峰 2
                df_opt.loc[target_indexs_peak_b2, "value"] = 0.0
            elif remain_ele_spike > 0:
                if remain_ele_spike_p1 <= 0:
                    # 尖峰
                    df_opt.loc[target_indexs_spike, "value"] = discharge_power_max
                    # 高峰 1
                    if not target_indexs_peak_b1.empty:
                        df_opt.loc[target_indexs_peak_b1, "value"] = discharge_power_max
                    # 高峰 2
                    df_opt.loc[target_indexs_peak_b2, "value"] = 0.0
                elif remain_ele_spike_p1 > 0:
                    if remain_ele_spike_p1_p2 <= 0:
                        # 尖峰
                        df_opt.loc[target_indexs_spike, "value"] = discharge_power_max
                        # 高峰 1
                        if not target_indexs_peak_b1.empty:
                            df_opt.loc[target_indexs_peak_b1, "value"] = discharge_power_max
                        # 高峰 2
                        remain_time_p2 = (remain_ele_spike_p1_p2 / discharge_power_max) * 60  # min
                        remain_index_p2 = remain_time_p2 / 5
                        target_indexs_peak_b2_update = target_indexs_peak_b2[-remain_index_p2:]
                        df_opt.loc[target_indexs_peak_b2_update, "value"] = discharge_power_max
                    elif remain_ele_spike_p1_p2 > 0:
                        # 尖峰
                        df_opt.loc[target_indexs_spike, "value"] = discharge_power_max
                        # 高峰 1
                        df_opt.loc[target_indexs_peak_b1, "value"] = discharge_power_max
                        # 高峰 2
                        df_opt.loc[target_indexs_peak_b2, "value"] = discharge_power_max
        # ------------------------------
        # 第二个峰时充电时段之前的一个小时不充电
        # ------------------------------
        df_opt = __flat_battery_cold_process(node_i=node_i, block=block, df_opt=df_opt)
        # ------------------------------
        # 第一个峰时充电时段之前的一个小时不充电
        # ------------------------------
        df_opt = __peak_battery_cold_process(node_i=node_i, block=block, df_opt=df_opt)
    
    return df_opt

def peak_battery_cold_process(node_i, df_opt, soc_df):
    # 获取关键间时间戳
    soc_df = soc_df.sort_index()
    year, month, day  = soc_df.index[-1].year, soc_df.index[-1].month, soc_df.index[-1].day
    peak_start_time =  pd.Timestamp(f"{year}-{month}-{day} 08:00:00")
    peak_mid_time = pd.Timestamp(f"{year}-{month}-{day} 10:00:00")
    peak_end_time = pd.Timestamp(f"{year}-{month}-{day} 11:00:00")
    current_time = pd.Timestamp.now().ceil("5min")
    logger.info(f"{node_i} debug::current_time: {current_time}")
    # 获取峰时放电区间内的电池 SOC
    discharge_stop_soc_index = soc_df.sort_index().loc[
        (soc_df.index > peak_start_time) & (soc_df.index <= peak_end_time) & 
        (soc_df["soc"] <= 0.05), :].index
    logger.info(f"{node_i} debug::discharge_stop_soc_index: \n{discharge_stop_soc_index}")

    # 原始在第一个峰时放电阶段后的电池冷静期（会由实际放电时长决定，可能低于 1 小时）: 10:00:00~10:55:00
    original_cold_index = pd.DatetimeIndex(data=pd.date_range(start=peak_mid_time, end=peak_end_time, freq="5min", inclusive='right'))
    logger.info(f"{node_i} debug::original_cold_index: \n{original_cold_index}")
    # 根据放电结束时间对放电后冷静期进行处理
    if not discharge_stop_soc_index.isnull().all():  # 放电已经结束
        # 获取放电结束时刻的时间戳
        discharge_stop_soc_index_min = discharge_stop_soc_index.min()
        logger.info(f"{node_i} debug::discharge_stop_soc_index_min: {discharge_stop_soc_index_min}")
        # 根据放电结束时刻生成冷静期时间索引
        if discharge_stop_soc_index_min >= peak_start_time and discharge_stop_soc_index_min <= peak_mid_time:
            cold_indexs = original_cold_index
        elif discharge_stop_soc_index_min > peak_mid_time and discharge_stop_soc_index_min <= peak_end_time:
            cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=discharge_stop_soc_index_min, periods=13, freq="5min", inclusive='right'))
    else:  # 没有放完电
        # 根据放电结束时刻生成冷静期时间索引
        if current_time >= peak_start_time and current_time < peak_mid_time:
            cold_indexs = original_cold_index
        elif current_time >= peak_mid_time and current_time <= peak_end_time:
            cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=current_time, periods=13, freq="5min", inclusive='right'))
        elif current_time > peak_end_time:
            cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=peak_end_time, periods=13, freq="5min", inclusive='right'))
    logger.info(f"{node_i} debug::cold_indexs: \n{cold_indexs}")

    # 电池冷却期开始、结束时间
    cold_start_time, cold_end_time = cold_indexs.min(), cold_indexs.max()
    logger.info(f"{node_i} debug::cold_start_time: {cold_start_time}, cold_end_time: {cold_end_time}")
    # 调度优化策略开始时间
    strategy_start_time = min(pd.to_datetime(df_opt.index).tolist())
    logger.info(f"{node_i} debug::strategy_start_time: {strategy_start_time}")
    # 电池冷却期时间索引动态调整
    if strategy_start_time <= cold_start_time:
        cold_indexs = cold_indexs
    elif strategy_start_time > cold_start_time and strategy_start_time <= cold_end_time:
        cold_indexs = pd.DatetimeIndex(data=pd.date_range(start=strategy_start_time, end=cold_end_time, freq="5min"))
    else:
        cold_indexs = []
    logger.info(f"{node_i} debug::cold_indexs: \n{cold_indexs}")

    # 数据处理
    df_opt.loc[cold_indexs, "value"] = 0.0
    logger.info(f"{node_i} debug::df_opt: \n{df_opt}")
    
    return df_opt

def post_handler_lingang(df_input, result_dict, soc_df, devices_info):
    """
    智能充放电时转换模式识别算法
    作用是保证储能策略在不同的充放电时段以不同的模式与倾向性进行策略的生成
    """
    for node_i, df_opt in result_dict.items():
        # 放电策略重新分配
        for device_info in devices_info:
            if device_info["e_c_opt_node_id"] == node_i:
                # 最大充放电功率
                discharge_power_max = device_info["es_charge_max"]
                logger.info(f"{node_i} debug::discharge_power_max: {discharge_power_max}")
                # 策略模型结果解析
                df_opt["maxLoad"] = discharge_power_max  # 最大充放电功率
                df_opt["demandLoad"] = df_opt.index.map(df_input["demandLoad"])  # 预测功率
                df_opt["type"] = df_opt.index.map(df_input["eleType"])  # 电价类型
                # 取预测功率与最大放电功率的最小值作为实际放电功率
                df_opt["value_pred"] = df_opt.loc[:, ["maxLoad", "demandLoad"]].min(axis=1)
                df_opt.drop("maxLoad", axis=1, inplace=True)
                df_opt.drop("demandLoad", axis=1, inplace=True)
                logger.info(f"{node_i} debug::df_opt: \n{df_opt}")
                # 提取连续放电时段
                df_opt, peak_spike_blocks = filter_discharge_time_period(
                    node_i=node_i, 
                    df_opt=df_opt
                )
                # 放电时段放电功率重新分配
                df_opt = discharge_post_process(
                    node_i=node_i, 
                    df_opt=df_opt, 
                    peak_spike_blocks=peak_spike_blocks, 
                    discharge_power_max=discharge_power_max,
                )
                # 第一个峰时段放电后、平时充电前的一个小时电池冷静期策略处理
                # df_opt = peak_battery_cold_process(
                #     node_i=node_i,
                #     df_opt=df_opt, 
                #     soc_df=soc_df,
                # )
                
                # 删除无用列
                logger.info(f"{node_i} debug::df_opt.columns: \n{df_opt.columns}")
                df_opt.drop("type", axis=1, inplace=True)
                df_opt.drop("value_pred", axis=1, inplace=True)
                df_opt.drop("is_peak_spike", axis=1, inplace=True)
                df_opt.drop("block_id", axis=1, inplace=True)
                # 后处理后的策略
                result_dict[node_i] = df_opt
    
    return result_dict

def shortTerm_maxDemand_match(now_time, df_input, df_demand_load_true, result_dict, devices_info):
    '''
    根据短期内的负荷趋势进一步控制需量突破的比例
    '''
    df_demand_load_true = df_demand_load_true.rename(columns={'count_data_time': 'time', 'h_total_use': 'value'})
    df_demand_load_true['time'] = pd.to_datetime(df_demand_load_true['time'])
    df_demand_load_true.set_index('time', inplace=True)
    df_demand_load_true.sort_index(inplace=True)
    
    last_true_demand_load = df_demand_load_true["value"].iloc[-1]
    reference_period_start_time = now_time - timedelta(hours = 8)
    true_demand_load_max = df_demand_load_true.loc[df_demand_load_true.index > reference_period_start_time, 'value'].max()
    true_demand_load_min = df_demand_load_true.loc[df_demand_load_true.index > reference_period_start_time, 'value'].min()

    result_fine_tune_df = df_input.loc[df_input.index >= now_time, ['demandLoad']]
    result_fine_tune_df.iloc[0:3, 0] = last_true_demand_load
    
    def FT_value_calculate(x):
        FT_value = 25 * ((x - true_demand_load_min) / (true_demand_load_max - true_demand_load_min))
        FT_value = min(FT_value, 25)
        FT_value = max(FT_value, 0)

        return - FT_value
    
    result_fine_tune_df['FT_value'] = result_fine_tune_df['demandLoad'].apply(FT_value_calculate)
    
    for key, result_df in result_dict.items():
        for index, row in result_df.iterrows():
            if index in result_fine_tune_df.index and row["value"] < -50:
                result_df.loc[index, "value"] = result_df.loc[index, "value"] + result_fine_tune_df.loc[index, "FT_value"]
    
    return result_dict
