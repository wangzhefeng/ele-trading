from ele_trading.data.sample_data import (
    load_default_day_ahead_prices,
    load_default_intraday_prices,
    load_default_price_scenarios,
    load_default_storage_config,
)


def test_sample_data_can_load():
    day_ahead = load_default_day_ahead_prices()
    intraday = load_default_intraday_prices()
    storage = load_default_storage_config()
    scenarios = load_default_price_scenarios()

    assert len(day_ahead.prices) == 24
    assert len(intraday.prices) == 24
    assert storage.soc_max > storage.soc_min
    assert len(scenarios) > 0
