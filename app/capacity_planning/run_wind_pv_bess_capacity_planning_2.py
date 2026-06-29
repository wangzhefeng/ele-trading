"""风光储一体化容量规划运行脚本

从 configs/capacity_planning/capacity_planning.yaml 加载参数，演示三个应用场景：
  A. 风光储联合优化（北京工业用户，默认参数）
  B. PV-only 最小投资（南方园区，无风电资源）
  C. 高绿电率碳中和方案（出口型企业）

流程：生成合成气象 → 光伏/风电出力模拟 → 容量规划优化 → 时序运行仿真
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import numpy as np
import pandas as pd

from ele_trading.resource_simulation import (
    PVSimulator, SimulationResult,
    WindSimulator,
)
from ele_trading.capacity_planning.wind_pv_bess_capacity_optimizer import (
    CapacityOptimizer, CapacityPlanResult, simulate_operation,
)
from ele_trading.forecasting.pv_forecast import PVPowerForecaster
from ele_trading.forecasting.wind_forecast import WindPowerForecaster
from ele_trading.utils.io import read_yaml
from ele_trading.utils.log_util import logger

CONFIG_PATH = PROJECT_ROOT / 'configs' / 'capacity_planning' / 'capacity_planning.yaml'


# ─────────────────────────────────────────────
# 格式化工具
# ─────────────────────────────────────────────

def _fmt_mw(v: float) -> str:
    return f'{v:.1f} MW'

def _fmt_mwh(v: float) -> str:
    return f'{v:.1f} MWh'

def _fmt_pct(v: float) -> str:
    return f'{v * 100:.1f} %'

def _fmt_wan(v: float) -> str:
    return f'{v:.0f} 万元'


# ─────────────────────────────────────────────
# 合成数据生成
# ─────────────────────────────────────────────

def _make_pv_weather(n_hours: int, timezone: str,
                        rng: np.random.Generator) -> pd.DataFrame:
    """合成光伏气象数据（GHI 用简化日循环模型）。"""
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
    """合成风电气象数据（Weibull 风速分布）。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=timezone)
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    seasonal = 1.1 - 0.2 * np.sin(2 * np.pi * day_of_year)
    wind_speed = rng.weibull(2.0, n_hours) * 7 * seasonal
    return pd.DataFrame({
        'wind_speed':  np.clip(wind_speed, 0, 30),
        'temperature': 10 + 15 * np.sin(2 * np.pi * (day_of_year - 0.25)) + rng.normal(0, 2, n_hours),
        'pressure':    rng.uniform(99000, 101325, n_hours),
    }, index=idx)


def _make_load(n_hours: int, timezone: str, load_mean_mw: float) -> pd.Series:
    """合成工业负荷：日波动 + 季节波动 + 随机扰动。"""
    idx = pd.date_range('2023-01-01', periods=n_hours, freq='h', tz=timezone)
    hours = np.arange(n_hours) % 24
    day_of_year = (idx.dayofyear.values - 1) / 365.0
    daily = 0.85 + 0.15 * np.sin(np.pi * (hours - 8) / 10) * (hours >= 8) * (hours < 22)
    seasonal = 1.0 + 0.1 * np.sin(2 * np.pi * day_of_year)
    rng = np.random.default_rng(7)
    noise = rng.normal(0, 0.02, n_hours)
    load = load_mean_mw * daily * seasonal + noise
    return pd.Series(np.clip(load, 5.0, None), index=idx, name='load_mw')


# ─────────────────────────────────────────────
# 单场景运行
# ─────────────────────────────────────────────

