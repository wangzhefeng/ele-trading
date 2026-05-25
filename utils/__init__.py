"""Project-level utility function entrypoints."""

from .energy_price import flat_valley_price_diff, flatten_valley_price_diff
from .pv_es_plot import plot_strategy_power_detail
from .time_index import (
    end_of_that_day,
    end_of_this_es_cycle,
    es_cycle_window,
    generate_5mins,
    generate_days,
    generate_hours,
    generate_quarters,
    generate_time_points,
    process_time_index,
    start_of_this_es_cycle,
)

__all__ = [
    "end_of_that_day",
    "end_of_this_es_cycle",
    "es_cycle_window",
    "flat_valley_price_diff",
    "flatten_valley_price_diff",
    "generate_5mins",
    "generate_days",
    "generate_hours",
    "generate_quarters",
    "generate_time_points",
    "plot_strategy_power_detail",
    "process_time_index",
    "start_of_this_es_cycle",
]
