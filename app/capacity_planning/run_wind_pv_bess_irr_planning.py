"""IRR 目标型 Wind+PV+BESS 容量规划运行脚本。

使用真实仿真数据替代合成数据：
- 正式测算：通过 --data-dir 显式传入输入目录
- Demo 测算：通过 --demo 显式使用 data/profit_calc/wind_pv_bess/v1
- 风电：使用 wind_simulation_v1 模块仿真单位出力曲线
- 光伏：使用 pv_simulation_v1 模块仿真单位出力曲线
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import pandas as pd

from ele_trading.capacity_planning import (
    WindPVBESSIRRPlanConfig,
    WindPVBESSIRRResult,
    plan_wind_pv_bess_for_target_irr,
)
from ele_trading.capacity_planning.wind_pv_bess_irr_tuning import (
    run_wind_pv_bess_irr_resource_tuning,
)
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger


RESULT_COLUMN_CN: dict[str, str] = {
    "solution_rank": "方案排序",
    "is_best_solution": "是否最优方案",
    "scenario_id": "场景编号",
    "target_irr": "目标IRR",
    "wind_target_full_load_hours": "风电目标等效满发小时数",
    "pv_cloud_factor": "光伏云量因子",
    "pv_system_loss": "光伏系统损耗",
    "wind_unit_flh": "风电单位曲线等效满发小时数",
    "pv_unit_flh": "光伏单位曲线等效满发小时数",
    "base_wind_unit_flh": "基准风电单位曲线等效满发小时数",
    "base_pv_unit_flh": "基准光伏单位曲线等效满发小时数",
    "resource_adjustment_score": "资源调整评分",
    "wind_curve_cache_path": "风电曲线缓存路径",
    "pv_curve_cache_path": "光伏曲线缓存路径",
    "stage": "搜索阶段",
    "status": "状态",
    "has_feasible_solution": "是否存在可行解",
    "best_reason": "最佳候选原因",
    "wind_mw": "风电容量(MW)",
    "pv_mw": "光伏容量(MW)",
    "bess_mwh": "储能容量(MWh)",
    "self_use_ratio": "绿电自用率",
    "load_cover_ratio": "负荷覆盖率",
    "owner_avg_price": "业主综合电价(元/kWh)",
    "green_price": "绿电价格(元/kWh)",
    "ppa_price": "PPA价格(元/kWh)",
    "annual_green_generation_kwh": "年度绿电发电量(kWh)",
    "annual_green_used_kwh": "年度绿电消纳量(kWh)",
    "annual_grid_buy_kwh": "年度电网购电量(kWh)",
    "curtail_kwh": "弃电量(kWh)",
    "total_capex_yuan": "总投资(元)",
    "annual_revenue_yuan": "年度收入(元)",
    "annual_opex_yuan": "年度运维成本(元)",
    "annual_cashflow_yuan": "年度现金流(元)",
    "irr": "内部收益率",
    "irr_gap": "IRR差距",
    "reason": "原因",
}


def _write_result_csv_with_cn_header(df: pd.DataFrame, path: Path) -> None:
    """Write a CSV with Chinese labels above the stable English columns."""
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([RESULT_COLUMN_CN.get(column, column) for column in df.columns])
        writer.writerow(list(df.columns))
        df.to_csv(f, index=False, header=False)


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
    from ele_trading.capacity_planning.resource_simulation import WindProfileConfig, load_or_build_wind_profile
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
    weather_cache_dir = cache_path.parent.parent if cache_path.parent.name == "curve_cache" else cache_path.parent
    weather_cache = weather_cache_dir / "weather_cache.csv"
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
    from ele_trading.capacity_planning.resource_simulation import PVProfileConfig, load_or_build_pv_profile

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
        irr_constraint_mode=price.get("irr_constraint_mode", "range"),
        self_use_ratio_min=constraints["self_use_ratio_min"],
        load_cover_ratio_min=constraints["load_cover_ratio_min"],
        wind_min_mw=capacity.get("wind_min_mw", 0.0),
        pv_min_mw=capacity.get("pv_min_mw", 0.0),
        bess_min_mwh=capacity.get("bess_min_mwh", 0.0),
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

# ------------------------------
# 构建新能源单位处理曲线数据路径
# ------------------------------
def _safe_float_token(value: Any) -> str:
    """Convert a scalar config value into a filesystem-safe token."""
    if value is None:
        return "auto"
    if isinstance(value, float):
        text = f"{value:g}"
    else:
        text = str(value)
    return text.replace(".", "p").replace("/", "_").replace(" ", "")


def _curve_cache_filename(prefix: str, config_section: dict[str, Any]) -> str:
    if prefix.startswith("wind"):
        parts = [
            f"flh-{_safe_float_token(config_section.get('target_full_load_hours'))}",
        ]
    elif prefix.startswith("pv"):
        parts = [
            f"cloud-{_safe_float_token(config_section.get('cloud_factor'))}",
            f"loss-{_safe_float_token(config_section.get('system_loss'))}",
        ]
    else:
        parts = []
    return f"{prefix}__{'__'.join(parts)}.csv"


def _curve_cache_path(data_dir: Path, prefix: str, config_section: dict[str, Any]) -> Path:
    return data_dir / "curve_cache" / _curve_cache_filename(prefix, config_section)


def _resolve_data_dir(data_dir: Path | None, *, demo: bool) -> Path:
    """解析运行输入目录。

    V4 明确区分正式输入和仓库样例输入：
    - 正式运行必须通过 --data-dir 显式传入数据目录；
    - 仓库内 data/profit_calc/... 只能在 --demo 模式下使用。
    """
    if data_dir is not None:
        return Path(data_dir)
    if demo:
        return PROJECT_ROOT / "data" / "profit_calc" / "wind_pv_bess" / "v1"
    raise SystemExit("run_wind_pv_bess_irr_planning requires --data-dir or explicit --demo")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Wind/PV/BESS target-IRR capacity planning")
    parser.add_argument("--data-dir", type=Path, default=None, help="正式测算输入目录，需包含 demand_load.csv")
    parser.add_argument("--demo", action="store_true", help="显式使用仓库内 data/profit_calc/wind_pv_bess/v1 样例数据")
    return parser.parse_args(argv)

# ------------------------------
# 构建最优解数据
# ------------------------------
def _build_optimal_solution_df(result: WindPVBESSIRRResult) -> pd.DataFrame:
    columns = [
        "solution_rank",
        "is_best_solution",
        "scenario_id",
        "wind_target_full_load_hours",
        "pv_cloud_factor",
        "pv_system_loss",
        "resource_adjustment_score",
        "wind_curve_cache_path",
        "pv_curve_cache_path",
        # capacity
        "wind_mw",
        "pv_mw",
        "bess_mwh",
        # contraints
        "self_use_ratio",
        "load_cover_ratio",
        "owner_avg_price",
        "green_price",
        "ppa_price",
        # energy calc
        "annual_green_generation_kwh",
        "annual_green_used_kwh",
        "annual_grid_buy_kwh",
        "curtail_kwh",
        # economics
        "total_capex_yuan",
        "annual_revenue_yuan",
        "annual_opex_yuan",
        "annual_cashflow_yuan",
        "irr",
        "irr_gap",
        "reason",
    ]
    if result.status != "ok" or result.diagnostics is None or result.diagnostics.empty:
        return pd.DataFrame(columns=columns)

    df = result.diagnostics.copy()
    if "reason" in df.columns:
        df = df[df["reason"] == "ok"].copy()
    if df.empty:
        return pd.DataFrame(columns=columns)

    for column in columns:
        if column not in df.columns:
            df[column] = None
    if "annual_green_generation_kwh" in df.columns:
        df["annual_green_generation_kwh"] = (
            pd.to_numeric(df["annual_green_used_kwh"], errors="coerce").fillna(0.0)
            + pd.to_numeric(df["curtail_kwh"], errors="coerce").fillna(0.0)
        )
    df.insert(0, "solution_rank_tmp", range(1, len(df) + 1))
    df["solution_rank"] = df["solution_rank_tmp"]
    df["is_best_solution"] = df["solution_rank"] == 1
    df = df.drop(columns=["solution_rank_tmp"])
    return df[columns]




def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    # 加载配置
    CONFIG_PATH = PROJECT_ROOT / 'configs' / 'capacity_planning' / 'wind_pv_bess_irr_planning.yaml'
    config = read_yaml(CONFIG_PATH)
    # 数据目录
    data_dir = _resolve_data_dir(data_dir=args.data_dir, demo=bool(args.demo))
    # ------------------------------
    # 1. 加载真实负荷数据
    # ------------------------------
    df_load = _load_demand(csv_path=data_dir / "demand_load.csv")
    idx = pd.DatetimeIndex(df_load["Time"])
    logger.info("负荷数据加载完成: %d 行, 时间范围 %s ~ %s", len(df_load), df_load["Time"].iloc[0], df_load["Time"].iloc[-1])
    # ------------------------------
    # 2. 运行容量规划
    # ------------------------------
    cfg = _to_config(config)

    tuning_enabled = bool(config.get("resource_tuning", {}).get("enabled", False))
    parameter_search_df: pd.DataFrame | None = None
    parameter_search_best: dict[str, Any] | None = None
    if tuning_enabled:
        logger.info("开始资源调参 + 容量规划遍历...")
        tuning_result = run_wind_pv_bess_irr_resource_tuning(
            config,
            df_load,
            idx,
            data_dir,
            cfg,
            build_wind_unit_curve=_build_wind_unit_curve,
            build_pv_unit_curve=_build_pv_unit_curve,
            curve_cache_path=_curve_cache_path,
        )
        result = tuning_result.result
        parameter_search_df = tuning_result.parameter_search_summary
        parameter_search_best = tuning_result.best_summary
        
        if result is None:
            logger.info("资源调参未找到 IRR 命中解，输出参数搜索摘要和空可行解表。")
            if parameter_search_df is not None and not parameter_search_df.empty:
                parameter_search_best = (
                    parameter_search_df
                    .sort_values("irr", ascending=False, na_position="last")
                    .head(1)
                    .to_dict("records")[0]
                )
            result = WindPVBESSIRRResult(
                status="no_solution",
                diagnostics=pd.DataFrame(),
                message="资源调参未找到满足 IRR 目标的风光储组合。",
            )
    else:
        # 风电单位出力曲线（已有缓存则直接读取，否则仿真并保存）
        wind_curve_path = _curve_cache_path(data_dir, "wind_unit_curve", config["wind_simulation"])
        logger.info("风电单位出力曲线 (1MW)...")
        wind_unit = _build_wind_unit_curve(config, cache_path=wind_curve_path)
        logger.info("风电单位出力曲线就绪 (行数=%d, 均值=%.2f kW/MW)", len(wind_unit), wind_unit.mean())
        
        # 光伏单位出力曲线（已有缓存则直接读取，否则仿真并保存）
        pv_curve_path = _curve_cache_path(data_dir, "pv_unit_curve", config["pv_simulation"])
        logger.info("光伏单位出力曲线 (1kWp)...")
        pv_unit = _build_pv_unit_curve(config, idx, pv_curve_path)
        logger.info("光伏单位出力曲线就绪 (行数=%d, 均值=%.4f kW/kWp)", len(pv_unit), pv_unit.mean())
        
        # 运行容量规划
        logger.info("开始容量规划遍历...")
        result = plan_wind_pv_bess_for_target_irr(df_load, wind_unit, pv_unit, cfg=cfg)
    # ------------------------------
    # 5. 保存结果
    # ------------------------------
    # 结果保存路径
    results_dir = PROJECT_ROOT / "results" / "wind_pv_bess_irr"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # diagnostics.csv save
    diagnostics_path = results_dir / "diagnostics.csv"

    # optimal_solution.csv save
    optimal_path = results_dir / "optimal_solution.csv"
    optimal_df = _build_optimal_solution_df(result)
    _write_result_csv_with_cn_header(optimal_df, optimal_path)
    if optimal_df.empty:
        logger.info("未找到可行解，空可行解表已保存: %s", optimal_path)
    else:
        logger.info("可行解表已保存: %s (%d 行)", optimal_path, len(optimal_df))
    
    # parameter_search_summary.csv save
    if parameter_search_df is not None:
        parameter_search_path = results_dir / "parameter_search_summary.csv"
        _write_result_csv_with_cn_header(parameter_search_df, parameter_search_path)
        logger.info("参数搜索摘要已保存: %s", parameter_search_path)
        if parameter_search_best:
            logger.info("parameter_search_best=%s", parameter_search_best)
    # ------------------------------
    # 6. 日志输出
    # ------------------------------
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
        if result.diagnostic_summary is not None:
            reason_counts = result.diagnostic_summary.get("reason_counts")
            max_irr_candidate = result.diagnostic_summary.get("max_irr_candidate")
            nearest_irr_candidate = result.diagnostic_summary.get("nearest_irr_candidate")
            target_gap_metrics = result.diagnostic_summary.get("target_gap_metrics")
            logger.info("reason_counts=%s", reason_counts)
            logger.info("max_irr_candidate=%s", max_irr_candidate)
            logger.info("nearest_irr_candidate=%s", nearest_irr_candidate)
            logger.info("target_gap_metrics=%s", target_gap_metrics)


if __name__ == "__main__":
    main()
