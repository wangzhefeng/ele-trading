"""风光储一体化运行脚本

演示流程：
  1. 生成合成气象数据（北京地区，全年 8760 小时）
  2. 光伏出力模拟（pvlib 物理模型 + 等效小时数校准）
  3. 风电出力模拟（windpowerlib 物理模型 + 等效小时数校准）
  4. 合成工业负荷曲线
  5. 容量规划优化（两阶段粗-精网格搜索，最低成本 + 满足绿电/自用约束）
  6. 最优方案时序运行仿真（储能充放电调度）
  7. 短期出力预测演示（光伏谐波回归 + 风电 AR 模型）
"""
from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import numpy as np
import pandas as pd

from ele_trading.capacity_planning import (
    SolarSimulator, SolarSimResult,
    WindSimulator, WindSimResult,
)
from ele_trading.capacity_planning.capacity_optimizer import (
    CapacityOptimizer, CapacityPlanResult, simulate_operation,
)
from ele_trading.forecasting.solar_forecast import SolarPowerForecaster
from ele_trading.forecasting.wind_forecast import WindPowerForecaster
from ele_trading.utils.log_util import logger


# ─────────────────────────────────────────────
# 场景参数
# ─────────────────────────────────────────────
LATITUDE      = 39.9          # 北京
LONGITUDE     = 116.4
TIMEZONE      = 'Asia/Shanghai'
N_HOURS       = 8760          # 全年小时数
SOLAR_EQUIV_H = 1200.0        # 光伏年等效小时数（华北典型值）
WIND_EQUIV_H  = 2200.0        # 风电年等效小时数（华北典型值）
LOAD_MEAN_MW  = 20.0          # 工业用户平均负荷

# 容量规划约束
GREEN_RATIO_MIN   = 0.40      # 绿电消纳率 ≥ 40 %
SELF_USE_MIN      = 0.80      # 绿电自用率 ≥ 80 %

# 储能参数
STORAGE_PARAMS = dict(
    eta_charge=0.95, eta_discharge=0.95,
    dod=0.90, soc_min=0.10, c_rate=0.5,
)
# 单位成本（元/kW 或 元/kWh）
COST_PARAMS = dict(
    wind_yuan_per_kw=5000,
    pv_yuan_per_kw=3500,
    ess_yuan_per_kwh=1500,
)
# 搜索参数（demo 用中等步长，加快速度）
SEARCH_PARAMS = dict(
    coarse_step_mw=10, coarse_step_mwh=20,
    fine_step_mw=2,    fine_step_mwh=5,
    fine_range_ratio=0.3,
    max_wind_mw=60, max_pv_mw=60, max_ess_mwh=120,
)


# ─────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────

def _make_solar_weather(n_hours: int, rng: np.random.Generator) -> pd.DataFrame:
    """合成光伏气象数据（GHI 用简化日循环模型）。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=TIMEZONE)
    hours = np.arange(n_hours) % 24
    # 日照：上午 6 时至下午 18 时余弦曲线，冬夏辐照强度有季节调制
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    seasonal = 0.8 + 0.2 * np.cos(2 * np.pi * (day_of_year - 0.17))   # 夏强冬弱
    ghi_max = 900 * seasonal
    ghi = np.maximum(0.0, np.sin(np.pi * (hours - 6) / 12)) * ghi_max
    ghi += rng.normal(0, 20, n_hours)
    ghi = np.clip(ghi, 0, None)
    return pd.DataFrame({
        'ghi':       ghi,
        'temp_air':  10 + 15 * np.sin(2 * np.pi * (day_of_year - 0.25)) + rng.normal(0, 2, n_hours),
        'wind_speed': np.abs(rng.normal(3, 1.5, n_hours)),
    }, index=idx)


def _make_wind_weather(n_hours: int, rng: np.random.Generator) -> pd.DataFrame:
    """合成风电气象数据（Weibull 风速分布）。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=TIMEZONE)
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    seasonal = 1.1 - 0.2 * np.sin(2 * np.pi * day_of_year)  # 春冬风速略高
    wind_speed = rng.weibull(2.0, n_hours) * 7 * seasonal
    return pd.DataFrame({
        'wind_speed':  np.clip(wind_speed, 0, 30),
        'temperature': 10 + 15 * np.sin(2 * np.pi * (day_of_year - 0.25)) + rng.normal(0, 2, n_hours),
        'pressure':    rng.uniform(99000, 101325, n_hours),
    }, index=idx)


