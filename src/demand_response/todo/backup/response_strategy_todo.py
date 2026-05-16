# ------------------------------
# TODO 未使用
# ------------------------------
def calc_soc(df: pd.DataFrame, device_info: Dict) -> pd.DataFrame:
    """
    仿真系统电量计算
    """
    # 负荷预测数据预处理
    demand_load_df = df[["time", "demand_load"]]
    demand_load_df = demand_load_df.rename(columns={"demand_load": "value"})
    demand_load_df = demand_load_df.set_index("time")
    # 电价数据预处理
    ele_price_df = df[["time", "ele_price"]]
    ele_price_df = ele_price_df.rename(columns={"ele_price": "value"})
    ele_price_df = ele_price_df.set_index("time")
    # 策略数据预处理
    strategy_df = df[["time", "strategy_load"]]
    strategy_df = strategy_df.rename(columns={"strategy_load": "value"})
    strategy_df = strategy_df.set_index("time") 
    # simulation
    simulation_model = EssSimulationModel(device_info)
    # ------------------------------
    # get simulation result
    # ------------------------------
    es_charge_df, es_soc_df, total_load_df = simulation_model.simulation_process(
        demand_load_df, strategy_df, last_soc=0,
    )
    es_soc_df.reset_index(inplace=True)
    es_soc_df = es_soc_df.rename(columns={"index": "time"})
    es_soc_df["time"] = pd.to_datetime(es_soc_df["time"])
    # ------------------------------
    # get simulation profit
    # ------------------------------
    # origin_balance, opt_balance = simulation_model.revenue_calculation(
    #     demand_load_df, 
    #     es_charge_df, 
    #     ele_price_df, 
    #     max_demand_price=38.4,
    # )
    # profit = origin_balance - opt_balance
    
    return es_soc_df

def get_remain_power_simulation(df_strategy_period: pd.DataFrame, stop_time: datetime, device_info) -> float:
    """
    计算储能电池截止到某个时刻的剩余电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(储能电池SOC)
        stop_time (datetime): 截止时刻
        device_info (Dict): 储能电池信息

    Returns:
        float: 剩余电量
    """
    df_soc = calc_soc(df_strategy_period, device_info)
    remain_power = df_soc.loc[df_soc["time"] == stop_time, "value"].values[0] 

    return remain_power

def get_remain_power_soc_history(df_strategy_period: pd.DataFrame, stop_time: datetime, device_info) -> float:
    """
    计算储能电池截止到某个时刻的剩余电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(储能电池SOC)
        stop_time (datetime): 截止时刻
        device_info (Dict): 储能电池信息

    Returns:
        float: 剩余电量
    """
    battery_soc = df_strategy_period.loc[
        df_strategy_period["time"] == stop_time, 
        "soc_history"
    ].values[0]
    if battery_soc > 0.05:
        remain_power = device_info["es_capacity_max"] * battery_soc
    else:
        remain_power = 0.0

    return remain_power

def get_discharge_power_soc_history(df_strategy_period: pd.DataFrame, time_period: Dict, device_info: Dict) -> float:
    """
    计算某一个时段的放电电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段

    Returns:
        float: 放电电量
    """
    remain_power_before_discharge = get_remain_power_soc_history(
        df_strategy_period, time_period["start"], device_info,
    )
    remain_power_after_discharge = get_remain_power_soc_history(
        df_strategy_period, time_period["end"], device_info,
    )
    if (time_period["start"] <= time_period["end"]) or \
       (remain_power_before_discharge <= remain_power_after_discharge):
        discharge_power = 0.0
    else:
        discharge_power = remain_power_before_discharge - remain_power_after_discharge

    return discharge_power

def get_charge_power_soc_history(df_strategy_period: pd.DataFrame, time_period: Dict, device_info: Dict) -> float:
    """
    计算某一个时段的充电电量

    Args:
        df_strategy_period (pd.DataFrame): 输入数据(策略数据)
        time_period (Dict): 时段

    Returns:
        float: 充电电量
    """
    remain_power_before_charge = get_remain_power_soc_history(
        df_strategy_period, time_period["start"], device_info,
    )
    remain_power_after_charge = get_remain_power_soc_history(
        df_strategy_period, time_period["end"], device_info,
    )
    if (time_period["start"] >= time_period["end"]) or \
       (remain_power_before_charge >= remain_power_after_charge):
        charge_power = 0.0
    else:
        charge_power = remain_power_after_charge - remain_power_before_charge
    
    return charge_power
