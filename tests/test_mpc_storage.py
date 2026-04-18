from ele_trading.data.sample_data import load_default_intraday_prices, load_default_storage_config
from ele_trading.optimization.mpc_storage import solve_one_mpc_window


def test_mpc_window_returns_required_fields():
    prices = load_default_intraday_prices()
    storage = load_default_storage_config()

    result = solve_one_mpc_window(
        prices_window=prices.prices[:4],
        soc0=storage.soc0,
        horizon=4,
        soc_min=storage.soc_min,
        soc_max=storage.soc_max,
        p_ch_max=storage.p_ch_max,
        p_dis_max=storage.p_dis_max,
        eta_ch=storage.eta_ch,
        eta_dis=storage.eta_dis,
        deg_cost=storage.deg_cost,
        dt=storage.dt,
    )

    assert {'p_ch', 'p_dis', 'soc_next', 'soc_terminal', 'obj'}.issubset(set(result))


def test_terminal_soc_constraint_prevents_over_discharge():
    """启用终端约束后，MPC 窗口末端 SOC 不应低于阈值。"""
    # 价格后段高 → 不加终端约束时求解器倾向于在末段前耗尽电量
    prices_window = [100.0, 100.0, 100.0, 500.0]
    result = solve_one_mpc_window(
        prices_window=prices_window,
        soc0=10.0,
        horizon=4,
        soc_min=1.0,
        soc_max=10.0,
        p_ch_max=3.0,
        p_dis_max=3.0,
        eta_ch=0.95,
        eta_dis=0.95,
        deg_cost=0.01,
        dt=1.0,
        terminal_soc_fraction=0.3,
    )
    # 终端 SOC 阈值 = soc_min + 0.3*(soc_max - soc_min) = 1 + 0.3*9 = 3.7
    assert result['soc_terminal'] >= 3.7 - 1e-4
