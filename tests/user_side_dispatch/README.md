# User-side dispatch tests

These tests cover the `ele_trading.user_side_dispatch` package (user-side,
distributed, and CVXPY dispatch behavior, plus landed-price composition in
`test_landed_price.py`). They are collected by normal pytest discovery
(`testpaths = ["tests"]` in `pyproject.toml`).

This includes `test_data_provider_investment_profiles.py` for target-year
load/profile and investment-case construction moved out of the active API.
