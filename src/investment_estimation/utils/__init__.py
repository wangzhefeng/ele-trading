"""investment_estimation 通用工具库。

从 `ele_trading.utils` 迁移而来的最小子集,供本包(含 `todo/` 内
capacity_planning 迁移代码)自包含使用,避免反向依赖 ele_trading 主包。
"""

from .io import read_yaml, write_text
from .log_util import logger
from .num_utils import clean_list, clean_value, inclusive_float_range
from .pulp_utils import check_pulp_status
from .time_index import (
    infer_dt_hours,
    monthly_kwh,
    generate_time_points,
    generate_days,
    generate_hours,
    generate_quarters,
    generate_5mins,
    end_of_that_day,
    start_of_this_bess_cycle,
    end_of_this_bess_cycle,
    bess_cycle_window,
    process_time_index,
    extract_timestamp_hours,
)
from .time_splitting import generate_month_ranges, generate_day_pairs, get_time_ranges
from .data_alignment import (
    as_time_series,
    normalize_time_and_load,
    align_to_time,
    align_and_merge,
    ensure_datetime_index,
    read_time_value_csv,
)
from .demand_charge import monthly_peak_demand_cost

__all__ = [
    "read_yaml", "write_text",
    "logger",
    "clean_list", "clean_value", "inclusive_float_range",
    "check_pulp_status",
    "infer_dt_hours", "monthly_kwh", "generate_time_points",
    "generate_days", "generate_hours", "generate_quarters", "generate_5mins",
    "end_of_that_day", "start_of_this_bess_cycle", "end_of_this_bess_cycle",
    "bess_cycle_window", "process_time_index", "extract_timestamp_hours",
    "generate_month_ranges", "generate_day_pairs", "get_time_ranges",
    "as_time_series", "normalize_time_and_load", "align_to_time",
    "align_and_merge", "ensure_datetime_index", "read_time_value_csv",
    "monthly_peak_demand_cost",
]