def run_scenario(name: str, config: dict, rng: np.random.Generator,
                 forecast: bool = False) -> dict:
    """运行单个容量规划场景，返回结果摘要。"""
    sc = config['scenario']
    cst = config['constraints']
    bess_params = config["bess"]
    cost_params = config['cost']
    search_params = config['search']

    n_hours = sc['n_hours']
    timezone = sc['timezone']
    latitude = sc['latitude']
    longitude = sc['longitude']
    pv_eq = sc['pv_equiv_hours']
    wind_eq = sc['wind_equiv_hours']
    load_mean = sc['load_mean_mw']
    green_min = cst['green_ratio_min']
    self_use_min = cst['self_use_ratio_min']

    fixed_wind = config.get('fixed_wind_mw')
    fixed_pv = config.get('fixed_pv_mw')

    logger.info('=' * 60)
    logger.info(f'场景 {name}')
    logger.info('=' * 60)

    # ── Step 1: 生成气象数据 ──────────────────
    logger.info('Step 1  生成合成气象数据')
    pv_weather = _make_pv_weather(n_hours, timezone, rng)
    wind_weather = _make_wind_weather(n_hours, timezone, rng)
    load_series = _make_load(n_hours, timezone, load_mean)
    logger.info(f'  气象时序长度: {n_hours} h，负荷均值: {load_series.mean():.2f} MW')

    # ── Step 2: 光伏出力模拟（1 MW 基准）────────
    logger.info('Step 2  光伏出力模拟（pvlib）')
    pv_sim = PVSimulator(
        latitude=latitude, longitude=longitude,
        timezone=timezone, altitude=50.0,
    )
    pv_result: SimulationResult = pv_sim.simulate(
        pv_weather, equiv_hours=pv_eq, target_capacity_mw=1.0,
    )
    logger.info(f'  等效小时数校准: {pv_result.total_generation_mwh:.1f} MWh/MW'
                f'（目标 {pv_eq} h，K={pv_result.scale_factor:.4f}）')

    # ── Step 3: 风电出力模拟（1 MW 基准）────────
    pv_unit = pv_result.power_series / 1000.0  # kW → per-unit MW
    if fixed_wind == 0.0:
        logger.info('Step 3  风电出力模拟（跳过，fixed_wind_mw=0）')
        wind_unit = pd.Series(0.0, index=pv_unit.index)
    else:
        logger.info('Step 3  风电出力模拟（windpowerlib）')
        wind_sim = WindSimulator(hub_height=100.0)
        wind_result: SimulationResult = wind_sim.simulate(
            wind_weather, equiv_hours=wind_eq, target_capacity_mw=1.0,
        )
        logger.info(f'  等效小时数校准: {wind_result.total_generation_mwh:.1f} MWh/MW'
                    f'（目标 {wind_eq} h，K={wind_result.scale_factor:.4f}）')
        logger.info(f'  选用机型: {wind_result.selected_turbine}')
        wind_unit = wind_result.power_series / 1000.0  # kW → per-unit MW

    # ── Step 4: 容量规划优化 ──────────────────
    logger.info('Step 4  两阶段容量规划优化')
    logger.info(f'  约束: 绿电率 ≥ {_fmt_pct(green_min)}，自用率 ≥ {_fmt_pct(self_use_min)}')
    logger.info(f'  搜索: 粗步长 {search_params["coarse_step_mw"]} MW / '
                f'{search_params["coarse_step_mwh"]} MWh，精步长 '
                f'{search_params["fine_step_mw"]} MW / {search_params["fine_step_mwh"]} MWh')

    optimizer = CapacityOptimizer(bess_params, cost_params, search_params)
    plan: CapacityPlanResult = optimizer.optimize(
        load_series, wind_unit, pv_unit,
        green_ratio_min=green_min,
        self_use_ratio_min=self_use_min,
        fixed_wind_mw=fixed_wind,
        fixed_pv_mw=fixed_pv,
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
    logger.info('Step 5  全年时序运行仿真（储能调度）')
    metrics = simulate_operation(
        load_series, wind_unit, pv_unit,
        wind_mw=plan.wind_mw, pv_mw=plan.pv_mw,
        ess_mwh=plan.ess_mwh,
        bess_params=bess_params,
    )
    total_load_mwh = float(load_series.sum())
    logger.info(f'  全年总负荷:     {total_load_mwh:.0f} MWh')
    logger.info(f'  绿电总发电量:   {metrics["total_green_gen_mwh"]:.0f} MWh')
    logger.info(f'  弃电量:         {metrics["total_curtailment_mwh"]:.0f} MWh')
    logger.info(f'  外购电量:       {metrics["total_grid_buy_mwh"]:.0f} MWh')
    logger.info(f'  绿电消纳率:     {_fmt_pct(metrics["green_ratio"])}')
    logger.info(f'  绿电自用率:     {_fmt_pct(metrics["self_use_ratio"])}')
    logger.info(f'  弃电率:         {_fmt_pct(metrics["curtailment_ratio"])}')

    result = {
        'name': name,
        'plan': plan,
        'metrics': metrics,
    }

    # ── Step 6（可选）: 短期出力预测演示 ──────
    if forecast and plan.wind_mw > 0 and plan.pv_mw > 0:
        logger.info('Step 6  短期出力预测演示（未来 48 h）')
        history_end = 6000
        pv_history = pv_unit.iloc[:history_end] * plan.pv_mw
        wind_history = wind_unit.iloc[:history_end] * plan.wind_mw

        pv_fc = PVPowerForecaster(mode='harmonic')
        pv_fc.fit(pv_history)
        pv_pred = pv_fc.predict(horizon=48)

        wind_fc = WindPowerForecaster(mode='statistical')
        wind_fc.fit(wind_history)
        wind_pred = wind_fc.predict(horizon=48)

        logger.info(f'  光伏预测（前 6 h）: '
                    + '  '.join(f'{v:.3f}' for v in pv_pred.point_forecast[:6])
                    + ' MW')
        logger.info(f'  风电预测（前 6 h）: '
                    + '  '.join(f'{v:.3f}' for v in wind_pred.point_forecast[:6])
                    + ' MW')

    return result


# ─────────────────────────────────────────────
# 多场景演示
# ─────────────────────────────────────────────

if __name__ == '__main__':

    rng = np.random.default_rng(42)
    base_config = read_yaml(CONFIG_PATH)

    # ─────────────────────────────────────────────
    # 场景 A：风光储联合优化（北京工业用户）
    # ─────────────────────────────────────────────
    # 直接使用 YAML 默认参数，不做修改
    run_scenario('A - 风光储联合优化（北京工业用户）', base_config, rng, forecast=True)

    # ─────────────────────────────────────────────
    # 场景 B：PV-only 最小投资（南方园区无风电资源）
    # ─────────────────────────────────────────────
    cfg_b = copy.deepcopy(base_config)
    cfg_b['scenario']['latitude'] = 22.5
    cfg_b['scenario']['longitude'] = 114.0
    cfg_b['scenario']['pv_equiv_hours'] = 1400.0
    cfg_b['scenario']['wind_equiv_hours'] = 0.0
    cfg_b['scenario']['load_mean_mw'] = 10.0
    cfg_b['constraints']['green_ratio_min'] = 0.20
    cfg_b['constraints']['self_use_ratio_min'] = 0.85
    cfg_b['search']['max_wind_mw'] = 0
    cfg_b['search']['max_pv_mw'] = 50
    cfg_b['search']['max_ess_mwh'] = 0
    cfg_b['search']['coarse_step_mwh'] = 1
    cfg_b['fixed_wind_mw'] = 0.0
    run_scenario('B - PV-only 最小投资（南方园区）', cfg_b, rng)

    # ─────────────────────────────────────────────
    # 场景 C：高绿电率碳中和方案（出口型企业）
    # ─────────────────────────────────────────────
    cfg_c = copy.deepcopy(base_config)
    cfg_c['constraints']['green_ratio_min'] = 0.80
    cfg_c['constraints']['self_use_ratio_min'] = 0.70
    cfg_c['search']['max_wind_mw'] = 120
    cfg_c['search']['max_pv_mw'] = 120
    cfg_c['search']['max_ess_mwh'] = 200
    cfg_c["bess"]['c_rate'] = 0.25
    cfg_c["bess"]['duration_hours'] = 4.0
    run_scenario('C - 高绿电率碳中和（出口型企业）', cfg_c, rng)

    logger.info('=' * 60)
    logger.info('全部场景演示完成。')