def _make_load(n_hours: int) -> pd.Series:
    """合成工业负荷：日波动 + 季节波动 + 随机扰动。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=TIMEZONE)
    hours      = np.arange(n_hours) % 24
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    daily    = 0.85 + 0.15 * np.sin(np.pi * (hours - 8) / 10) * (hours >= 8) * (hours < 22)
    seasonal = 1.0 + 0.1 * np.sin(2 * np.pi * day_of_year)
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.02, n_hours)
    load = LOAD_MEAN_MW * daily * seasonal + noise
    return pd.Series(np.clip(load, 5.0, None), index=idx, name='load_mw')


def _fmt_mw(v: float) -> str:
    return f'{v:.1f} MW'

def _fmt_mwh(v: float) -> str:
    return f'{v:.1f} MWh'

def _fmt_pct(v: float) -> str:
    return f'{v * 100:.1f} %'

def _fmt_wan(v: float) -> str:
    return f'{v:.0f} 万元'


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

if __name__ == '__main__':

    rng = np.random.default_rng(42)

    # ── Step 1: 生成气象数据 ──────────────────
    logger.info('=' * 60)
    logger.info('Step 1  生成合成气象数据')
    solar_weather = _make_solar_weather(N_HOURS, rng)
    wind_weather  = _make_wind_weather(N_HOURS, rng)
    load_series   = _make_load(N_HOURS)
    logger.info(f'  气象时序长度: {N_HOURS} h，负荷均值: {load_series.mean():.2f} MW')

    # ── Step 2: 光伏出力模拟（1 MW 基准）────────
    logger.info('=' * 60)
    logger.info('Step 2  光伏出力模拟（pvlib）')
    pv_sim = SolarSimulator(
        latitude=LATITUDE, longitude=LONGITUDE,
        timezone=TIMEZONE, altitude=50.0,
    )
    pv_result: SolarSimResult = pv_sim.simulate(
        solar_weather, equiv_hours=SOLAR_EQUIV_H, target_capacity_mw=1.0,
    )
    logger.info(f'  等效小时数校准: {pv_result.total_generation_mwh:.1f} MWh/MW'
                f'（目标 {SOLAR_EQUIV_H} h，K={pv_result.scale_factor:.4f}）')

    # ── Step 3: 风电出力模拟（1 MW 基准）────────
    logger.info('=' * 60)
    logger.info('Step 3  风电出力模拟（windpowerlib）')
    wind_sim = WindSimulator(hub_height=100.0)
    wind_result: WindSimResult = wind_sim.simulate(
        wind_weather, equiv_hours=WIND_EQUIV_H, target_capacity_mw=1.0,
    )
    logger.info(f'  等效小时数校准: {wind_result.total_generation_mwh:.1f} MWh/MW'
                f'（目标 {WIND_EQUIV_H} h，K={wind_result.scale_factor:.4f}）')
    logger.info(f'  选用机型: {wind_result.selected_turbine}')

    # 每 MW 单位出力时序
    solar_unit = pv_result.output_mw    # pd.Series, MW per 1 MW installed
    wind_unit  = wind_result.output_mw  # pd.Series, MW per 1 MW installed

    # ── Step 4: 容量规划优化 ──────────────────
    logger.info('=' * 60)
    logger.info('Step 4  两阶段容量规划优化')
    logger.info(f'  约束: 绿电率 ≥ {_fmt_pct(GREEN_RATIO_MIN)}，自用率 ≥ {_fmt_pct(SELF_USE_MIN)}')
    logger.info(f'  搜索: 粗步长 {SEARCH_PARAMS["coarse_step_mw"]} MW / '
                f'{SEARCH_PARAMS["coarse_step_mwh"]} MWh，精步长 '
                f'{SEARCH_PARAMS["fine_step_mw"]} MW / {SEARCH_PARAMS["fine_step_mwh"]} MWh')

    optimizer = CapacityOptimizer(STORAGE_PARAMS, COST_PARAMS, SEARCH_PARAMS)
    plan: CapacityPlanResult = optimizer.optimize(
        load_series, wind_unit, solar_unit,
        green_ratio_min=GREEN_RATIO_MIN,
        self_use_ratio_min=SELF_USE_MIN,
    )

    logger.info('─' * 40)
    logger.info('  【最优容量方案】')
    logger.info(f'  风电装机:   {_fmt_mw(plan.wind_mw)}')
    logger.info(f'  光伏装机:   {_fmt_mw(plan.pv_mw)}')
    logger.info(f'  储能容量:   {_fmt_mwh(plan.ess_mwh)}  /  功率 {_fmt_mw(plan.ess_mw)}')
    logger.info(f'  总投资:     {_fmt_wan(plan.total_cost_wan)}')
    logger.info(f'  绿电率:     {_fmt_pct(plan.green_ratio)}')
    logger.info(f'  绿电自用率: {_fmt_pct(plan.self_use_ratio)}')
    logger.info(f'  弃电率:     {_fmt_pct(plan.curtailment_ratio)}')

    # ── Step 5: 全年时序运行仿真 ──────────────
    logger.info('=' * 60)
    logger.info('Step 5  全年时序运行仿真（储能调度）')
    metrics = simulate_operation(
        load_series, wind_unit, solar_unit,
        wind_mw=plan.wind_mw, pv_mw=plan.pv_mw,
        ess_mwh=plan.ess_mwh,
        storage_params=STORAGE_PARAMS,
    )
    total_load_mwh = float(load_series.sum())
    logger.info(f'  全年总负荷:     {total_load_mwh:.0f} MWh')
    logger.info(f'  绿电总发电量:   {metrics["total_green_gen_mwh"]:.0f} MWh')
    logger.info(f'  弃电量:         {metrics["total_curtailment_mwh"]:.0f} MWh')
    logger.info(f'  外购电量:       {metrics["total_grid_buy_mwh"]:.0f} MWh')
    logger.info(f'  绿电消纳率:     {_fmt_pct(metrics["green_ratio"])}')
    logger.info(f'  绿电自用率:     {_fmt_pct(metrics["self_use_ratio"])}')
    logger.info(f'  弃电率:         {_fmt_pct(metrics["curtailment_ratio"])}')

    # ── Step 6: 短期出力预测演示（未来 48 h）────
    logger.info('=' * 60)
    logger.info('Step 6  短期出力预测演示（未来 48 h）')

    # 用前 6000 h 训练，预测第 6001-6048 h
    history_end = 6000
    solar_history = solar_unit.iloc[:history_end] * plan.pv_mw
    wind_history  = wind_unit.iloc[:history_end]  * plan.wind_mw

    solar_fc = SolarPowerForecaster(mode='harmonic')
    solar_fc.fit(solar_history)
    solar_pred = solar_fc.predict(horizon=48)

    wind_fc = WindPowerForecaster(mode='statistical')
    wind_fc.fit(wind_history)
    wind_pred = wind_fc.predict(horizon=48)

    logger.info(f'  光伏预测（前 6 h）: '
                + '  '.join(f'{v:.3f}' for v in solar_pred.point_forecast[:6])
                + ' MW')
    logger.info(f'  光伏区间（前 6 h）: '
                + '  '.join(
                    f'[{lo:.3f},{hi:.3f}]'
                    for lo, hi in zip(
                        solar_pred.lower_quantile[:6],
                        solar_pred.upper_quantile[:6],
                    )
                ))
    logger.info(f'  风电预测（前 6 h）: '
                + '  '.join(f'{v:.3f}' for v in wind_pred.point_forecast[:6])
                + ' MW')
    logger.info(f'  风电区间（前 6 h）: '
                + '  '.join(
                    f'[{lo:.3f},{hi:.3f}]'
                    for lo, hi in zip(
                        wind_pred.lower_quantile[:6],
                        wind_pred.upper_quantile[:6],
                    )
                ))

    logger.info('=' * 60)
    logger.info('风光储一体化演示完成。')
