# Inactive user-side tests

These tests cover archived user-side, distributed, and CVXPY behavior. Normal
pytest discovery excludes this directory; run a specific file by explicit path
when validating the archived code.

This includes `test_data_provider_investment_profiles.py` for target-year
load/profile and investment-case construction moved out of the active API.
