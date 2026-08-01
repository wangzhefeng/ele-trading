# trading — 蒙西单结算交易主线

本包实现 v2 的中长期/月度持仓、次日资源运行、日内滚动、需求响应、
单结算、统一编排和无前瞻回测。日前价格只可作为显式解释信号，不进入
财务结算。

## 活动模块

| 模块 | 职责 |
|------|------|
| `contracts.py` | `PositionState`、`MarketForecastBundle`、`OperationalPlan`、`IntradayPlan`、`SettlementReport`、`DecisionTrace` 及市场配置 |
| `config_loader.py` | 严格一一映射并校验 `configs/trading/market_mengxi.yaml` |
| `settlement_mengxi.py` | 实时电量成本、中长期差价、月度回收、逐项调整和 DR 履约结算（`compute_dr_settlement`） |
| `mid_long_planner.py` / `monthly_trader.py` | 中长期覆盖、实时敞口、月度阶梯和缺少 orderbook 时的透明走廊 |
| `day_ahead_coupled.py` | 基于共享 BESS 物理内核的次日运行计划，支持联合场景 CVaR 和 `dr_enabled=True` 时的 DR 两阶段联合优化 |
| `intraday_rolling.py` | 冻结已执行段、滚动重优化、求解失败物理裁剪回退和 DR 履约硬约束 |
| `orchestrator.py` | 注入数据、预测、场景、配置和求解器并运行完整链 |
| `backtest.py` | walk-forward 策略、无储能、确定性、风险感知和 oracle 对照 |
| `metrics.py` | 交易/BESS 指标和退化核算 |
| `sample_data.py` | demo 样例数据 provider 与按 issue-time vintage 的 walk-forward 预测 provider |

活动公开契约不含财务日前申报量、报价或日前偏差考核字段。

## 归档

完整 v1 双结算源码、配置、旧 app 和回归夹具位于
`todo/dual_settlement_v1/`。它只用于历史复现，活动 source、app 和
常规 tests 均不得导入 `ele_trading.trading.todo`。归档测试必须显式运行：

```bash
UV_CACHE_DIR=.uv_cache uv run pytest -q \
  src/ele_trading/trading/todo/dual_settlement_v1/tests
```

## 活动流向

```text
data provider → PositionState
forecast provider/registry → MarketForecastBundle
scenario builder → ScenarioSet
OperationalPlan → IntradayPlan → SettlementReport
```

活动入口（v2 §8.2）：`app/trading/run_{mid_long,monthly,day_ahead,intraday,dr,backtest}.py`
以及统一编排入口 `app/trading/run_pipeline.py`。`run_backtest.py` 把 30 天
walk-forward 回归基线写入 `results/trading/backtest/v2_baseline/`。样例数据只
用于接口和回归验证；生产数据必须经 `data_provider` 注入。
