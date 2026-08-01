"""Archived user-side entrypoint smoke tests, run by explicit path only."""

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = PROJECT_ROOT / "app"


def _run_script(script_name: str, timeout: int = 120) -> subprocess.CompletedProcess:
    python = str(PROJECT_ROOT / ".venv" / "bin" / "python")
    return subprocess.run(
        [python, str(APP_DIR / script_name)],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )


def test_run_user_side_bess_dispatch():
    result = _run_script("user_side_dispatch/run_user_side_bess_dispatch.py")
    assert result.returncode == 0, result.stderr[:300]


def test_run_user_side_pv_dispatch():
    result = _run_script("user_side_dispatch/run_user_side_pv_dispatch.py")
    assert result.returncode == 0, result.stderr[:300]


def test_run_user_side_pv_bess_dispatch():
    result = _run_script("user_side_dispatch/run_user_side_pv_bess_dispatch.py")
    assert result.returncode == 0, result.stderr[:300]


def test_run_cvxp_bess_dispatch():
    result = _run_script("user_side_dispatch/run_cvxp_bess_dispatch.py")
    assert result.returncode == 0, result.stderr[:300]
