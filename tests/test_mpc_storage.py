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

    assert set(result) == {'p_ch', 'p_dis', 'soc_next', 'obj'}
