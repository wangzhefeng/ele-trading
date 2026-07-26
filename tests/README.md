# tests/ 测试目录说明

本目录包含 `ele-trading` 项目的单元测试和集成测试。

`tests/todo/` 是已隔离的用户侧、分布式和 CVXPY 测试归档。它们只能通过
显式路径运行，常规 pytest 收集不会包含它们。

v1 双结算回归位于
`src/ele_trading/trading/todo/dual_settlement_v1/tests/`，同样只允许显式
运行；活动测试不得导入该归档包。

## 运行方式

```bash
# 运行全部测试
uv run python -m pytest -q

# 运行指定测试文件
uv run python -m pytest tests/test_bess_arbitrage.py -v

# 运行 archived 用户侧测试（常规收集明确排除）
uv run python -m pytest tests/todo/test_user_side_bess_dispatch.py -q

# 运行指定测试函数
uv run python -m pytest tests/test_metrics.py::test_sharpe_finite -v
```

## 测试文件清单

### 核心算法测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_bess_arbitrage.py` | `optimization/bess_arbitrage.py` | 储能套利优化（含 dt=0.25 15 分钟步长） |
| `test_mpc_bess.py` | `optimization/mpc_bess.py` | MPC 滚动优化（含终端 SOC 约束） |
| `test_two_stage.py` | `optimization/two_stage_cvar.py` | 两阶段 CVaR 优化（需 glpk/cbc 求解器） |
| `test_capacity_optimizer.py` | `investment_estimation/todo/wind_pv_bess_capacity_optimizer.py` | 容量优化规划 |
| `test_bess_capacity_planner.py` | `investment_estimation/todo/wind_pv_bess_capacity_planner.py` | 储能容量规划 |
| `test_bess_capacity_operating_planner.py` | `investment_estimation/todo/bess_capacity_operating_planner.py` | 单节点 BESS 容量规划 |
| `test_distributed_bess_dispatch.py` | `investment_estimation/todo/bess_capacity_distributed_planner.py` | 分布式储能容量搜索与导出编排 |
| `test_wind_pv_bess_irr_planner.py` | `investment_estimation/todo/wind_pv_bess_irr_planner.py` | 风光储 IRR 规划 |
| `test_wind_pv_bess_irr_summary_export.py` | `investment_estimation/todo/wind_pv_bess_irr_tuning.py` | 风光储 IRR 汇总导出 |

### 场景生成与评估测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_scenario.py` | `scenario/sampler.py`、`scenario/reduction.py` | LHS 采样 + Cholesky 相关性 + Kantorovich 缩减 |
| `test_metrics.py` | `trading/metrics.py` | 扩展指标（Sharpe、MDD、EFC、RTE、利用率） |
| `test_v2_phase5_trading.py` | active `trading` chain | 单结算恒等式、合同/配置、运行计划、日内回退、DR、编排、无前瞻回测、归档边界和 pipeline app |

### 预测与天气测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_forecasting.py` | `forecasting/price_forecast.py`、`pv_forecast.py`、`wind_forecast.py` | 价格/光伏/风电预测 |
| `test_dr_forecast.py` | `forecasting/contracts.py`、`provider.py` | 请求/结果 API 与无前瞻约束 |
| `test_weather.py` | `data_provider/weather_data.py` 兼容实现 | 天气数据读取 |

