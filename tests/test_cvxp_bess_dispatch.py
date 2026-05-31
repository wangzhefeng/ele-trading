import pytest

from ele_trading.data_provider.cvxp_bess_sample import build_cvxp_bess_dispatch_input
from ele_trading.optimization.user_side_bess_dispatch_cvxpy import run_cvxp_bess_dispatch
from ele_trading.optimization.interfaces import (
    CvxpBESSDispatchInput,
    CvxpBESSProfile,
    UserSideBESSParams,
)
from ele_trading.utils.io import read_yaml


def _bess() -> UserSideBESSParams:
    return UserSideBESSParams(
        capacity=10.0,
        soc_min=0.0,
        soc_max=10.0,
        p_ch_max=5.0,
        p_dis_max=5.0,
        eta_ch=1.0,
        eta_dis=1.0,
    )


def test_data_provider_package_exports_cvxp_builders_without_stale_loader():
    """data_provider 包级导入不应依赖已移除的 CVXP YAML loader。"""
    import ele_trading.data_provider as data_provider

    assert hasattr(data_provider, "build_synthetic_cvxp_dispatch_frame")
    assert hasattr(data_provider, "build_cvxp_bess_dispatch_input")
    assert not hasattr(data_provider, "load_cvxp_bess_dispatch_config")


def test_cvxp_dispatch_charges_from_grid_before_high_price_discharge():
    """初始 SOC 为 0 时，低价充电应增加 SOC，并支持后续高价放电。"""
    dispatch_input = CvxpBESSDispatchInput(
        timestamps=[0, 1, 2],
        demand_load=[10.0, 10.0, 10.0],
        ele_prices=[0.1, 1.0, 1.0],
        ele_types=["valley", "peak", "peak"],
        bess=_bess(),
        initial_soc=0.0,
        max_demand_price=0.0,
        freq_minutes=60,
        profile=CvxpBESSProfile(demand_charge_type="none"),
    )

    result = run_cvxp_bess_dispatch(dispatch_input)

    assert result.charge_power[0] > 0.0
    assert result.soc[0] == pytest.approx(result.charge_power[0])
    assert sum(result.discharge_power[1:]) > 0.0
    assert result.soc[-1] == pytest.approx(0.0, abs=1e-6)


def test_cvxp_result_power_outputs_are_non_negative_business_values():
    """输出给业务侧的充电和放电功率应清理求解器容差噪声。"""
    config = read_yaml("configs/cvxp_bess_dispatch.yaml")
    dispatch_input = build_cvxp_bess_dispatch_input(config)

    result = run_cvxp_bess_dispatch(dispatch_input)

    assert min(result.charge_power) >= 0.0
    assert min(result.discharge_power) >= 0.0
