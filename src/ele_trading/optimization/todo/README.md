# Inactive user-side optimization

## Boundary and source

This directory was moved from the active `optimization/user_side_*.py` modules
in Phase 1A. It archives user-side, distributed, and CVXPY dispatch code. It
is excluded from the active v2 API; consumers must import explicit
`ele_trading.optimization.todo` paths.

## Dependencies and validation

- PuLP-backed archived modules import without CVXPY.
- CVXPY and distributed entrypoints are lazily loaded; install them with
  `uv sync --extra archived-user-side` before use.
- Last valid focused regression: the selected archived suite in
  `tests/todo/` passed 32 tests; four non-legacy archived entrypoint smoke
  tests also passed.

## Restore condition

Do not add compatibility exports to active `optimization`. Restore any module
only with an approved v2 owner, active app/config path, normal test coverage,
and a decision to make its dependencies active again.
