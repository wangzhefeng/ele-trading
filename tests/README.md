# tests/ 测试目录说明

本目录包含 `ele-trading` 项目的单元测试和集成测试。

## 运行方式

```bash
# 运行全部测试
uv run python -m pytest -q

# 运行指定测试文件
uv run python -m pytest tests/test_bess_arbitrage.py -v

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
| `test_cvxp_bess_dispatch.py` | `optimization/user_side_bess_dispatch_cvxpy.py` | CVXPY 储能调度 |
| `test_user_side_bess_dispatch.py` | `optimization/user_side_bess_dispatch.py` | 用户侧储能调度 |
| `test_user_side_pv_dispatch.py` | `optimization/user_side_pv_dispatch.py` | 用户侧光伏调度 |
| `test_user_side_pv_bess_dispatch.py` | `optimization/user_side_pv_bess_dispatch.py` | 用户侧光储联合调度 |
| `test_user_side_renewable_dispatch.py` | `optimization/user_side_renewable_dispatch_class.py` | 用户侧可再生能源调度 |
| `test_capacity_optimizer.py` | `capacity_planning/capacity_optimizer.py` | 容量优化规划 |
| `test_bess_capacity_planner.py` | `capacity_planning/bess_capacity_planner.py` | 储能容量规划 |
| `test_wind_pv_bess_irr_planner.py` | `capacity_planning/wind_pv_bess_irr_planner.py` | 风光储 IRR 规划 |
| `test_wind_pv_bess_irr_summary_export.py` | `capacity_planning/wind_pv_bess_irr_tuning.py` | 风光储 IRR 汇总导出 |

### 场景生成与评估测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_scenario.py` | `scenario/sampler.py`、`scenario/reduction.py` | LHS 采样 + Cholesky 相关性 + Kantorovich 缩减 |
| `test_metrics.py` | `evaluation/metrics.py` | 扩展指标（Sharpe、MDD、EFC、RTE、利用率） |
| `test_settlement.py` | `evaluation/settlement.py` | 偏差考核（广东分层罚款） |
| `test_backtest.py` | `evaluation/backtest.py` | 回测指标输出回归 |
| `test_extended_metrics_backtest.py` | `evaluation/metrics.py` + MPC 回测 | 扩展指标接入完整回测 |

### 预测与天气测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_forecasting.py` | `forecasting/price_forecast.py`、`pv_forecast.py`、`wind_forecast.py` | 价格/光伏/风电预测 |
| `test_weather.py` | `data_provider/weather_io.py` | 天气数据读取 |
| `test_pv_es_plot.py` | `utils/pv_es_plot.py` | 光伏储能绘图 |

### 数据层与工具测试

| 测试文件 | 覆盖模块 | 说明 |
|----------|----------|------|
| `test_data_layer_generalization.py` | `data_provider/loader.py` | 数据加载泛化 |
| `test_yaml_config_loading.py` | YAML 配置加载 | 配置文件解析 |
| `test_legacy_data_bridge.py` | legacy 数据桥接 | 旧数据格式兼容 |
| `test_utils_data_alignment.py` | `utils/data_alignment.py` | 数据对齐工具 |
| `test_utils_demand_charge.py` | `utils/demand_charge.py` | 需量电费计算 |
| `test_utils_energy_price.py` | `utils/energy_price.py` | 能量价格工具 |
| `test_utils_num.py` | `utils/num_utils.py` | 数值工具 |
| `test_utils_time_index.py` | `utils/time_index.py` | 时间索引工具 |

### 样例数据构造测试

| 测试文件 | 说明 |
|----------|------|
| `test_user_side_bess_sample_data.py` | 用户侧储能样例数据构造 |
| `test_user_side_pv_sample_data.py` | 用户侧光伏样例数据构造 |
| `test_user_side_pv_dispatch_sample_data.py` | 用户侧光伏调度样例数据构造 |
| `test_user_side_pv_bess_dispatch_sample_data.py` | 用户侧光储调度样例数据构造 |

### 入口脚本冒烟测试

`test_entry_scripts.py` 通过 `subprocess.run` 调用 `app/` 目录下的入口脚本，验证退出码为 0 且有基本输出。

**已覆盖的入口脚本（14 个）：**

| 入口脚本 | 验证内容 |
|----------|----------|
| `run_bess_arbitrage.py` | 退出码 + 输出非空 |
| `run_mpc_demo.py` | 退出码 + 输出非空 |
| `run_two_stage_skeleton.py` | 退出码 + 输出非空 |
| `run_backtest.py` | 退出码 + 输出非空 |
| `run_user_side_bess_dispatch.py` | 退出码 + 关键字匹配 |
| `run_user_side_pv_dispatch.py` | 退出码 + 关键字匹配 |
| `run_user_side_pv_bess_dispatch.py` | 退出码 + 关键字匹配 |
| `run_wind_pv_legacy_profit_eval.py` | 退出码 + 关键字匹配 |
| `run_wind_pv_legacy_market_trading.py` | 退出码 + 关键字匹配 |
| `run_wind_pv_bess_irr_planning.py` | 退出码 + 关键字匹配 |
| `run_pv_simulation_v1.py` | 退出码 + 关键字匹配（quick） |
| `run_cvxp_bess_dispatch.py` | 退出码 + 关键字匹配（quick） |
| `run_bess_capacity_planning.py` | 退出码验证（medium，timeout=180s） |
| `run_wind_bess_capacity_planning.py` | 退出码验证（medium，timeout=180s） |

**已注册但默认跳过的入口脚本（6 个）：**

以下入口脚本已注册为 `@pytest.mark.skip` 测试用例，需手动运行验证：

| 入口脚本 | 手动验收命令 | 跳过原因 |
|----------|-------------|---------|
| `run_pv_simulation_v2.py` | `uv run python app/run_pv_simulation_v2.py` | 需要 Open-Meteo 网络 API |
| `run_wind_simulation_v1.py` | `uv run python app/run_wind_simulation_v1.py` | 需要 Open-Meteo 网络 API |
| `run_wind_simulation_v2.py` | `uv run python app/run_wind_simulation_v2.py` | 需要 Open-Meteo 网络 API |
| `run_wind_pv_bess_capacity_planning_1.py` | `uv run python app/run_wind_pv_bess_capacity_planning_1.py` | 运行时间 >30s |
| `run_wind_pv_bess_capacity_planning_2.py` | `uv run python app/run_wind_pv_bess_capacity_planning_2.py` | 运行时间 >30s |
| `run_dist_bess_dispatch.py` | `uv run python app/run_dist_bess_dispatch.py` | 需要外部 CSV 数据文件 |

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
```
