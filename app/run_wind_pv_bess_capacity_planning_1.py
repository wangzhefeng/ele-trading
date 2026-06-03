"""Wind+PV+BESS 容量规划运行脚本

从 configs/wind_pv_bess_capacity_planning.yaml 加载参数，
演示离网风光储场景下的最优容量搜索（PV + BESS 联合搜索）。

流程：合成气象 → 光伏/风电出力模拟 → Wind+PV+BESS 容量规划 → 输出结果
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import numpy as np
import pandas as pd

from ele_trading.capacity_planning import (
    WindPVBEssPlanConfig, WindPVBEssResult,
    plan_wind_pv_bess,
)
from ele_trading.resource_simulation import (
    PVSimulator, SimulationResult,
    WindSimulator,
)
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger

CONFIG_PATH = PROJECT_ROOT / 'configs' / 'wind_pv_bess_capacity_planning.yaml'


# ─────────────────────────────────────────────
# 格式化工具
# ─────────────────────────────────────────────

def _fmt_mw(v: float) -> str:
    return f'{v:.1f} MW'

def _fmt_mwh(v: float) -> str:
    return f'{v:.1f} MWh'

def _fmt_kwp(v: float) -> str:
    return f'{v:.0f} kWp'

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
    pv_search = config['pv_search']
    bess_search = config['bess_search']
    gate_cfg = config['gate_check']
    cap = config['capacity']

    n_hours = sc['n_hours']
    timezone = sc['timezone']
    latitude = sc['latitude']
    longitude = sc['longitude']

    rng = np.random.default_rng(42)

    # ── Step 1: 生成合成数据 ──────────────────
    logger.info('=' * 60)
    logger.info('Wind+PV+BESS 容量规划')
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
    # 光伏单位出力曲线（kW/kWp）= 单位出力(kW/kW)
    df_pv = pd.DataFrame({
        'Time': pv_result.power_series.index,
        'pv_unit_kw': pv_result.power_series.values / 1000.0,
    })

    # 风电功率曲线（MW）= 单位出力(kW/kW) × 装机(MW)
    wind_mw_series = wind_result.power_series / 1000.0 * cap['wind_farm_mw']
    df_wind = pd.DataFrame({
        'Time': wind_mw_series.index,
        'WindPower_MW': wind_mw_series.values,
    })

    logger.info(f'  负荷: {len(df_load)} 点, 光伏: {len(df_pv)} 点, 风电: {len(df_wind)} 点')

    # ── Step 5: Wind+PV+BESS 容量规划 ─────────
    logger.info('Step 5  Wind+PV+BESS 容量规划')
    logger.info(f'  约束: 自消纳率 ≥ {_fmt_pct(cst["self_use_ratio_min"])}，'
                f'覆盖率 ≥ {_fmt_pct(cst["load_cover_ratio_min"])}')

    cfg = WindPVBEssPlanConfig(
        pv_capex_yuan_per_kwp=cost['pv_capex_yuan_per_kwp'],
        bess_capex_yuan_per_kwh=cost['bess_capex_yuan_per_kwh'],
        eta_roundtrip=bess['eta_roundtrip'],
        c_rate=bess['c_rate'],
        soc_init_frac=bess['soc_init_frac'],
        soc_min_frac=bess['soc_min_frac'],
        soc_max_frac=bess['soc_max_frac'],
        self_use_ratio_min=cst['self_use_ratio_min'],
        load_cover_ratio_min=cst['load_cover_ratio_min'],
        pv_step_coarse_kwp=pv_search['pv_step_coarse_kwp'],
        pv_step_fine_kwp=pv_search['pv_step_fine_kwp'],
        pv_refine_window_kwp=pv_search['pv_refine_window_kwp'],
        pv_min_kwp=pv_search['pv_min_kwp'],
        enable_bess=bess_search['enable_bess'],
        batt_hi_init_kwh=bess_search['batt_hi_init_kwh'],
        batt_hi_max_kwh=bess_search['batt_hi_max_kwh'],
        batt_bisect_iter=bess_search['batt_bisect_iter'],
        batt_tol_kwh=bess_search['batt_tol_kwh'],
        enable_gate_check=gate_cfg['enable'],
        gate_target_ratio=gate_cfg['target_ratio'],
        switch_gap_hours=config.get('switch_gap_hours', 0.0),
    )

    result = plan_wind_pv_bess(
        df_load,
        pv_unit_kw=df_pv,
        wind_input=df_wind,
        load_col='P_kw',
        time_col='Time',
        cfg=cfg,
        wind_unit='MW',
    )

    # ── Step 6: 输出结果 ─────────────────────
    logger.info('─' * 40)
    if result.status == 'ok':
        logger.info('  【规划结果】')
        logger.info(f'  状态:              {result.status}')
        logger.info(f'  光伏容量:          {_fmt_kwp(result.pv_kwp)}')
        logger.info(f'  储能容量:          {_fmt_kwh(result.bess_kwh)}')
        logger.info(f'  光伏投资:          {_fmt_wan(result.pv_capex_yuan)}')
        logger.info(f'  储能投资:          {_fmt_wan(result.bess_capex_yuan)}')
        logger.info(f'  总投资:            {_fmt_wan(result.total_capex_yuan)}')
        logger.info(f'  新能源自消纳率:    {_fmt_pct(result.self_use_ratio)}')
        logger.info(f'  负荷覆盖率:        {_fmt_pct(result.load_cover_ratio)}')
        logger.info(f'  光伏年发电量:      {result.pv_gen_kwh_annual / 1e6:.2f} GWh')
        logger.info(f'  风电年发电量:      {result.wind_gen_kwh_annual / 1e6:.2f} GWh')
        logger.info(f'  调度引擎:          {result.engine}')
    elif result.status == 'gate_failed':
        logger.info('  【规划结果】能量门槛未通过')
        logger.info(f'  {result.message}')
        if result.gate:
            logger.info(f'  发电量占比: {result.gate["gen_ratio"]:.3f}')
    else:
        logger.info('  【规划结果】不可行')
        logger.info(f'  原因: {result.message}')

    logger.info('=' * 60)
    logger.info('运行完成。')


if __name__ == '__main__':
    main()
