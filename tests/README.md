# tests/ 测试目录说明

本目录包含 `ele-trading` 项目的单元测试和集成测试，按被测包归属组织子目录，
与 `src/ele_trading/`、`src/investment_estimation/` 的包结构一一对应。

## 目录结构

| 子目录 | 被测包 | 内容 |
|--------|--------|------|
| `tests/optimization/` | `ele_trading.optimization` | BESS 套利、MPC、Two-stage CVaR 数学内核 |
| `tests/forecasting/` | `ele_trading.forecasting` | 价格/负荷/可再生/天气特征预测与 provider 契约 |
| `tests/scenario/` | `ele_trading.scenario` | LHS 采样、Kantorovich 缩减、联合场景×优化 |
| `tests/data_provider/` | `ele_trading.data_provider` | 数据质量、加载泛化 |
| `tests/utils/` | `ele_trading.utils` | 数据对齐、数值、时间索引工具 |
| `tests/investment_estimation/` | `investment_estimation` | 容量规划、IRR、资源仿真、调度、绘图 |
| `tests/trading/` | `ele_trading.trading`（编排层及全链） | 单结算交易链路（含 DR 联合优化、结算、编排、故障模式、性能） |
| `tests/positions/` | `ele_trading.positions` | 中长期分解与月度交易 |
| `tests/backtest/` | `ele_trading.backtest` | 交易/BESS 指标、30 天回测回归基线 |
| `tests/markets/` | `ele_trading.markets` | 双结算插件公式与配置加载 |
| `tests/user_side_dispatch/` | 归档 | 用户侧/分布式/CVXPY 测试，常规收集排除 |
| 根目录 | 跨包 | 项目结构边界、契约、入口冒烟、YAML 纪律 |

混合覆盖文件按主体归属：`test_dr_forecast.py`（forecasting，含少量 DR
allocator 用例）、`test_weather.py`（forecasting，主体 weather_feature，
含少量 weather_io 用例）、`test_data_layer_generalization.py`
（data_provider，含 forecasting/investment_estimation 泛化回归）。

## 运行方式

```bash
# 运行全部测试
uv run python -m pytest -q

# 按子目录运行
uv run python -m pytest tests/optimization/ -q
uv run python -m pytest tests/forecasting/ -q
uv run python -m pytest tests/scenario/ -q
uv run python -m pytest tests/data_provider/ -q
uv run python -m pytest tests/utils/ -q
uv run python -m pytest tests/investment_estimation/ -q
uv run python -m pytest tests/trading/ -q

# 运行指定测试文件
uv run python -m pytest tests/optimization/test_bess_arbitrage.py -v

# 运行 slow 标记测试（默认 deselect）
uv run python -m pytest tests/ -m slow -q

# 运行 archived 用户侧测试（常规收集明确排除）
uv run python -m pytest tests/user_side_dispatch/test_user_side_bess_dispatch.py -q

# 运行指定测试函数
uv run python -m pytest tests/backtest/test_metrics.py::test_sharpe_finite -v
```

## 测试文件清单

### `tests/optimization/` — 数学内核

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_bess_arbitrage.py` | `optimization/bess_arbitrage.py` | 储能套利优化（含 dt=0.25 15 分钟步长） |
| `test_mpc_bess.py` | `optimization/mpc_bess.py` | MPC 滚动优化（含终端 SOC 约束） |
| `test_two_stage.py` | `optimization/two_stage_cvar.py` | 两阶段 CVaR 优化（需 glpk/cbc 求解器） |

### `tests/forecasting/` — 预测

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_forecasting.py` | `forecasting/price_forecast.py`、`pv_forecast.py`、`wind_forecast.py` | 价格/光伏/风电预测 |
| `test_dr_forecast.py` | `forecasting/contracts.py`、`provider.py`、`demand_response/allocator.py` | 请求/结果 API、无前瞻约束、DR 事后评估 |
| `test_weather.py` | `forecasting/weather_feature.py`、`data_provider/weather_io.py` | 天气特征工程与数据读取 |
| `test_v2_phase3_forecasting.py` | forecasting weather/price/load/renewable/provider/metrics | Phase 3 完整预测能力与逐场景 RED/GREEN 回归 |
| `test_v2_phase3_review_fixes.py` | Phase 3 provenance/timezone/history/compatibility/ARIMA/weather input contracts | Round 1/2 评审问题的 RED/GREEN 防回归 |

