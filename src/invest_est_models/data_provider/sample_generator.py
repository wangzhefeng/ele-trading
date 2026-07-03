from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def generate_sample_csvs(output_dir: str | Path, year: int = 2026, freq: str = "1h") -> dict[str, Path]:
    """生成可复现的模拟输入 CSV，用于真实数据未就绪时打通 MVP 流程。"""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # 左闭右开生成整年时间戳，保证不会多出下一年 1 月 1 日零点。
    time = pd.date_range(f"{year}-01-01 00:00:00", f"{year + 1}-01-01 00:00:00", freq=freq, inclusive="left")
    hour = time.hour.to_numpy()
    day_of_year = time.dayofyear.to_numpy()

    # 负荷样例：基础负荷 + 日内工作时段抬升 + 年内季节波动。
    load_kw = 800 + 180 * ((hour >= 8) & (hour <= 20)) + 80 * np.sin(2 * np.pi * day_of_year / 365)
    # 光伏样例：用日内正弦曲线模拟白天出力，夜间截断为 0。
    pv_kw = np.maximum(0, np.sin(np.pi * (hour - 6) / 12)) * 600
    # 风电样例：叠加年内季节项和日内波动项，负值截断为 0。
    wind_kw = 280 + 90 * np.sin(2 * np.pi * (day_of_year + 30) / 365) + 30 * np.sin(2 * np.pi * hour / 24)
    wind_kw = np.maximum(0, wind_kw)

    # 电价样例：price_type 使用标准英文编码 valley/flat/peak，供规则储能调度使用。
    price_type = np.where((hour >= 10) & (hour <= 21), "peak", np.where((hour >= 0) & (hour <= 7), "valley", "flat"))
    price = np.where(price_type == "peak", 0.95, np.where(price_type == "valley", 0.35, 0.62))

    paths = {
        "load": output / "sample_load.csv",
        "price": output / "sample_price.csv",
        "resource": output / "sample_resource.csv",
    }
    pd.DataFrame({"time": time, "value": load_kw}).to_csv(paths["load"], index=False)
    pd.DataFrame({"time": time, "price": price, "price_type": price_type}).to_csv(paths["price"], index=False)
    pd.DataFrame({"time": time, "pv_kw": pv_kw, "wind_kw": wind_kw}).to_csv(paths["resource"], index=False)
    return paths
