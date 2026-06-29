import pytest

from ele_trading.data_provider.cvxp_bess_sample import build_cvxp_bess_dispatch_input
from ele_trading.optimization.user_side_bess_dispatch_cvxpy import (
    CvxpBESSDispatcher,
    run_cvxp_bess_dispatch,
)
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


def test_cvxp_dispatcher_solve_matches_function_wrapper():
    """class 求解结果应与兼容函数入口保持完全一致。"""
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

    class_result = CvxpBESSDispatcher(dispatch_input).solve()
    function_result = run_cvxp_bess_dispatch(dispatch_input)

    assert class_result == function_result


@pytest.mark.parametrize(
    ("profile", "transform_capacity"),
    [
        (
            CvxpBESSProfile(
                demand_charge_type="none",
                transformer_capacity_constraint=False,
                demand_peak_guard_constraint=True,
            ),
            0.0,
        ),
        (
            CvxpBESSProfile(
                demand_charge_type="approx_min_charge",
                smoothing_enabled=True,
                transformer_capacity_constraint=False,
                demand_peak_guard_constraint=False,
            ),
            0.0,
        ),
        (
            CvxpBESSProfile(
                demand_charge_type="exact_max_net",
                transformer_capacity_constraint=True,
                demand_peak_guard_constraint=False,
            ),
            12.0,
        ),
    ],
)
def test_cvxp_dispatcher_profiles_keep_result_shape(profile, transform_capacity):
    """不同 profile 下 class 路径都应返回稳定的结果维度。"""
    dispatch_input = CvxpBESSDispatchInput(
        timestamps=[0, 1, 2, 3],
        demand_load=[10.0, 8.0, 12.0, 9.0],
        ele_prices=[0.2, 0.3, 1.0, 0.4],
        ele_types=["valley", "flat", "peak", "flat"],
        bess=_bess(),
        initial_soc=5.0,
        max_demand_price=10.0,
        freq_minutes=60,
        profile=profile,
        transform_capacity=transform_capacity,
    )

    result = CvxpBESSDispatcher(dispatch_input).solve()

    assert len(result.charge_power) == len(dispatch_input.timestamps)
    assert len(result.discharge_power) == len(dispatch_input.timestamps)
    assert len(result.net_power) == len(dispatch_input.timestamps)
    assert len(result.soc) == len(dispatch_input.timestamps)