### 数据层与工具测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_v2_phase2_contracts.py` | forecast/data snapshot contracts + AST boundaries | Phase 2 契约、模块权威与依赖方向 |
| `test_v2_phase3_forecasting.py` | forecasting weather/price/load/renewable/provider/metrics | Phase 3 完整预测能力与逐场景 RED/GREEN 回归 |
| `test_v2_phase3_review_fixes.py` | Phase 3 provenance/timezone/history/compatibility/ARIMA/weather input contracts | Round 1/2 评审问题的 RED/GREEN 防回归 |
| `test_v2_phase4_scenario_optimization.py` | joint scenario/reduction/BESS/CVaR/solver | Phase 4 联合场景、概率转移、物理约束与求解失败 |
| `test_v2_phase4_review_fixes.py` | Phase 4 median/provenance/reduction/penalty/version contracts | Phase 4 Round 1 评审问题的 RED/GREEN 防回归 |
| `test_data_layer_generalization.py` | `data_provider/market_data.py`、归档 profile 回归 | 数据加载泛化 |
| `test_yaml_config_loading.py` | YAML 配置加载 | 配置文件解析 |
| `test_legacy_data_bridge.py` | legacy 数据桥接 | 旧数据格式兼容 |
| `test_utils_data_alignment.py` | `utils/data_alignment.py` | 数据对齐工具 |
| `test_utils_num.py` | `utils/num_utils.py` | 数值工具 |
| `test_utils_time_index.py` | `utils/time_index.py` | 时间索引工具 |

### 样例数据构造测试

| 测试文件 | 说明 |
|----------|------|

### 入口脚本冒烟测试

`test_entry_scripts.py` 通过 `subprocess.run` 调用 `app/` 目录下的入口脚本，验证退出码为 0 且有基本输出。

**已覆盖的入口脚本（14 个）：**

| 入口脚本 | 验证内容 |
|----------|----------|
| `optimization/run_bess_arbitrage.py` | 退出码 + 输出非空 |
| `optimization/run_mpc_demo.py` | 退出码 + 输出非空 |
| `optimization/run_two_stage_skeleton.py` | 退出码 + 输出非空 |
| `trading/run_pipeline.py` | 退出码 + 单结算完整链路汇总 |
| `legacy/run_wind_pv_legacy_profit_eval.py` | 退出码 + 关键字匹配 |
| `capacity_planning/run_wind_pv_bess_irr_planning.py` | 退出码 + 关键字匹配 |
| `resource_simulation/run_pv_simulation_v1.py` | 退出码 + 关键字匹配（quick） |
| `capacity_planning/run_bess_capacity_planning.py` | 退出码验证（medium，timeout=180s） |
| `capacity_planning/run_wind_bess_capacity_planning.py` | 退出码验证（medium，timeout=180s） |

**已注册但默认跳过的入口脚本（6 个）：**

以下入口脚本已注册为 `@pytest.mark.skip` 测试用例，需手动运行验证：

| 入口脚本 | 手动验收命令 | 跳过原因 |
|----------|-------------|---------|
| `resource_simulation/run_pv_simulation_v2.py` | `uv run python app/resource_simulation/run_pv_simulation_v2.py` | 需要 Open-Meteo 网络 API |
| `resource_simulation/run_wind_simulation_v1.py` | `uv run python app/resource_simulation/run_wind_simulation_v1.py` | 需要 Open-Meteo 网络 API |
| `resource_simulation/run_wind_simulation_v2.py` | `uv run python app/resource_simulation/run_wind_simulation_v2.py` | 需要 Open-Meteo 网络 API |
| `capacity_planning/run_wind_pv_bess_capacity_planning_1.py` | `uv run python app/capacity_planning/run_wind_pv_bess_capacity_planning_1.py` | 运行时间 >30s |
| `capacity_planning/run_wind_pv_bess_capacity_planning_2.py` | `uv run python app/capacity_planning/run_wind_pv_bess_capacity_planning_2.py` | 运行时间 >30s |
| `capacity_planning/run_dist_bess_dispatch.py` | `uv run python app/capacity_planning/run_dist_bess_dispatch.py` | 需要外部 CSV 数据文件 |

## 依赖说明

- 核心测试依赖：`pytest>=8.0.0`（`pyproject.toml` 的 `[dev]` 可选依赖）
- 部分测试需要系统安装求解器：`glpk`（`brew install glpk`）、`cbc`（PuLP 自带）
- `test_two_stage.py` 依赖 `glpk` 或 `cbc` 求解器
- `test_weather.py` 可能需要 `xarray`、`netCDF4`（`[weather]` 可选依赖）

## pytest 配置

配置位于 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
cache_dir = "tests/.pytest_cache"
```
