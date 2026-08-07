# tests/ 测试目录说明

测试分为活动电力市场交易、独立用户侧调度和独立投资测算三种口径。测试文件名中的 `v1`、`v2`、`Phase` 记录历史迁移来源，不表示旧设计仍是当前规范；测试所保护的当前行为应以测试正文、v5 当前路线和 v3 已批准约束为准。

## 1. 活动测试

| 路径 | 主要覆盖 |
|---|---|
| `tests/optimization/` | BESS 套利、MPC、Two-stage + CVaR 数学内核 |
| `tests/forecasting/` | 预测契约、价格/负荷/风光/天气模型、provider 和指标 |
| `tests/scenario/` | LHS/MC、联合场景、Wasserstein L1 缩减和风险优化衔接 |
| `tests/data_provider/` | 市场快照、数据质量和加载泛化 |
| `tests/positions/` | 中长期与月度头寸基线 |
| `tests/trading/` | 单结算完整链、DR、故障模式和性能 |
| `tests/markets/` | 双结算规则、配置加载和共享聚合 |
| `tests/backtest/` | 交易/BESS 指标和 walk-forward 回归 |
| `tests/utils/` | 时间、数值和数据对齐工具 |
| 根目录结构测试 | 公共契约、依赖方向、配置纪律和入口冒烟 |

### 历史命名测试

以下文件名保留迁移记录，但其测试仍参加当前活动回归：

- `test_v2_phase0_structure.py`
- `test_v2_phase1b_structure.py`
- `test_v2_phase2_contracts.py`
- `forecasting/test_v2_phase3_forecasting.py`
- `forecasting/test_v2_phase3_review_fixes.py`
- `scenario/test_v2_phase4_scenario_optimization.py`
- `scenario/test_v2_phase4_review_fixes.py`
- `trading/test_v2_phase5_trading.py`
- `trading/test_v2_phase6_failure_modes.py`
- `trading/test_v2_phase6_performance.py`
- `backtest/test_v2_phase6_regression.py`

这些名称本轮不重命名，避免把文档重置扩大为测试迁移。

## 2. 活动入口冒烟

`tests/test_entry_scripts.py` 中与 `src/ele_trading/` 活动入口直接相关的测试节点共 11 个，覆盖 10 个脚本和 1 个配置注入行为：

| 测试节点 | 验证内容 |
|---|---|
| `test_run_bess_arbitrage` | BESS 套利入口退出码与输出 |
| `test_run_mpc_demo` | MPC 入口退出码与输出 |
| `test_run_two_stage_skeleton` | Two-stage 入口退出码与输出 |
| `test_two_stage_skeleton_uses_market_config_deviation_costs` | 偏差成本从市场配置注入 |
| `test_run_pipeline` | 默认单结算完整链汇总 |
| `test_run_mid_long` | 中长期入口 |
| `test_run_monthly` | 月度入口 |
| `test_run_dr` | DR 入口 |
| `test_run_day_ahead` | 日前入口 |
| `test_run_intraday` | 日内入口 |
| `test_run_backtest` | walk-forward 回测输出 |

严格排除平级投资测算入口时，应按上述节点运行，而不是直接运行整个 `test_entry_scripts.py`。

## 3. 用户侧测试（独立领域能力，v3 M6 恢复）

`tests/user_side_dispatch/` 覆盖用户侧、分布式和 CVXPY 调度，已恢复进入常规收集。用户侧与市场主链互不依赖，其失败应与交易链结果分开解读。

```bash
uv run python -m pytest -q tests/user_side_dispatch
```

## 4. 独立投资测算测试

`tests/investment_estimation/` 属于平级、自包含的 `investment_estimation` 包，不纳入 v5 电力市场交易活动范围。其失败、跳过和入口时长应与 `src/ele_trading/` 活动结果分开报告。

```bash
uv run python -m pytest -q tests/investment_estimation
```

## 5. 运行口径

### 活动快速验证

```bash
uv run python -m pytest -q \
  tests/optimization tests/forecasting tests/scenario tests/data_provider \
  tests/utils tests/trading tests/positions tests/backtest tests/markets \
  tests/test_structure_layers.py tests/test_v2_phase1b_structure.py \
  tests/test_v2_phase2_contracts.py tests/test_yaml_config_loading.py
```

### 完整仓库测试

```bash
uv run python -m pytest -q
```

完整仓库测试会包含独立投资测算与已恢复的用户侧目录。

### 慢测试

```bash
UV_CACHE_DIR=.uv_cache uv run python -m pytest -m slow -q
```

慢测试默认由 `addopts = "-m 'not slow'"` 排除，主要用于性能预算和较长回归。

### 指定测试

```bash
uv run python -m pytest -q tests/backtest/test_metrics.py::test_sharpe_finite
```

## 6. 最近验证快照

完整仓库验证快照和验收分层见 [v5 §3、§15](../docs/策略算法框架详细设计-v5.md#15-验证与性能预算)。测试通过证明接口和样例回归，不证明生产数据、预测精度、市场参数或策略收益有效。

## 7. 测试维护要求

- 新公共行为必须有业务或契约测试，不只验证类名和文件存在。
- 架构测试应保护依赖方向和隔离边界，不用历史版本号替代行为说明。
- 入口测试必须验证退出码和最小业务输出；需要外部数据或长时间运行的入口应显式标记。
- 修改测试收集范围、marker 或验证命令时，同步更新本 README 和 v5 的验证快照。
