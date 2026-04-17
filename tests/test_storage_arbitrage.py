from ele_trading.data.sample_data import load_default_day_ahead_prices, load_default_storage_config
from ele_trading.optimization.storage_arbitrage import solve_storage_arbitrage


def test_storage_arbitrage_returns_non_empty_result():
    prices = load_default_day_ahead_prices()
    storage = load_default_storage_config()

    result = solve_storage_arbitrage(
        prices=prices.prices,
        soc0=storage.soc0,
        soc_min=storage.soc_min,
        soc_max=storage.soc_max,
        p_ch_max=storage.p_ch_max,
        p_dis_max=storage.p_dis_max,
        eta_ch=storage.eta_ch,
        eta_dis=storage.eta_dis,
        deg_cost=storage.deg_cost,
        dt=storage.dt,
    )

    assert result['objective'] is not None
    assert len(result['p_ch']) == len(prices.prices)
    assert len(result['soc']) == len(prices.prices)
