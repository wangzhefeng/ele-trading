from typing import Dict
from datetime import timedelta

import pandas as pd

from model.model_packages.Demand_Response_optim.models.EssSimulation_withoutMaxDemand import (
    EssSimulationModel,
)


def calc_soc(df: pd.DataFrame, device_info: Dict) -> pd.DataFrame:
    demand_load_df = df[["time", "demand_load"]].rename(columns={"demand_load": "value"}).set_index("time")
    strategy_df = df[["time", "strategy_load"]].rename(columns={"strategy_load": "value"}).set_index("time")
    simulation_model = EssSimulationModel(device_info)
    _, es_soc_df, _ = simulation_model.simulation_process(
        demand_load_df, strategy_df, last_soc=0,
    )
    es_soc_df.reset_index(inplace=True)
    es_soc_df = es_soc_df.rename(columns={"index": "time"})
    es_soc_df["time"] = pd.to_datetime(es_soc_df["time"])
    return es_soc_df


def get_response_time_len(response_period: Dict) -> float:
    return (
        response_period["end"] + timedelta(minutes=5) - response_period["start"]
    ).total_seconds() / 3600


def get_remain_power(df_strategy_period: pd.DataFrame, time_period: Dict, freq: str = "5min") -> float:
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    remain_power = df_strategy_period.loc[period_mask, "strategy_load"].sum() * (int(freq[:-3]) / 60)
    return abs(remain_power)


def get_discharge_load(df_strategy_period: pd.DataFrame, time_period: Dict) -> float:
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    return df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] > 0.0),
        "strategy_load",
    ].mean()


def get_discharge_time_len(df_strategy_period: pd.DataFrame, time_period: Dict) -> float:
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    discharge_timestamp = df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] > 0.0),
        "time",
    ]
    if len(discharge_timestamp) > 0.0:
        return (discharge_timestamp.max() - discharge_timestamp.min()).total_seconds() / 3600 + 5 / 60
    return 0.0


def get_discharge_power(df_strategy_period: pd.DataFrame, time_period: Dict, freq: str = "5min") -> float:
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    return df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] > 10.0),
        "strategy_load",
    ].sum() * (int(freq[:-3]) / 60)


def get_charge_power(df_strategy_period: pd.DataFrame, time_period: Dict, freq: str = "5min") -> float:
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    return df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] < 0.0),
        "strategy_load",
    ].sum() * (int(freq[:-3]) / 60)


def get_cancel_charge_power(df_strategy_period: pd.DataFrame, time_period: Dict) -> float:
    period_mask = (
        (df_strategy_period["time"] >= time_period["start"])
        & (df_strategy_period["time"] <= time_period["end"])
    )
    df_cancel_charge = df_strategy_period.loc[
        period_mask & (df_strategy_period["strategy_load"] == 0.0),
        :,
    ]
    return True if len(df_cancel_charge) > 0 else False


def get_response_period_sign_flags(response_period_values):
    response_period_values_pos = all([value > 0.0 for value in response_period_values])
    response_period_values_neg = all([value < 0.0 for value in response_period_values])
    return response_period_values_pos, response_period_values_neg
