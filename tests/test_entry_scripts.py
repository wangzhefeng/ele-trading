"""入口脚本冒烟测试。"""

import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / 'app'


def _run_script(script_name: str, timeout: int = 120, args: list[str] | None = None) -> subprocess.CompletedProcess:
    """使用项目 .venv 运行入口脚本，返回 subprocess 结果。"""
    python = str(PROJECT_ROOT / '.venv' / 'bin' / 'python')
    script = str(APP_DIR / script_name)
    return subprocess.run(
        [python, script, *(args or [])],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )


def test_run_bess_arbitrage():
    """run_bess_arbitrage.py 应退出码为 0 且有输出。"""
    result = _run_script('optimization/run_bess_arbitrage.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0


def test_run_mpc_demo():
    """run_mpc_demo.py 应退出码为 0 且有输出。"""
    result = _run_script('optimization/run_mpc_demo.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0


def test_run_two_stage_skeleton():
    """run_two_stage_skeleton.py 应退出码为 0 且有输出。"""
    result = _run_script('optimization/run_two_stage_skeleton.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0


def test_run_backtest():
    """run_backtest.py 应退出码为 0 且有输出。"""
    result = _run_script('evaluation/run_backtest.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0


def test_run_user_side_bess_dispatch():
    """run_user_side_bess_dispatch.py 应退出码为 0 且有用户侧调度输出。"""
    result = _run_script('optimization/run_user_side_bess_dispatch.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert '用户侧储能调度' in combined or 'total_cost' in combined


def test_run_user_side_pv_dispatch():
    """run_user_side_pv_dispatch.py 应退出码为 0 且有用户侧光伏调度输出。"""
    result = _run_script('optimization/run_user_side_pv_dispatch.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert '用户侧光伏调度' in combined or 'total_cost' in combined


def test_run_user_side_pv_bess_dispatch():
    """run_user_side_pv_bess_dispatch.py 应退出码为 0 且有用户侧光伏储能调度输出。"""
    result = _run_script('optimization/run_user_side_pv_bess_dispatch.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert '用户侧光伏储能调度' in combined or 'total_cost' in combined


def test_run_wind_pv_legacy_profit_eval():
    """run_wind_pv_legacy_profit_eval.py 应退出码为 0 且输出收益测算结果。"""
    result = _run_script('legacy/run_wind_pv_legacy_profit_eval.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'annual_net_profit' in combined or '收益测算' in combined


def test_run_wind_pv_legacy_market_trading():
    """run_wind_pv_legacy_market_trading.py 应退出码为 0 且输出交易调度结果。"""
    result = _run_script('legacy/run_wind_pv_legacy_market_trading.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'market trading' in combined.lower() or 'total_cost' in combined


@pytest.mark.skip(reason="V4 canonical+settlement demo 网格较重，仅手动用 --demo 验收")
def test_run_wind_pv_bess_irr_planning():
    """run_wind_pv_bess_irr_planning.py 应退出码为 0 且输出 IRR 规划结果。"""
    result = _run_script('capacity_planning/run_wind_pv_bess_irr_planning.py', args=["--demo"])
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'IRR 目标型 Wind+PV+BESS' in combined or 'wind_mw' in combined


# --- 以下为补充的入口脚本冒烟测试 ---


def test_run_pv_simulation_v1():
    """run_pv_simulation_v1.py 应退出码为 0 且输出光伏仿真结果。"""
    result = _run_script('resource_simulation/run_pv_simulation_v1.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'pv simulation' in combined.lower() or 'pv_kw' in combined.lower()


def test_run_cvxp_bess_dispatch():
    """run_cvxp_bess_dispatch.py 应退出码为 0 且输出 CVXPY 调度结果。"""
    result = _run_script('optimization/run_cvxp_bess_dispatch.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'CVXPY' in combined or 'objective_value' in combined


def test_run_bess_capacity_planning():
    """run_bess_capacity_planning.py 应退出码为 0（合成数据，无需外部依赖）。"""
    result = _run_script('capacity_planning/run_bess_capacity_planning.py', timeout=180)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'


def test_run_wind_bess_capacity_planning():
    """run_wind_bess_capacity_planning.py 应退出码为 0（合成数据，无需外部依赖）。"""
    result = _run_script('capacity_planning/run_wind_bess_capacity_planning.py', timeout=180)
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'


@pytest.mark.skip(reason="需要 Open-Meteo 网络 API 访问，CI 环境可能不可用")
def test_run_pv_simulation_v2():
    """run_pv_simulation_v2.py 需要 Open-Meteo API，仅手动验收。"""
    result = _run_script('resource_simulation/run_pv_simulation_v2.py')
    assert result.returncode == 0


@pytest.mark.skip(reason="需要 Open-Meteo 网络 API 访问，CI 环境可能不可用")
def test_run_wind_simulation_v1():
    """run_wind_simulation_v1.py 需要 Open-Meteo API，仅手动验收。"""
    result = _run_script('resource_simulation/run_wind_simulation_v1.py')
    assert result.returncode == 0


@pytest.mark.skip(reason="需要 Open-Meteo 网络 API 访问，CI 环境可能不可用")
def test_run_wind_simulation_v2():
    """run_wind_simulation_v2.py 需要 Open-Meteo API，仅手动验收。"""
    result = _run_script('resource_simulation/run_wind_simulation_v2.py')
    assert result.returncode == 0


@pytest.mark.skip(reason="运行时间 >30s，仅手动验收")
def test_run_wind_pv_bess_capacity_planning_1():
    """run_wind_pv_bess_capacity_planning_1.py 二维搜索较重，仅手动验收。"""
    result = _run_script('capacity_planning/run_wind_pv_bess_capacity_planning_1.py', timeout=300)
    assert result.returncode == 0


@pytest.mark.skip(reason="运行时间 >30s，仅手动验收")
def test_run_wind_pv_bess_capacity_planning_2():
    """run_wind_pv_bess_capacity_planning_2.py 三场景演示较重，仅手动验收。"""
    result = _run_script('capacity_planning/run_wind_pv_bess_capacity_planning_2.py', timeout=300)
    assert result.returncode == 0


@pytest.mark.skip(reason="需要 data/profit_calc/dist_es/ 外部数据文件，仅手动验收")
def test_run_dist_bess_dispatch():
    """run_dist_bess_dispatch.py 需要外部 CSV 数据，仅手动验收。"""
    result = _run_script('capacity_planning/run_dist_bess_dispatch.py', timeout=300)
    assert result.returncode == 0
