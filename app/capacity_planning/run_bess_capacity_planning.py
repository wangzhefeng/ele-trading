"""离网 BESS 容量规划运行脚本

从 configs/capacity_planning/bess_capacity_planning.yaml 加载参数，
演示离网风光储场景下的最小储能容量搜索。

流程：合成气象 → 光伏/风电出力模拟 → BESS 容量规划 → 输出结果
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import numpy as np
import pandas as pd

from ele_trading.capacity_planning import (
    plan_energy_system, BESSPlanConfig, UnitsConfig,
)
from ele_trading.resource_simulation import (
    PVSimulator, SimulationResult,
    WindSimulator,
)
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger

CONFIG_PATH = PROJECT_ROOT / 'configs' / 'capacity_planning' / 'bess_capacity_planning.yaml'


# ─────────────────────────────────────────────
# 格式化工具
# ─────────────────────────────────────────────

def _fmt_mw(v: float) -> str:
    return f'{v:.1f} MW'

def _fmt_mwh(v: float) -> str:
    return f'{v:.1f} MWh'

def _fmt_kwh(v: float) -> str:
    return f'{v:.0f} kWh'

def _fmt_pct(v: float) -> str:
    return f'{v * 100:.1f} %'

def _fmt_wan(v: float) -> str:
    return f'{v / 10000:.0f} 万元'


# ─────────────────────────────────────────────
# 合成数据生成
# ─────────────────────────────────────────────

def _make_pv_weather(n_hours: int, timezone: str,
                        rng: np.random.Generator) -> pd.DataFrame:
    """合成光伏气象数据。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=timezone)
    hours = np.arange(n_hours) % 24
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    seasonal = 0.8 + 0.2 * np.cos(2 * np.pi * (day_of_year - 0.17))
    ghi_max = 900 * seasonal
    ghi = np.maximum(0.0, np.sin(np.pi * (hours - 6) / 12)) * ghi_max
    ghi += rng.normal(0, 20, n_hours)
    ghi = np.clip(ghi, 0, None)
    return pd.DataFrame({
        'ghi':       ghi,
        'temp_air':  10 + 15 * np.sin(2 * np.pi * (day_of_year - 0.25)) + rng.normal(0, 2, n_hours),
        'wind_speed': np.abs(rng.normal(3, 1.5, n_hours)),
    }, index=idx)


def _make_wind_weather(n_hours: int, timezone: str,
                       rng: np.random.Generator) -> pd.DataFrame:
    """合成风电气象数据。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=timezone)
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    seasonal = 1.1 - 0.2 * np.sin(2 * np.pi * day_of_year)
    wind_speed = rng.weibull(2.0, n_hours) * 7 * seasonal
    return pd.DataFrame({
        'wind_speed':  np.clip(wind_speed, 0, 30),
        'temperature': 10 + 15 * np.sin(2 * np.pi * (day_of_year - 0.25)) + rng.normal(0, 2, n_hours),
        'pressure':    rng.uniform(99000, 101325, n_hours),
    }, index=idx)


def _make_load(n_hours: int, timezone: str, load_mean_kw: float) -> pd.DataFrame:
    """合成工业负荷（kW）。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=timezone)
    hours = np.arange(n_hours) % 24
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    daily = 0.85 + 0.15 * np.sin(np.pi * (hours - 8) / 10) * (hours >= 8) * (hours < 22)
    seasonal = 1.0 + 0.1 * np.sin(2 * np.pi * day_of_year)
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.02, n_hours)
    load = load_mean_kw * daily * seasonal + noise
    return pd.DataFrame({
        'Time': idx,
        'P_kw': np.clip(load, 500.0, None),
    })


# ─────────────────────────────────────────────
# 主运行逻辑
# ─────────────────────────────────────────────

