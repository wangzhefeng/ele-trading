# Inactive v1 dual-settlement archive

This directory is a traceability-only snapshot of the former active Mengxi
dual-settlement chain. Active source, apps, and normal tests must not import
`ele_trading.trading.todo`.

## Archived source

- `settlement_mengxi.py`: `compute_settlement_C`,
  `compute_settlement_C2`, `compute_cpen_dayah`, and
  `compute_cpen_long`.
- `contracts.py`, `config_loader.py`, and `market_mengxi.yaml`: the matching
  v1 contracts and configuration.
- `day_ahead_coupled.py`, `intraday_rolling.py`, `backtest.py`, and
  `noisy_backcast.py`: the former dual-settlement decision and backtest path.
- `apps/`: the former active day-ahead, intraday, and backtest entry scripts.
- `tests/`: the regression fixtures moved with the implementation.

## Dependencies

The archive requires NumPy, pandas, PuLP with CBC, PyYAML, and the repository
utility functions used by the original implementation. It is intentionally
not exported by the active trading package.

## Last explicit tests

On 2026-07-26:

```text
UV_CACHE_DIR=.uv_cache uv run pytest -q \
  src/ele_trading/trading/todo/dual_settlement_v1/tests
56 passed, 1 skipped in 4.79s
```

## Recovery condition

Recover this code only to reproduce or audit a historical v1 result. Any
reactivation requires a separately approved market-rule change, a new active
design, and migration tests. Do not import the archive to repair or extend the
single-settlement main line.
