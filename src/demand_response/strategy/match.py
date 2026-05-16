from dataclasses import dataclass
from datetime import timedelta

import pandas as pd


@dataclass(frozen=True)
class StrategyRuleMatchContext:
    response_date: object
    response_start: object
    delta_discharge_power_1: float
    delta_discharge_power_2: float
    peak1_discharge_power: float
    peak2_discharge_power: float


def build_rule_match_context(
    *,
    response_date,
    response_start,
    delta_discharge_power_1: float,
    delta_discharge_power_2: float,
    peak1_discharge_power: float,
    peak2_discharge_power: float,
) -> StrategyRuleMatchContext:
    return StrategyRuleMatchContext(
        response_date=response_date,
        response_start=response_start,
        delta_discharge_power_1=delta_discharge_power_1,
        delta_discharge_power_2=delta_discharge_power_2,
        peak1_discharge_power=peak1_discharge_power,
        peak2_discharge_power=peak2_discharge_power,
    )


def get_day_rule_matches(context: StrategyRuleMatchContext):
    response_date = context.response_date
    response_start = context.response_start
    delta_discharge_power_1 = context.delta_discharge_power_1
    delta_discharge_power_2 = context.delta_discharge_power_2
    peak1_discharge_power = context.peak1_discharge_power
    peak2_discharge_power = context.peak2_discharge_power
    return {
        "rule1": (
            response_start >= pd.to_datetime(f"{response_date} 10:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 12:29:00")
            and delta_discharge_power_1 > 0
            and delta_discharge_power_1 < peak1_discharge_power
        ),
        "rule2": (
            response_start >= pd.to_datetime(f"{response_date} 21:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 21:59:00")
            and delta_discharge_power_2 > 0
            and delta_discharge_power_2 < peak2_discharge_power
        ),
        "rule3": (
            response_start >= pd.to_datetime(f"{response_date} 10:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 10:59:00")
            and delta_discharge_power_1 == 0
        ),
        "rule4": (
            response_start >= pd.to_datetime(f"{response_date} 21:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 21:59:00")
            and delta_discharge_power_2 == 0
        ),
        "rule5": delta_discharge_power_1 == peak1_discharge_power,
    }


def get_night_rule_matches(context: StrategyRuleMatchContext):
    response_date = context.response_date
    response_start = context.response_start
    delta_discharge_power_1 = context.delta_discharge_power_1
    delta_discharge_power_2 = context.delta_discharge_power_2
    peak1_discharge_power = context.peak1_discharge_power
    peak2_discharge_power = context.peak2_discharge_power
    peak1_window = (
        (
            response_start >= pd.to_datetime(f"{response_date} 10:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 12:29:00")
        )
        or (
            response_start >= pd.to_datetime(f"{response_date - timedelta(days=1)} 22:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 05:59:00")
        )
        or (
            response_start >= pd.to_datetime(f"{response_date} 22:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 23:29:00")
        )
    )
    peak2_window = (
        response_start >= pd.to_datetime(f"{response_date} 21:00:00")
        and response_start <= pd.to_datetime(f"{response_date} 21:59:00")
    )
    return {
        "rule1": peak1_window
        and delta_discharge_power_1 > 0
        and delta_discharge_power_1 < peak1_discharge_power,
        "rule2": peak2_window
        and delta_discharge_power_2 > 0
        and delta_discharge_power_2 < peak2_discharge_power,
        "rule3": (
            response_start >= pd.to_datetime(f"{response_date} 10:00:00")
            and response_start <= pd.to_datetime(f"{response_date} 10:59:00")
            and delta_discharge_power_1 == 0
        ),
        "rule4": peak2_window and delta_discharge_power_2 == 0,
        "rule5": delta_discharge_power_1 == peak1_discharge_power,
    }