### `tests/scenario/` — 场景

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_scenario.py` | `scenario/sampler.py`、`scenario/reduction.py` | LHS 采样 + Cholesky 相关性 + Kantorovich 缩减 |
| `test_v2_phase4_scenario_optimization.py` | joint scenario/reduction/BESS/CVaR/solver | Phase 4 联合场景、概率转移、物理约束与求解失败 |
| `test_v2_phase4_review_fixes.py` | Phase 4 median/provenance/reduction/penalty/version contracts | Phase 4 Round 1 评审问题的 RED/GREEN 防回归 |

### `tests/data_provider/` — 数据层

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_data_provider_quality.py` | `data_provider/quality.py` | 数据质量检查 |
| `test_data_layer_generalization.py` | `data_provider/market_data.py`、归档 profile 回归 | 数据加载泛化 |

### `tests/utils/` — 工具

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_utils_data_alignment.py` | `utils/data_alignment.py` | 数据对齐工具 |
| `test_utils_num.py` | `utils/num_utils.py` | 数值工具 |
| `test_utils_time_index.py` | `utils/time_index.py` | 时间索引工具 |

### `tests/investment_estimation/` — 投资收益测算

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_investment_estimation_v1.py` | `investment_estimation` v1 链路 | MVP 容量搜索/配置加载/数据/财务 |
| `test_investment_estimation_resource_simulation.py` | `investment_estimation/resource_simulation` | PV/Wind 资源仿真 |
| `test_capacity_planning_v4_phase1.py` | `investment_estimation` v4 链路 | v4 Phase 1 结构 |
| `test_capacity_planning_irr_finance.py` | `investment_estimation/finance` | IRR 财务核算 |
| `test_capacity_optimizer.py` | `investment_estimation/todo/wind_pv_bess_capacity_optimizer.py` | 容量优化规划 |
| `test_bess_capacity_planner.py` | `investment_estimation/todo/wind_pv_bess_capacity_planner.py` | 储能容量规划 |
| `test_bess_capacity_operating_planner.py` | `investment_estimation/todo/bess_capacity_operating_planner.py` | 单节点 BESS 容量规划 |
| `test_wind_pv_bess_irr_planner.py` | `investment_estimation/todo/wind_pv_bess_irr_planner.py` | 风光储 IRR 规划 |
| `test_wind_pv_bess_irr_summary_export.py` | `investment_estimation/todo/wind_pv_bess_irr_tuning.py` | 风光储 IRR 汇总导出 |
| `test_project_cashflow.py` | `investment_estimation/todo` | 项目现金流 |
| `test_tariff_and_price_aware_dispatch.py` | `investment_estimation/todo` | 电价与价格感知调度 |
| `test_bess_charge_discharge_plot.py` | `investment_estimation/utils/bess_charge_discharge_plot.py` | 充放电绘图 |

### `tests/trading/` — 交易链路（编排层及全链）

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_v2_phase5_trading.py` | active 单结算 chain | 单结算恒等式、合同/配置、运行计划、日内回退、DR、编排、无前瞻回测、归档边界和 pipeline app |
| `test_v2_phase6_failure_modes.py` | `backtest/backtest.py`、`trading/orchestrator.py` | 数据缺失/求解失败等故障模式 |
| `test_v2_phase6_performance.py` | 单结算 chain | 30 天回测性能基线（slow） |

### `tests/positions/` — 头寸决策

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_mid_long_monthly.py` | `positions/mid_long_planner.py`、`positions/monthly_trader.py` | 中长期分解与月度交易 |

