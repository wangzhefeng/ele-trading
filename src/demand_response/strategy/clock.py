import pandas as pd


def is_in_window(ts, start, end) -> bool:
    return ts >= start and ts < end


def is_before(ts, point) -> bool:
    return ts < point


def is_at_or_after(ts, point) -> bool:
    return ts >= point


def build_day_time_points(response_date):
    return {
        "08:00": pd.to_datetime(f"{response_date} 08:00:00"),
        "10:00": pd.to_datetime(f"{response_date} 10:00:00"),
        "12:00": pd.to_datetime(f"{response_date} 12:00:00"),
        "10:30": pd.to_datetime(f"{response_date} 10:30:00"),
        "19:00": pd.to_datetime(f"{response_date} 19:00:00"),
        "21:00": pd.to_datetime(f"{response_date} 21:00:00"),
    }
