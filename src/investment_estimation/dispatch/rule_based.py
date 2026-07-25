from __future__ import annotations

import pandas as pd

from investment_estimation.config_loader import BESSConfig


def dispatch_rule_based(df: pd.DataFrame, config: BESSConfig) -> pd.DataFrame:
    """执行 MVP 规则型储能调度，输出每个时间点的能量流。"""

    result = df.copy()
    # 这些列构成后续月度结算的能量流合同，单位均为 kWh。
    columns = [
        "renewable_to_load_kwh",
        "charge_from_renewable_kwh",
        "charge_from_grid_kwh",
        "discharge_to_load_kwh",
        "grid_buy_kwh",
        "grid_sell_kwh",
        "curtail_kwh",
        "soc_kwh",
    ]
    for col in columns:
        result[col] = 0.0

    if config.power_kw <= 0 or config.energy_kwh <= 0:
        return _dispatch_without_storage(result)

    # SOC 运行边界以储能额定容量乘比例得到，单位 kWh。
    soc_min = config.energy_kwh * config.soc_min_pct
    soc_max = config.energy_kwh * config.soc_max_pct
    soc = min(max(config.energy_kwh * config.initial_soc_pct, soc_min), soc_max)

    for idx, row in result.iterrows():
        # 输入的 load_kw/pv_kw/wind_kw 都是平均功率，必须乘 dt 转成电量。
        dt = float(row["dt_hours"])
        load_kwh = float(row["load_kw"]) * dt
        renewable_kwh = (float(row["pv_kw"]) + float(row["wind_kw"])) * dt
        # 功率约束在每个时间步内转换为最大可充/可放电量。
        max_charge_grid = config.power_kw * dt
        max_discharge_grid = config.power_kw * dt

        # 第一步：风光优先直接供负荷，这是自发自用的基础能量流。
        renewable_to_load = min(load_kwh, renewable_kwh)
        remaining_load = load_kwh - renewable_to_load
        renewable_surplus = renewable_kwh - renewable_to_load

        # 第二步：风光余电优先给储能充电，受功率和剩余 SOC 空间约束。
        charge_from_renewable = min(
            renewable_surplus,
            max_charge_grid,
            max((soc_max - soc) / config.charge_efficiency, 0.0),
        )
        soc += charge_from_renewable * config.charge_efficiency
        renewable_surplus -= charge_from_renewable

        # 第三步：在配置允许的高价时段，储能放电供剩余负荷。
        discharge_to_load = 0.0
        if row["price_type"] in config.discharge_price_types and remaining_load > 0:
            discharge_to_load = min(
                remaining_load,
                max_discharge_grid,
                max((soc - soc_min) * config.discharge_efficiency, 0.0),
            )
            soc -= discharge_to_load / config.discharge_efficiency
            remaining_load -= discharge_to_load

        # 第四步：用户已确认 MVP 允许电网充电，低价时段补充 SOC。
        charge_from_grid = 0.0
        if config.allow_grid_charge and row["price_type"] in config.charge_price_types:
            charge_from_grid = min(
                max_charge_grid - charge_from_renewable,
                max((soc_max - soc) / config.charge_efficiency, 0.0),
            )
            soc += charge_from_grid * config.charge_efficiency

        # 记录本时间步能量流；grid_buy 包含剩余负荷购电和电网充储能电量。
        result.at[idx, "renewable_to_load_kwh"] = renewable_to_load
        result.at[idx, "charge_from_renewable_kwh"] = charge_from_renewable
        result.at[idx, "charge_from_grid_kwh"] = charge_from_grid
        result.at[idx, "discharge_to_load_kwh"] = discharge_to_load
        result.at[idx, "grid_buy_kwh"] = remaining_load + charge_from_grid
        result.at[idx, "grid_sell_kwh"] = renewable_surplus
        result.at[idx, "curtail_kwh"] = 0.0
        result.at[idx, "soc_kwh"] = soc

    return result


def _dispatch_without_storage(result: pd.DataFrame) -> pd.DataFrame:
    """无储能或储能容量为 0 时的简化能量平衡。"""

    renewable_kwh = (result["pv_kw"] + result["wind_kw"]) * result["dt_hours"]
    load_kwh = result["load_kw"] * result["dt_hours"]
    # 风光先供负荷，超出负荷的部分作为余电上网，缺口由电网购电。
    result["renewable_to_load_kwh"] = renewable_kwh.where(renewable_kwh < load_kwh, load_kwh)
    result["grid_buy_kwh"] = (load_kwh - result["renewable_to_load_kwh"]).clip(lower=0.0)
    result["grid_sell_kwh"] = (renewable_kwh - result["renewable_to_load_kwh"]).clip(lower=0.0)
    result["soc_kwh"] = 0.0
    return result