### `tests/backtest/` — 回测与指标

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_metrics.py` | `backtest/metrics.py` | 扩展指标（Sharpe、MDD、EFC、RTE、利用率） |
| `test_v2_phase6_regression.py` | 单结算 chain | 30 天回测回归基线（slow） |

### 根目录 — 项目级结构/跨域测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_v2_phase0_structure.py` | 包结构边界 | Phase 0 目录/模块归属 |
| `test_v2_phase1b_structure.py` | 包结构边界 | Phase 1b 结构回归 |
| `test_v2_phase2_contracts.py` | forecast/data snapshot contracts + AST boundaries | Phase 2 契约、模块权威与依赖方向 |
| `test_structure_layers.py` | 包层级依赖方向 | domain/markets/positions/operations/trading/backtest 分层守卫 |
| `test_yaml_config_loading.py` | src/app YAML 纪律扫描 | 配置读取统一走 `read_yaml` |
| `test_entry_scripts.py` | `app/` 入口脚本 | 跨域入口冒烟（见下） |

### 入口脚本冒烟测试

`test_entry_scripts.py` 通过 `subprocess.run` 调用 `app/` 目录下的入口脚本，验证退出码为 0 且有基本输出。因横跨 optimization/trading/planning 多个分类，留在根目录。

**已覆盖的入口脚本（12 个）：**

| 入口脚本 | 验证内容 |
|----------|----------|
| `optimization/run_bess_arbitrage.py` | 退出码 + 输出非空 |
| `optimization/run_mpc_demo.py` | 退出码 + 输出非空 |
| `optimization/run_two_stage_skeleton.py` | 退出码 + 输出非空 |
| `trading/run_pipeline.py` | 退出码 + 单结算完整链路汇总 |
| `capacity_planning/run_wind_pv_bess_irr_planning.py` | 退出码 + 关键字匹配 |
| `capacity_planning/run_bess_capacity_planning.py` | 退出码验证（medium，timeout=180s） |
| `capacity_planning/run_wind_bess_capacity_planning.py` | 退出码验证（medium，timeout=180s） |

**已注册但默认跳过的入口脚本（3 个）：**

以下入口脚本已注册为 `@pytest.mark.skip` 测试用例，需手动运行验证：

| 入口脚本 | 手动验收命令 | 跳过原因 |
|----------|-------------|---------|
| `capacity_planning/run_wind_pv_bess_capacity_planning_1.py` | `uv run python src/investment_estimation/app/capacity_planning/run_wind_pv_bess_capacity_planning_1.py` | 运行时间 >30s |
| `capacity_planning/run_wind_pv_bess_capacity_planning_2.py` | `uv run python src/investment_estimation/app/capacity_planning/run_wind_pv_bess_capacity_planning_2.py` | 运行时间 >30s |
| `capacity_planning/run_dist_bess_dispatch.py` | `uv run python src/investment_estimation/app/capacity_planning/run_dist_bess_dispatch.py` | 需要外部 CSV 数据文件 |

## 归档测试

`tests/user_side_dispatch/` 是已隔离的用户侧、分布式和 CVXPY 测试归档
（pytest 配置 `norecursedirs = ["user_side_dispatch"]`，常规收集不会
包含），只能通过显式路径运行。

v1 双结算归档（原 `src/ele_trading/trading/todo/dual_settlement_v1/`）已
删除：结算引擎测试随实现迁移至 `tests/markets/`（活动收集）；v1 契约/
报量报价日前/回测的回归测试由 git 历史保留。

## 依赖说明

- 核心测试依赖：`pytest>=8.0.0`（`pyproject.toml` 的 `[dev]` 可选依赖）
- 部分测试需要系统安装求解器：`glpk`（`brew install glpk`）、`cbc`（PuLP 自带）
- `tests/optimization/test_two_stage.py` 依赖 `glpk` 或 `cbc` 求解器
- `tests/forecasting/test_weather.py` 可能需要 `xarray`、`netCDF4`（`[weather]` 可选依赖）

## pytest 配置

配置位于 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
norecursedirs = ["user_side_dispatch"]
cache_dir = "tests/.pytest_cache"
markers = [
    "slow: performance/budget tests skipped by default (run explicitly with -m slow)",
]
addopts = "-m 'not slow'"
```
