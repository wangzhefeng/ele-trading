"""IRR 目标型 Wind+PV+BESS 容量规划运行脚本。

使用真实仿真数据替代合成数据：
- 负荷：从 data/profit_calc/wind_pv_bess/v1/demand_load.csv 读取
- 风电：使用 wind_simulation_v1 模块仿真单位出力曲线
- 光伏：使用 pv_simulation_v1 模块仿真单位出力曲线
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import pandas as pd

from ele_trading.capacity_planning import (
    WindPVBESSIRRPlanConfig,
    plan_wind_pv_bess_for_target_irr,
)
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger

CONFIG_PATH = PROJECT_ROOT / 'configs' / 'wind_pv_bess_irr_planning.yaml'


def _load_demand(csv_path: Path) -> pd.DataFrame:
    """从 CSV 读取真实负荷数据。

    CSV 格式：Time, value（单位 kW）
    返回 DataFrame 列名统一为 Time, P_kw。
    """
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    # 取前两列（处理尾部多余逗号）
    df = df.iloc[:, :2]
    df.columns = ["Time", "P_kw"]
    df["Time"] = pd.to_datetime(df["Time"])
    df["P_kw"] = pd.to_numeric(df["P_kw"], errors="coerce").fillna(0.0)
    return df.sort_values("Time").reset_index(drop=True)


def _build_wind_unit_curve(config: dict, cache_path: Path) -> pd.Series:
    """使用 wind_simulation_v1 仿真 1MW 风电的单位出力曲线 (kW per MW)。

    如果 cache_path 已存在则直接读取，否则仿真后保存。
    """
    from ele_trading.resource_simulation import WindProfileConfig, load_or_build_wind_profile
    from ele_trading.data_provider.resource_weather import fetch_weather_open_meteo

    # 读取缓存
    if cache_path.exists():
        logger.info("读取已有的风电单位出力曲线: %s", cache_path)
        series = pd.read_csv(cache_path, parse_dates=["timestamp"]).set_index("timestamp").iloc[:, 0]
        series.name = "wind_unit_kw"
        return series

    wind_cfg_dict = dict(config["wind_simulation"])
    site = config["site"]
    wind_cfg_dict["farm_capacity_mw"] = 1.0
    wind_cfg = WindProfileConfig(**wind_cfg_dict)

    # 气象数据缓存
    weather_cache = PROJECT_ROOT / "data" / "profit_calc" / "wind_pv_bess" / "v1" / "weather_cache.csv"
    if weather_cache.exists():
        weather_df = pd.read_csv(weather_cache, parse_dates=["timestamp"]).set_index("timestamp")
    else:
        weather_df = fetch_weather_open_meteo(
            latitude=float(site["latitude"]),
            longitude=float(site["longitude"]),
            start_date=f"{wind_cfg.year}-01-01",
            end_date=f"{wind_cfg.year}-12-31",
            hourly_fields=["wind_speed_100m", "temperature_2m"],
        )
        weather_df.to_csv(weather_cache, index=False)
        if "timestamp" in weather_df.columns:
            weather_df = weather_df.set_index("timestamp")

    result = load_or_build_wind_profile(config=wind_cfg, weather_df=weather_df)
    series = result.power_series.rename("wind_unit_kw")
    if series.index.tz is not None:
        series.index = series.index.tz_localize(None)

    # 保存缓存
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    series.to_frame().to_csv(cache_path, index_label="timestamp")
    logger.info("风电单位出力曲线已保存: %s", cache_path)
    return series


def _build_pv_unit_curve(config: dict, time_index: pd.DatetimeIndex, cache_path: Path) -> pd.Series:
    """使用 pv_simulation_v1 仿真 1kWp 光伏的单位出力曲线 (kW per kWp)。

    如果 cache_path 已存在则直接读取，否则仿真后保存。
    """
    from ele_trading.resource_simulation import PVProfileConfig, load_or_build_pv_profile

    # 读取缓存
    if cache_path.exists():
        logger.info("读取已有的光伏单位出力曲线: %s", cache_path)
        series = pd.read_csv(cache_path, parse_dates=["timestamp"]).set_index("timestamp").iloc[:, 0]
        series.name = "pv_unit_kw"
        return series

    pv_cfg_dict = dict(config["pv_simulation"])
    pv_cfg_dict["capacity_kwp"] = 1.0
    pv_cfg = PVProfileConfig(**pv_cfg_dict)

    result = load_or_build_pv_profile(config=pv_cfg, time_index=time_index)
    series = result.power_series.rename("pv_unit_kw")
    if series.index.tz is not None:
        series.index = series.index.tz_localize(None)

    # 保存缓存
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    series.to_frame().to_csv(cache_path, index_label="timestamp")
    logger.info("光伏单位出力曲线已保存: %s", cache_path)
    return series


def _to_config(config: dict) -> WindPVBESSIRRPlanConfig:
    price = config["price"]
    constraints = config["constraints"]
    capacity = config["capacity"]
    search = config["search"]
    bess = config["bess"]
    cost = config["cost"]
    return WindPVBESSIRRPlanConfig(
        target_owner_price_yuan_per_kwh=price["target_owner_price_yuan_per_kwh"],
        grid_buy_price_yuan_per_kwh=price["grid_buy_price_yuan_per_kwh"],
        green_price_adder_yuan_per_kwh=price["green_price_adder_yuan_per_kwh"],
        target_irr=price["target_irr"],
        irr_tolerance=price["irr_tolerance"],
        self_use_ratio_min=constraints["self_use_ratio_min"],
        load_cover_ratio_min=constraints["load_cover_ratio_min"],
        wind_max_mw=capacity["wind_max_mw"],
        pv_max_mw=capacity["pv_max_mw"],
        bess_max_mwh=capacity["bess_max_mwh"],
        wind_step_mw=search["wind_step_mw"],
        pv_step_mw=search["pv_step_mw"],
        bess_step_mwh=search["bess_step_mwh"],
        eta_roundtrip=bess["eta_roundtrip"],
        c_rate=bess["c_rate"],
        soc_init_frac=bess["soc_init_frac"],
        soc_min_frac=bess["soc_min_frac"],
        soc_max_frac=bess["soc_max_frac"],
        switch_gap_hours=bess.get("switch_gap_hours", 0.0),
        wind_capex_yuan_per_kw=cost["wind_capex_yuan_per_kw"],
        pv_capex_yuan_per_kwp=cost["pv_capex_yuan_per_kwp"],
        bess_capex_yuan_per_kwh=cost["bess_capex_yuan_per_kwh"],
        annual_opex_ratio=cost["annual_opex_ratio"],
        life_years=cost["life_years"],
    )


def main() -> None:
    config = read_yaml(CONFIG_PATH)

    # 1. 加载真实负荷数据
    load_csv = PROJECT_ROOT / "data" / "profit_calc" / "wind_pv_bess" / "v1" / "demand_load.csv"
    df_load = _load_demand(load_csv)
    logger.info("负荷数据加载完成: %d 行, 时间范围 %s ~ %s", len(df_load), df_load["Time"].iloc[0], df_load["Time"].iloc[-1])

    idx = pd.DatetimeIndex(df_load["Time"])

    # 2. 风电单位出力曲线（已有缓存则直接读取，否则仿真并保存）
    data_dir = PROJECT_ROOT / "data" / "profit_calc" / "wind_pv_bess" / "v1"
    wind_curve_path = data_dir / "wind_unit_curve.csv"
    logger.info("风电单位出力曲线 (1MW)...")
    wind_unit = _build_wind_unit_curve(config, wind_curve_path)
    logger.info("风电单位出力曲线就绪 (行数=%d, 均值=%.2f kW/MW)", len(wind_unit), wind_unit.mean())

    # 3. 光伏单位出力曲线（已有缓存则直接读取，否则仿真并保存）
    pv_curve_path = data_dir / "pv_unit_curve.csv"
    logger.info("光伏单位出力曲线 (1kWp)...")
    pv_unit = _build_pv_unit_curve(config, idx, pv_curve_path)
    logger.info("光伏单位出力曲线就绪 (行数=%d, 均值=%.4f kW/kWp)", len(pv_unit), pv_unit.mean())

    # 4. 运行容量规划
    cfg = _to_config(config)
    logger.info("开始容量规划遍历...")
    result = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)

    # 5. 保存结果
    results_dir = PROJECT_ROOT / "results" / "wind_pv_bess_irr"
    results_dir.mkdir(parents=True, exist_ok=True)

    if result.status == "ok":
        summary = pd.DataFrame([{
            "wind_mw": result.wind_mw,
            "pv_mw": result.pv_mw,
            "bess_mwh": result.bess_mwh,
            "irr": result.irr,
            "ppa_price": result.ppa_price,
            "green_price": result.green_price,
            "owner_avg_price": result.owner_avg_price,
            "total_capex_yuan": result.total_capex_yuan,
            "annual_revenue_yuan": result.annual_revenue_yuan,
            "annual_opex_yuan": result.annual_opex_yuan,
            "annual_cashflow_yuan": result.annual_cashflow_yuan,
            "self_use_ratio": result.self_use_ratio,
            "load_cover_ratio": result.load_cover_ratio,
            "curtail_kwh": result.curtail_kwh,
        }])
        summary.to_csv(results_dir / "optimal_solution.csv", index=False)
        logger.info("最优方案已保存: %s", results_dir / "optimal_solution.csv")

    if result.diagnostics is not None and not result.diagnostics.empty:
        result.diagnostics.to_csv(results_dir / "diagnostics.csv", index=False)
        logger.info("诊断表已保存: %s (%d 行)", results_dir / "diagnostics.csv", len(result.diagnostics))

    # 6. 日志输出
    logger.info("=== IRR 目标型 Wind+PV+BESS 容量规划 ===")
    logger.info("status=%s", result.status)
    if result.status == "ok":
        logger.info(
            "wind_mw=%.2f pv_mw=%.2f bess_mwh=%.2f irr=%.4f ppa=%.4f green_price=%.4f owner_avg=%.4f",
            result.wind_mw,
            result.pv_mw,
            result.bess_mwh,
            result.irr or 0.0,
            result.ppa_price,
            result.green_price,
            result.owner_avg_price,
        )
        logger.info(
            "self_use=%.4f load_cover=%.4f total_capex=%.2f annual_cf=%.2f curtail_kwh=%.2f",
            result.self_use_ratio,
            result.load_cover_ratio,
            result.total_capex_yuan,
            result.annual_cashflow_yuan,
            result.curtail_kwh,
        )
    else:
        logger.info("message=%s", result.message)
        if result.diagnostics is not None and not result.diagnostics.empty:
            logger.info("nearest_candidate=%s", result.diagnostics.head(1).to_dict("records")[0])


if __name__ == "__main__":
    main()
