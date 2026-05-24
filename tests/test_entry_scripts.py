"""入口脚本冒烟测试。"""

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / 'app'


def _run_script(script_name: str) -> subprocess.CompletedProcess:
    """使用项目 .venv 运行入口脚本，返回 subprocess 结果。"""
    python = str(PROJECT_ROOT / '.venv' / 'bin' / 'python')
    script = str(APP_DIR / script_name)
    return subprocess.run(
        [python, script],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(PROJECT_ROOT),
    )


def test_run_storage_arbitrage():
    """run_storage_arbitrage.py 应退出码为 0 且有输出。"""
    result = _run_script('run_storage_arbitrage.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0


def test_run_mpc_demo():
    """run_mpc_demo.py 应退出码为 0 且有输出。"""
    result = _run_script('run_mpc_demo.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0


def test_run_two_stage_skeleton():
    """run_two_stage_skeleton.py 应退出码为 0 且有输出。"""
    result = _run_script('run_two_stage_skeleton.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0


def test_run_backtest():
    """run_backtest.py 应退出码为 0 且有输出。"""
    result = _run_script('run_backtest.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert len(combined) > 0
