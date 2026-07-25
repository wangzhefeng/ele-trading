from __future__ import annotations

import pandas as pd

from investment_estimation.config_loader import ProjectConfig


def settle_monthly(dispatch: pd.DataFrame, config: ProjectConfig) -> pd.DataFrame:
    """按月聚合调度时序，形成业主成本和投资方收入口径。"""

    df = dispatch.copy()
    df["month"] = df["time"].dt.to_period("M").astype(str)
    # 无项目基准：假设全部负荷按输入分时电价向电网购电。
    df["baseline_grid_cost"] = df["load_kw"] * df["dt_hours"] * df["price"]
    # 有项目后：电网购电只覆盖剩余负荷和允许的电网充储能电量。
    df["grid_purchase_cost"] = df["grid_buy_kwh"] * df["price"]
    df["transmission_adder_cost"] = df["grid_buy_kwh"] * config.settlement.transmission_price_adder
    df["deviation_penalty_cost"] = df["grid_buy_kwh"] * config.settlement.deviation_penalty_per_kwh
    # MVP 口径：PPA 电量包括风光直接供负荷，以及风光先充储能后续可用的电量。
    df["ppa_energy_kwh"] = df["renewable_to_load_kwh"] + df["charge_from_renewable_kwh"]
    df["ppa_revenue"] = df["ppa_energy_kwh"] * config.ppa_price
    df["export_revenue"] = df["grid_sell_kwh"] * config.export_price

    # 月度聚合是财务模型的年度收入基础，也便于后续对齐真实账单。
    monthly = (
        df.groupby("month", as_index=False)
        .agg(
            load_kwh=("load_kw", lambda s: float((s * df.loc[s.index, "dt_hours"]).sum())),
            grid_buy_kwh=("grid_buy_kwh", "sum"),
            ppa_energy_kwh=("ppa_energy_kwh", "sum"),
            grid_sell_kwh=("grid_sell_kwh", "sum"),
            baseline_grid_cost=("baseline_grid_cost", "sum"),
            grid_purchase_cost=("grid_purchase_cost", "sum"),
            transmission_adder_cost=("transmission_adder_cost", "sum"),
            deviation_penalty_cost=("deviation_penalty_cost", "sum"),
            ppa_cost_to_owner=("ppa_revenue", "sum"),
            export_revenue=("export_revenue", "sum"),
            max_grid_buy_kw=("grid_buy_kwh", lambda s: float((s / df.loc[s.index, "dt_hours"]).max())),
        )
    )
    monthly["basic_charge"] = config.settlement.basic_charge_per_month
    monthly["demand_charge"] = monthly["max_grid_buy_kw"] * config.settlement.demand_charge_per_kw_month
    # 业主侧成本：剩余电网购电成本 + 支付给投资方的 PPA 电费。
    monthly["with_project_owner_cost"] = (
        monthly["grid_purchase_cost"]
        + monthly["transmission_adder_cost"]
        + monthly["deviation_penalty_cost"]
        + monthly["basic_charge"]
        + monthly["demand_charge"]
        + monthly["ppa_cost_to_owner"]
    )
    monthly["owner_saving"] = monthly["baseline_grid_cost"] - monthly["with_project_owner_cost"]
    monthly["owner_saving_pct"] = monthly["owner_saving"] / monthly["baseline_grid_cost"].replace(0, pd.NA)
    # 投资方收入：PPA 收入 + 余电上网收入；暂不含绿证、补贴或储能分成。
    monthly["investor_revenue"] = monthly["ppa_cost_to_owner"] + monthly["export_revenue"]
    return monthly
