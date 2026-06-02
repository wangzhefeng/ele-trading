"""Wind+BESS 容量规划运行脚本

从 configs/wind_bess_capacity_planning.yaml 加载参数，
演示离网风储场景下的最小储能容量搜索（支持 shift 策略）。

流程：合成数据 → 风电出力模拟 → Wind+BESS 容量规划 → 输出结果
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
    WindBESSPlanConfig, ShiftPolicy, WindBESSResult,
    plan_wind_bess_system,
)
from ele_trading.resource_simulation import WindSimulator, SimulationResult
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger

CONFIG_PATH = PROJECT_ROOT / 'configs' / 'wind_bess_capacity_planning.yaml'


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
    shift_cfg = config.get('shift_policy', {})

    n_hours = sc['n_hours']
    timezone = sc['timezone']
    latitude = sc['latitude']
    longitude = sc['longitude']

    rng = np.random.default_rng(42)

    # ── Step 1: 生成合成数据 ──────────────────
    logger.info('=' * 60)
    logger.info('Wind+BESS 容量规划')
    logger.info('=' * 60)
    logger.info('Step 1  生成合成数据')

    wind_weather = _make_wind_weather(n_hours, timezone, rng)
    df_load = _make_load(n_hours, timezone, sc['load_mean_kw'])
    logger.info(f'  负荷均值: {df_load["P_kw"].mean():.0f} kW')

    # ── Step 2: 风电出力模拟 ──────────────────
    logger.info('Step 2  风电出力模拟（windpowerlib）')
    wind_sim = WindSimulator(hub_height=100.0)
    wind_result: SimulationResult = wind_sim.simulate(
        wind_weather, equiv_hours=sc['wind_equiv_hours'], target_capacity_mw=1.0,
    )
    logger.info(f'  等效小时数: {wind_result.total_generation_mwh:.0f} h')

    # ── Step 3: 构造输入 DataFrame ────────────
    logger.info('Step 3  构造输入数据')
    # 风电功率曲线（MW）= 单位出力(kW/kW) × 装机(MW)
    wind_mw_series = wind_result.power_series / 1000.0 * cap['wind_farm_mw']
    df_wind = pd.DataFrame({
        'Time': wind_mw_series.index,
        'WindPower_MW': wind_mw_series.values,
    })

    logger.info(f'  负荷: {len(df_load)} 点, 风电: {len(df_wind)} 点')

    # ── Step 4: Wind+BESS 容量规划 ─────────────
    logger.info('Step 4  Wind+BESS 容量规划')
    logger.info(f'  约束: 自消纳率 ≥ {_fmt_pct(cst["min_green_self_consumption"])}，'
                f'覆盖率 ≥ {_fmt_pct(cst["min_load_coverage"])}')

    # 构造 ShiftPolicy
    shift_policy = ShiftPolicy(
        enable_shift=shift_cfg.get('enable_shift', False),
        lookahead_steps=shift_cfg.get('lookahead_steps', 8),
        shift_max_frac_of_wind=shift_cfg.get('shift_max_frac_of_wind', 0.30),
    )

    # 构造 WindBESSPlanConfig
    cfg = WindBESSPlanConfig(
        eta_charge=bess['eta_charge'],
        eta_discharge=bess['eta_discharge'],
        c_rate=bess['c_rate'],
        soc_init=bess['soc_init'],
        soc_min=bess['soc_min'],
        soc_max=bess['soc_max'],
        enforce_terminal_soc=bess.get('enforce_terminal_soc', False),
        min_green_self_consumption=cst['min_green_self_consumption'],
        min_load_coverage=cst['min_load_coverage'],
        capex_cny_per_kwh=cost['capex_cny_per_kwh'],
        cap_max_mwh=search['cap_max_mwh'],
        tol_mwh=search['tol_mwh'],
        shift_policy=shift_policy,
    )

    # 运行规划
    result = plan_wind_bess_system(
        df_load,
        wind_input=df_wind,
        cfg=cfg,
        time_col='Time',
        load_col='P_kw',
        wind_col='WindPower_MW',
        out_schedule_csv=str(PROJECT_ROOT / 'data' / 'wind_bess_schedule.csv'),
    )

    # ── Step 5: 输出结果 ─────────────────────
    logger.info('─' * 40)
    if result.feasible:
        logger.info('  【规划结果】')
        logger.info(f'  可行:              是')
        logger.info(f'  最小储能容量:      {_fmt_mwh(result.capacity_mwh)}')
        logger.info(f'  储能投资:          {_fmt_wan(result.cost_cny)}')
        logger.info(f'  风电消纳率:        {_fmt_pct(result.green_self_consumption)}')
        logger.info(f'  负荷覆盖率:        {_fmt_pct(result.load_coverage)}')
        logger.info(f'  等效循环次数:      {result.equiv_cycles:.1f}')
        logger.info(f'  调度模式:          {"平移充电" if shift_policy.enable_shift else "纯弃电搬运"}')

        if result.diagnosis:
            logger.info(f'  诊断信息:')
            for k, v in result.diagnosis.items():
                logger.info(f'    {k}: {v:.3f}')
    else:
        logger.info('  【规划结果】不可行')
        logger.info(f'  原因: {result.diagnosis}')

    logger.info('=' * 60)
    logger.info('运行完成。')


if __name__ == '__main__':
    main()
