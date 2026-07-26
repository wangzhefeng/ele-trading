# Inactive user-side sample data

## Boundary and source

This package was moved from the active `data_provider/user_side_*.py`,
`data_provider/cvxp_bess_sample.py`, and investment-only
`data_provider/case_dataset.py`, target-year `load_profile.py`, and resource
profile contracts/loaders. It archives deterministic user-side and CVXPY
sample-input builders plus investment/profile construction. These are outside
the active v2 data-provider API. Consumers must import explicit
`ele_trading.data_provider.todo` paths.

## Dependencies and validation

- Non-CVXPY builders import without CVXPY.
- CVXPY sample builders are lazily loaded; install them with
  `uv sync --extra archived-user-side` before using a CVXPY builder.
- Last valid focused regression: `tests/todo/test_cvxp_bess_dispatch.py` and
  the user-side builder tests selected in Task 2 (32 passing focused tests).

## Restore condition

Do not re-export this package from active `data_provider`. Restore a builder
only after a v2 owner, active entrypoint/configuration, and normal-discovery
test contract are approved together.
