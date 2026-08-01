# Archived user-side dispatch tests

These tests cover the archived `ele_trading.user_side_dispatch` package
(user-side, distributed, and CVXPY dispatch behavior). Normal pytest discovery
excludes this directory (`norecursedirs = ["user_side_dispatch"]`); run a
specific file by explicit path when validating the archived code.

This includes `test_data_provider_investment_profiles.py` for target-year
load/profile and investment-case construction moved out of the active API.