def main():
    config = read_yaml(CONFIG_PATH)
    sc = config['scenario']
    cst = config['constraints']
    bess = config["bess"]
    cost = config['cost']
    search = config['search']
    cap = config['capacity']

    n_hours = sc['n_hours']
    timezone = sc['timezone']
    latitude = sc['latitude']
    longitude = sc['longitude']

    rng = np.random.default_rng(42)

    # ── Step 1: 生成合成数据 ──────────────────
    logger.info('=' * 60)
    logger.info('离网 BESS 容量规划')
    logger.info('=' * 60)
    logger.info('Step 1  生成合成数据')

    pv_weather = _make_pv_weather(n_hours, timezone, rng)
    wind_weather = _make_wind_weather(n_hours, timezone, rng)
    df_load = _make_load(n_hours, timezone, sc['load_mean_kw'])
    logger.info(f'  负荷均值: {df_load["P_kw"].mean():.0f} kW')

    # ── Step 2: 光伏出力模拟 ──────────────────
    logger.info('Step 2  光伏出力模拟（pvlib）')
    pv_sim = PVSimulator(
        latitude=latitude, longitude=longitude,
        timezone=timezone, altitude=50.0,
    )
    pv_result: SimulationResult = pv_sim.simulate(
        pv_weather, equiv_hours=sc['pv_equiv_hours'], target_capacity_mw=1.0,
    )
    logger.info(f'  等效小时数: {pv_result.total_generation_mwh:.0f} h')

    # ── Step 3: 风电出力模拟 ──────────────────
    logger.info('Step 3  风电出力模拟（windpowerlib）')
    wind_sim = WindSimulator(hub_height=100.0)
    wind_result: SimulationResult = wind_sim.simulate(
        wind_weather, equiv_hours=sc['wind_equiv_hours'], target_capacity_mw=1.0,
    )
    logger.info(f'  等效小时数: {wind_result.total_generation_mwh:.0f} h')

    # ── Step 4: 构造输入 DataFrame ────────────
    logger.info('Step 4  构造输入数据')
    # 光伏功率曲线（kW）= 单位出力(kW/kW) × 装机(kWp)
    pv_kw_series = pv_result.power_series / 1000.0 * cap['pv_kwp']
    df_pv = pd.DataFrame({
        'Time': pv_kw_series.index,
        'pv_kw': pv_kw_series.values,
    })

    # 风电功率曲线（MW）= 单位出力(kW/kW) × 装机(MW)
    wind_mw_series = wind_result.power_series / 1000.0 * cap['wind_farm_mw']
    df_wind = pd.DataFrame({
        'Time': wind_mw_series.index,
        'WindPower_MW': wind_mw_series.values,
    })

    logger.info(f'  负荷: {len(df_load)} 点, 光伏: {len(df_pv)} 点, 风电: {len(df_wind)} 点')

    # ── Step 5: BESS 容量规划 ─────────────────
    logger.info('Step 5  BESS 容量规划')
    logger.info(f'  约束: 自消纳率 ≥ {_fmt_pct(cst["self_use_ratio_min"])}，'
                f'覆盖率 ≥ {_fmt_pct(cst["load_cover_ratio_min"])}')

    cfg = BESSPlanConfig(
        bess_capex_yuan_per_kwh=cost['bess_capex_yuan_per_kwh'],
        eta_roundtrip=bess['eta_roundtrip'],
        c_rate=bess['c_rate'],
        soc_init_frac=bess['soc_init_frac'],
        soc_min_frac=bess['soc_min_frac'],
        soc_max_frac=bess['soc_max_frac'],
        self_use_ratio_min=cst['self_use_ratio_min'],
        load_cover_ratio_min=cst['load_cover_ratio_min'],
        batt_hi_max_kwh=search['batt_hi_max_kwh'],
        search_points=search['search_points'],
    )
    units = UnitsConfig(load_power='kW', wind_power='MW')

    result = plan_energy_system(
        df_load,
        pv_power=df_pv,
        wind_input=df_wind,
        time_col='Time',
        load_col='P_kw',
        cfg=cfg,
        units=units,
    )

    # ── Step 6: 输出结果 ─────────────────────
    logger.info('─' * 40)
    if result.feasible:
        logger.info('  【规划结果】')
        logger.info(f'  可行:              是')
        logger.info(f'  最小储能容量:      {_fmt_kwh(result.bess_kwh)}')
        logger.info(f'  储能投资:          {_fmt_wan(result.cost_yuan)}')
        logger.info(f'  新能源自消纳率:    {_fmt_pct(result.self_use_ratio)}')
        logger.info(f'  负荷覆盖率:        {_fmt_pct(result.load_cover_ratio)}')
        logger.info(f'  总发电量:          {result.gen_kwh / 1e6:.2f} GWh')
        logger.info(f'  总用电量:          {result.load_kwh / 1e6:.2f} GWh')
        logger.info(f'  有效供电量:        {result.used_kwh / 1e6:.2f} GWh')
        logger.info(f'  调度引擎:          {result.engine}')
    else:
        logger.info('  【规划结果】不可行')
        logger.info(f'  原因: {result.diagnosis}')

    logger.info('=' * 60)
    logger.info('运行完成。')


if __name__ == '__main__':
    main()
