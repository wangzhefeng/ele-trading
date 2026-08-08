"""V5-9 多资源求解失败透明测试。"""

from __future__ import annotations

import numpy as np

from ele_trading.operations.multi_resource import BESSUnit, solve_multi_resource
from ele_trading.optimization.solver import SolveStatus


def test_multi_resource_reports_infeasible_solve_without_zero_schedule():
    """不可行模型不得把未取值变量伪装成零调度。"""
    result = solve_multi_resource(
        load_mwh=np.array([0.0]),
        price=np.array([100.0]),
        bess_units=(
            BESSUnit(
                name="bess-a",
                soc0=1.0,
                soc_min=0.0,
                soc_max=1.0,
                p_charge_max=1.0,
                p_discharge_max=1.0,
                eta_charge=1.0,
                eta_discharge=1.0,
                terminal_soc_min=2.0,
            ),
        ),
        dt=1.0,
    )

    assert result.solve_result.status is SolveStatus.INFEASIBLE
    assert result.resource_schedules == {}
    assert result.grid_import_mwh is None
    assert result.expected_cost is None
