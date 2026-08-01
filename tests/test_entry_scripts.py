"""入口脚本冒烟测试。"""

import subprocess
from pathlib import Path

import pytest

from ele_trading.markets.single_settlement.config_loader import load_market_config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / 'app'
IE_APP_DIR = PROJECT_ROOT / 'src' / 'investment_estimation' / 'app'


def _run_script(script_name: str, timeout: int = 120, args: list[str] | None = None) -> subprocess.CompletedProcess:
    """使用项目 .venv 运行入口脚本，返回 subprocess 结果。"""
    python = str(PROJECT_ROOT / '.venv' / 'bin' / 'python')
    # optimization/trading/user_side_dispatch 入口在根 app/，capacity_planning 入口在 investment_estimation 包内
    base = IE_APP_DIR if script_name.startswith('capacity_planning/') else APP_DIR
    script = str(base / script_name)
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


def test_two_stage_skeleton_uses_market_config_deviation_costs(tmp_path):
    """修改 market YAML 后，two-stage 模型偏差成本系数应随之改变。"""
    from app.optimization.run_two_stage_skeleton import build_demo_model

    source = PROJECT_ROOT / "configs" / "markets" / "single_settlement.yaml"
    models = []
    for name, positive_cost, negative_cost in (
        ("lower", 2.0, 3.0),
        ("higher", 5.0, 7.0),
    ):
        configured = source.read_text()
        configured = configured.replace(
            "  two_stage_scenario_deviation_cost_positive: 0.25",
            f"  two_stage_scenario_deviation_cost_positive: {positive_cost}",
        )
        configured = configured.replace(
            "  two_stage_scenario_deviation_cost_negative: 0.25",
            f"  two_stage_scenario_deviation_cost_negative: {negative_cost}",
        )
        path = tmp_path / f"{name}.yaml"
        path.write_text(configured)
        models.append(build_demo_model(load_market_config(path)))

    coefficients = []
    for model in models:
        variables = {item.name: item for item in model.variables()}
        coefficients.append(
            (
                model.objective.get(variables["deviation_positive_0_0"]),
                model.objective.get(variables["deviation_negative_0_0"]),
            )
        )

    assert coefficients[0] == pytest.approx((0.4, 0.6))
    assert coefficients[1] == pytest.approx((1.0, 1.4))


@pytest.mark.skip(reason="V4 canonical+settlement demo 网格较重，仅手动用 --demo 验收")
def test_run_wind_pv_bess_irr_planning():
    """run_wind_pv_bess_irr_planning.py 应退出码为 0 且输出 IRR 规划结果。"""
    result = _run_script('capacity_planning/run_wind_pv_bess_irr_planning.py', args=["--demo"])
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'IRR 目标型 Wind+PV+BESS' in combined or 'wind_mw' in combined


# --- 以下为补充的入口脚本冒烟测试 ---


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


# ---------------------------------------------------------------------------
# 蒙西交易主线入口（v1.3 §11.4.6）
# ---------------------------------------------------------------------------


def test_run_pipeline():
    """统一单结算入口应退出 0 并输出完整链路汇总。"""
    result = _run_script(
        'trading/run_pipeline.py',
        args=['--scenario-count', '2'],
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'single-settlement pipeline' in combined


def test_run_mid_long():
    """app/trading/run_mid_long.py 应退出码为 0 且输出占比。"""
    result = _run_script('trading/run_mid_long.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'α_long' in combined


def test_run_monthly():
    """app/trading/run_monthly.py 应退出码为 0 且输出阶梯与走廊。"""
    result = _run_script('trading/run_monthly.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert '集中竞价阶梯申报' in combined
    assert '量价走廊' in combined


def test_run_dr():
    """app/trading/run_dr.py 应退出码为 0 且输出参与决策。"""
    result = _run_script('trading/run_dr.py')
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert '参与' in combined


def test_run_day_ahead():
    """app/trading/run_day_ahead.py 应退出 0 并输出日前运行计划摘要。"""
    result = _run_script(
        'trading/run_day_ahead.py',
        args=['--scenario-count', '2'],
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'day-ahead operational plan' in combined


def test_run_intraday():
    """app/trading/run_intraday.py 应退出 0 并输出日内滚动计划摘要。"""
    result = _run_script(
        'trading/run_intraday.py',
        args=['--scenario-count', '2'],
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'intraday rolling plan' in combined


def test_run_backtest(tmp_path):
    """app/trading/run_backtest.py 应退出 0 并写出报告+manifest（小样本）。"""
    out_dir = tmp_path / 'bt'
    result = _run_script(
        'trading/run_backtest.py',
        args=[
            '--days', '2',
            '--scenario-count', '2',
            '--out-dir', str(out_dir),
        ],
        timeout=180,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f'stderr: {result.stderr[:300]}'
    assert 'walk-forward backtest' in combined
    assert (out_dir / 'backtest_report.csv').exists()
    assert (out_dir / 'manifest.json').exists()
