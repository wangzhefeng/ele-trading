# ele_trading 算法架构与数据流（实现现状）

> 定位：本文描述 `src/ele_trading/` **当前已实现**的算法架构、数据流、业务流与各模块输入输出。
> 权威关系：设计意图与业务口径以 `docs/策略算法框架详细设计_v2.md`（下称 v2 设计）为准；项目硬约束以根目录 `AGENTS.md` 为准。本文只做实现现状的事实性描述，不重复 v2 设计论证；两者冲突时以 v2 设计 + 代码为准并修正本文。
> 范围：`data_provider/`、`forecasting/`、`scenario/`、`optimization/`、`trading/`、`utils/` 的活动代码。各包 `todo/`（归档用户侧/分布式/CVXPY/v1 双结算）不在本文范围。

---

## 1. 总体分层与依赖方向

```text
┌─────────────────────────────────────────────────────────────┐
│ app/trading/run_{mid_long,monthly,day_ahead,intraday,dr,    │
│                  backtest,pipeline}.py   （唯一入口层）      │
└──────────────────────────┬──────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────┐
│ trading/  蒙西规则、业务契约、策略、结算、回测、编排         │
│   contracts · config_loader · mid_long_planner ·            │
│   monthly_trader · day_ahead_coupled · intraday_rolling ·   │
│   settlement_mengxi · orchestrator ·                        │
│   backtest · metrics · sample_data                          │
└──────┬───────────────┬────────────────┬─────────────────────┘
       ↓               ↓                ↓
┌─────────────┐ ┌─────────────┐ ┌─────────────────────────────┐
│ forecasting │ │  scenario/  │ │ optimization/               │
│ 五类预测     │ │ 联合场景     │ │ 无市场规则的数学内核        │
│ 契约+注册   │ │ 生成+缩减    │ │ BESS 约束/套利/MPC/CVaR     │
└──────┬──────┘ └──────┬──────┘ └─────────────┬───────────────┘
       ↓               ↑                      ↑
┌─────────────────────────────────────────────────────────────┐
│ data_provider/  数据快照、质量控制、资产参数、气象接入       │
└─────────────────────────────────────────────────────────────┘
       utils/  无业务语义的小型通用函数（被以上各层共享）
```

硬规则（AGENTS.md + v2 §3.1）：

- 依赖方向固定：`data_provider → forecasting → scenario`，`optimization ← trading`；**禁止** `data_provider`/`forecasting`/`scenario`/`optimization` 反向导入 `trading`。
- 跨包边界统一走契约对象（`ForecastRequest/ForecastResult`、`ScenarioSet`、`MarketConfig`），不传裸 DataFrame 约定。
- 市场参数只允许来自 `configs/market_mengxi.yaml` → `trading.config_loader.load_market_config()`，代码中不得硬编码罚款系数、价格限幅等。
- 15 分钟颗粒度 `dt = 0.25`；风光功率统一 MW，电量由下游按 `dt` 换算。

---

## 2. 端到端业务流（蒙西单结算主线）

业务主链（v2 §1.1）：

```text
数据快照 → 多维预测 → 联合场景 → 中长期/月度决策 → 日前运行计划
        → 日内滚动 → 单结算 → 回测复盘
```

`TradingOrchestrator.run()`（`trading/orchestrator.py`）是当前唯一的全链编排实现，一次 `run` 对应**一个决策日**，严格分“决策阶段”和“结算阶段”——真值（actual）只在所有决策完成后进入结算：

| # | 阶段 | 输入 | 处理 | 输出 |
|---|------|------|------|------|
| 1 | 入参校验 | `decision_time`（必须 tz-aware）、`actual_load`/`actual_price`（等长 1-D 有限向量）、`intraday_start` ∈ [0, horizon) | 生成 `valid_times = decision_time + 15min × horizon` | — |
| 2 | 持仓读取 | `data_provider.get_position_state(decision_time, valid_times)` | 读取中长期合约量/价、月度头寸、预算 | `PositionState` |
| 3 | 预测 | 4 个 `ForecastRequest`（target ∈ price/load/wind_power/pv_power，scope_type=`"market"`，freq=15min，quantiles=(0.1, 0.9)） | `forecast_provider.forecast()` ×4，每个结果过 `assert_no_future_info` | `MarketForecastBundle` |
| 4 | 场景 | 4 个 `ForecastResult` + `scenario_count/method/seed`（来自 MarketConfig） | `scenario_builder`（默认 `build_joint_scenarios`） | `ScenarioSet` |
| 5 | 净负荷 | load/wind/pv 点预测（MW → ×dt 转 MWh） | `net_load = max(load − wind − pv, 0)` | `np.ndarray` |
| 6 | 日前计划 | 净负荷、实时价预测、BESS 参数、`q_long/p_long`（按 valid_times reindex，缺失即报错）、`p_ref = 实时价预测`、`ScenarioSet`、DR 调整额 | `solve_day_ahead_operational()` | `OperationalPlan` |
| 7 | 日内滚动 | 冻结 `day_ahead.resource_schedule[:intraday_start]` 为已执行段；`current_soc = day_ahead.soc[intraday_start]`；剩余净负荷/价格/场景切片（`_slice_scenarios`） | `solve_intraday_rolling()`（失败时物理裁剪回退） | `IntradayPlan` |
| 8 | 单结算 | 执行计划拼接（已执行前缀 + 日内剩余）；`q_real = max(actual_load − p_net·dt, 0)`；退化成本 = `(p_ch+p_dis).sum()·dt·deg_cost_per_mwh` | `build_settlement_report()` ×2（baseline 无储能 vs 实际） | `SettlementReport` |

产物打包为 `TradingPipelineResult{position_state, forecasts, scenarios, day_ahead_plan, intraday_plan, settlement_report}`。

中长期/月度/DR 决策（`mid_long_planner`、`monthly_trader`、`demand_response.allocator`）是**独立可调用的策略模块**，由各自 `run_*.py` 入口驱动，不串在 orchestrator 日内链路里；它们与日内链共享 `MarketConfig` 和契约类型。

---

## 3. 核心数据流

### 3.1 数据流向全图

```text
CSV / YAML / Open-Meteo / NetCDF / Mongo / 实测文件
        │
        ▼
data_provider ── MarketDataSnapshot(as_of, version, quality_flags)
        │        ObservedPowerSeries · BESSConfig · PriceSeries
        ▼
forecasting ──── ForecastResult(point, quantiles, unit,
        │                       model_version, feature_as_of)
        ▼
scenario ─────── ScenarioSet(scenarios[(trajectories, probability)],
        │                    valid_time_index, units, metadata)
        ▼
trading ──────── PositionState → OperationalPlan → IntradayPlan
        │                                  → SettlementReport
        ▼
backtest ─────── walk-forward 逐日回放，对照组 DataFrame
```

### 3.2 时间与版本追溯链（无前瞻机制）

每个跨层对象都携带追溯字段，构成完整的“决策可重放”证据链：

| 字段 | 载体 | 语义 | 校验 |
|------|------|------|------|
| `as_of` | `MarketDataSnapshot` | 数据快照截止时刻 | 只有 `is_observation=False` 的行可晚于 `as_of` |
| `issue_time` | `ForecastRequest/Result`、`Scenario`、`MarketForecastBundle` | 预测/场景出具时刻 | `assert_no_future_info`：issue_time ≤ decision_time |
| `feature_as_of` | `ForecastResult` | 实际参与计算的最新历史特征时刻 | 必须 ≤ `issue_time`；scenario 入口重新校验 |
| `valid_time` | 预测/场景的 DatetimeIndex（tz-aware） | 目标时段 | 首个 valid_time = issue_time + frequency |
| `model_version` / `source_version` | `ForecastResult`、`PositionState` | 模型/数据来源版本 | 汇入 `input_versions` |
| `config_version` | `DecisionTrace` | 配置哈希（run_pipeline 用 sha256(config yaml)） | 随决策落 trace |

`DecisionTrace`（每笔交易决策附带）：`decision_time, input_versions, model_versions, config_version, solver_name/version/status, objective_components, active_constraints, fallback_used, fallback_reason`。

---

## 4. 模块详解

### 4.1 `data_provider/` — 数据接入与质量边界

**职责**：把市场、气象、资产输入转换为带 `as_of` 与版本的活动交易数据。

**核心契约**：

| 类型 | 字段 | 说明 |
|------|------|------|
| `MarketDataSnapshot` | `market, scope_type, scope_id, as_of, frame(DataFrame), version, quality_flags` | 版本化市场数据快照；构造时校验时区、时间顺序、唯一性 |
| `ObservedPowerSeries` | `values(pd.Series, tz-aware), unit, source, quality_flags` | 实测负荷/新能源功率（无投资语义） |
| `PriceSeries` | `timestamps(List[int]), prices(List[float]), label` | 整数索引价格序列（样例用） |
| `BESSConfig`（asset_data） | `asset_name, soc0, soc_min, soc_max, p_ch_max, p_dis_max, eta_ch, eta_dis, deg_cost, dt` | 储能物理约束与效率参数 |

**公开 API（输入 → 输出）**：

| 函数 | 输入 | 输出 |
|------|------|------|
| `load_market_data_csv(path, *, market, scope_type, scope_id, as_of, version, quality_flags)` | 带时间戳 CSV | `MarketDataSnapshot` |
| `build_trading_case_dataset(load_df, pv_series, wind_series, price_df, *, market, ...)` | 源数据帧 | `MarketDataSnapshot`（不得经由投资 case builder） |
| `load_observed_power_series(path, *, value_col, unit)` | CSV | `ObservedPowerSeries` |
| `load_price_series(path, time_col, price_col, label)` | CSV | `PriceSeries` |
| `load_bess_config(path)` | YAML | `BESSConfig`（字段严格一一对应） |
| `fetch_weather_open_meteo(lat, lon, start, end, hourly_fields)` | 经纬度+日期 | 小时级天气 DataFrame（ERA5-Land） |
| `load_weather_csv / save_weather_csv` | 路径 | 气象帧 IO |

**质量控制（`quality.py`）**：`ensure_datetime_column`（排序规整）、`ensure_unique_timestamps`（重复即抛错）、`resample_series_frame`、`align_series_on_timestamp`、`compute_quality_score`（按修复标记打分）、`detect_zero_values` / `detect_step_jumps` / `repair_anomalies`（零值+阶跃跳变检测修复）。

**气象双实现**：`weather_data.py` 是 facade；`resource_weather.py`（Open-Meteo + CSV，被 resource_simulation 等 app 使用）；`weather_io.py`（NetCDF/Mongo/模拟器/实测文件夹，目前仅 tests 消费）。`WeatherSimulator` 只用于测试 fixture，不得标记为生产天气源。

### 4.2 `forecasting/` — 五类预测统一门面

**核心契约**（跨包边界唯一权威）：

```python
ForecastRequest(target,        # weather | price | load | wind_power | pv_power
                scope_type,    # system | region | node | portfolio | site（交易链用 "market"）
                scope_id, horizon, frequency,   # 主链 horizon=96, frequency="15min"
                issue_time, quantiles,
                data,          # 目标特有数据（如 market_scope、weather_variable）
                model_name='default', model_version=None)

ForecastResult(request, point: pd.Series, quantiles: Mapping[float, pd.Series],
               unit, model_version, feature_as_of, quality_flags)
```

**调用链**：

```text
ForecastRequest
  → ForecastModelRegistry.resolve(target, model_name, model_version)
      （缺模型→ForecastModelNotFoundError；未知目标→UnknownForecastTargetError）
  → ForecastModel(Protocol).forecast(request) → ForecastResult
```

`ForecastProvider.forecast(request)` 是统一入口；`get_weather/price/load/wind_power/pv_power_forecast()` 五个 typed 便捷 API 仅校验目标后委托通用路径。`SimpleForecastProvider` 是基线实现（不提供隐藏默认价格，必须显式传历史）。

**各目标实现**：

| 目标 | 模块 | 实现要点 |
|------|------|----------|
| weather | `weather_forecast.py` | `ExternalWeatherForecastAdapter`（生产 NWP 边界）、`ArchivedWeatherForecastAdapter`（按 `issue_time` 选 vintage，无前瞻回测必需）、`WeatherBaselineModel`（persistence/climatology 降级 + bias correction） |
| price | `price_forecast.py` | `PriceForecastModel`（seasonal-naive/回归基线）、`ARIMAForecastModel`（registry 适配器）；`data.market_scope` 显式区分日前参考/实时参考/中长期 |
| load | `load_forecast.py` | AR(p)+climatology 递归预测、短历史显式降级标记、五级 scope；`bottom_up_reconcile` / `reconcile_hierarchy`（最小二乘）层级协调 |
| wind_power / pv_power | `renewable_forecast.py`（统一门面）、`wind_forecast.py`、`pv_forecast.py` | physical（`wind_power_curve` 切入/额定/切出；`pv_physical_output` 夜间强制零）/ statistical / external 三路径；输出统一 MW；物理路径只接受具体 `ForecastResult` 或带显式 `feature_as_of` 的对齐天气序列 |

**评估（`metrics.py`）**：MAE、RMSE、pinball loss、区间覆盖率、方向准确率，均带 unit/grain 元数据（`ForecastMetric`）。

**特征工程（`weather_feature.py`）**：Pearson/Spearman/Kendall 相关、多滞后相关、KMeans/DBSCAN 聚类、RBF/Kriging 插值、测点-城市空间匹配与权重；依赖 `weather` optional 依赖组。

**无前瞻**：`assert_no_future_info(result, decision_time)` 在 trading 链路逐结果调用。

### 4.3 `scenario/` — 联合场景生成与缩减

**核心契约**：

| 类型 | 字段 |
|------|------|
| `Scenario` | `scenario_id, probability, issue_time, trajectories{target: pd.Series}, seed, source_versions` |
| `ScenarioSet` | `horizon, valid_time_index(tz-aware), units, scenarios, metadata` |

**生成**：`build_joint_scenarios(price, load, wind, pv: ForecastResult, *, num_scenarios, requested_quantiles, residual_scales, correlation_matrix, method='lhs', random_seed=7) → ScenarioSet`

- 从四类 ForecastResult 的分位与残差尺度构造边际分布，Gaussian copula 保相关矩阵；
- 默认 **LHS**，保留 `method='mc'` 向后兼容（AGENTS.md 硬约束）；
- 强制四类来源同 issue_time、tz-aware index、同 horizon；显式 q0.5 优先于 point；
- `generate_price_scenarios()` / `PriceScenario` 仅为 v1 兼容面。

**缩减**：`reduce_scenarios(ScenarioSet, top_k, *, quantiles, max_mean_drift, max_quantile_drift, preserve_critical_events=True, return_diagnostics) → ScenarioSet`

- **Kantorovich/Wasserstein L1 后向缩减**（AGENTS.md 硬约束，禁止 Top-K 剔除）：按 target 尺度归一的联合 L1 距离，逐轮重算最小运输代价，移除场景概率转移到最近保留场景并归一化；
- 默认保留关键净负荷峰值/爬坡场景；
- `ReductionDiagnostics`：Wasserstein 代价、概率转移、均值/分位漂移、关键事件保留标记；漂移可设阈值显式失败。

### 4.4 `optimization/` — 无市场规则的数学内核

**共享 BESS 物理内核（`bess_model.py`）**：

```python
BESSConfig(soc0, soc_min, soc_max, p_ch_max, p_dis_max, eta_ch, eta_dis,
           dt=0.25, terminal_soc=None, max_throughput=None, no_export=False)

add_bess_constraints(model, time_steps, config, *, net_load=None, prefix='bess')
    → BESSVariables(p_charge, p_discharge, soc, charge_mode, discharge_mode)
```

注入约束：SOC 动态、充放效率、功率上限、充放互斥（二进制 mode）、可选末端 SOC、吞吐量上限、不可倒送（no-export）。被 `bess_arbitrage`、`mpc_bess`、`trading/day_ahead_coupled`、`trading/intraday_rolling` 共同复用。

**求解器边界（`solver.py`）**：`solve_pulp_model(model, *, solver, msg) → SolverResult{status: SolveStatus, objective_value, raw_status, solver_name, message}`。不抛异常、不返回伪造值；typed 状态边界。

**三个求解器**：

| 求解器 | 输入 | 输出 | 用途 |
|--------|------|------|------|
| `solve_bess_arbitrage(_typed)` | 价格序列 + BESS 参数（soc/功率/效率/deg_cost/dt/末端 SOC 开关） | `BESSArbitrageResult{objective, p_ch, p_dis, soc}` | 确定性单市场套利基准/oracle，目标 = 放电收入 − 充电成本 − 线性退化 |
| `solve_one_mpc_window` / `run_bess_mpc` | 价格序列、horizon、initial_soc、`terminal_soc_fraction` | 单窗结果 / `pd.DataFrame`（逐步 `MPCStepResult{step, price, p_ch, p_dis, soc_next, step_objective}`） | 滚动 MPC；终端 SOC 下界防窗口末端过度放电 |
| `solve_two_stage_cvar(scenario_set, *, bess_config, deviation_penalty_positive, deviation_penalty_negative, day_ahead_prices=None, alpha=0.95, risk_weight=1.0, degradation_cost, solver)` | `ScenarioSet` + 显式偏差考核系数（**不提供市场默认值**） | `TwoStageCVaRResult{solve_status, first_stage_bid, scenario_recourse{scenario_id→ScenarioRecourse}, expected_cost, var, cvar, objective_value, trace_metadata}` | 第一阶段日前申报量 + 第二阶段各场景充放/SOC/偏差；失败不返回伪造零计划 |

**风险工具（`risk.py`）**：`add_cvar_auxiliaries`（Rockafellar-Uryasev 线性化：VaR 阈值、超额损失变量、CVaR 表达式）、`risk_adjusted_objective(expected + risk_weight×CVaR)`、`weighted_var_cvar`（独立离散校验）。

### 4.5 `trading/` — 蒙西单结算业务主线

#### 4.5.1 配置体系

`configs/market_mengxi.yaml` → `load_market_config()` → `MarketConfig`（YAML 叶子与字段**严格一一对应**，多/少字段都失败）。区段与字段：

| 区段 | 字段（默认值） |
|------|----------------|
| market | `market_name='mengxi'`, `settlement_mode='mengxi_single'`, `settle_periods=96`, `dt=0.25` |
| long_recovery | `long_recovery_lower_ratio=0.9`, `upper_ratio=1.05`, `multiplier=1.2`, `applies_to_storage=True`, `pos_tol_ratio=0.05`（均 TODO(rule-confirm)） |
| scenario | `two_stage_scenario_deviation_cost_positive/negative=0.25`, `scenario_method='lhs'`, `scenario_count=20`, `scenario_seed=7`, `scenario_cvar_alpha=0.95`, `scenario_cvar_weight=0.0` |
| bess | `soc_terminal_min=None`, `exclusive_charge_discharge=True`, `operational_power_margin=0.8`, `throughput_max_ratio=1.0`, `deg_cost_per_mwh=0.0`, `bess_market_role='behind_meter'`, `no_discharge_on_curtail=False` |
| dr | `dr_aggregation='aggregator'`, `dr_compensation_per_mwh=2000`, `dr_penalty_per_mwh=3000`, `dr_minimum_margin=0`, `dr_minimum_response_mwh=0.1`, `dr_window_start=72`, `dr_window_end=80` |
| monthly | `monthly_price_floor=0.0`, `monthly_price_cap=1500.0`, `monthly_trade_unit_mwh=1.0` |
| solver | `solver_name='cbc'`, `solver_time_limit_seconds=30.0`, `solver_mip_gap=0.0` |

#### 4.5.2 业务契约（`contracts.py`）

| 契约 | 字段 | 语义 |
|------|------|------|
| `PositionState` | `as_of, q_long, p_long, monthly_positions, budget_remaining, risk_exposure, source_version` | 中长期合约+月度成交+预算+敞口 |
| `MarketForecastBundle` | `issue_time, price/load/wind/pv_forecast` | 同一 issue_time 的四路预测 |
| `OperationalPlan` | `resource_schedule(DataFrame), soc, expected_cost, expected_risk, constraint_trace, decision_trace` | 次日物理运行计划（**不含财务申报量**） |
| `IntradayAdjustment` | `p_net_new, delta_p_net, expected_cost_delta, reasons` | 相对上次可行计划的变化 |
| `IntradayPlan` | `schedule, executed_prefix, adjustment, fallback_used` | 已执行前缀 + 最新可行剩余计划 |
| `SettlementReport` | `energy_cost, contract_difference, long_recovery, dr_adjustment, degradation_cost, execution_adjustment, total_cost, baseline_cost, delta_cost, trace` | 单结算逐项报告 |
| `PositionPlan` | `alpha_long, alpha_real, q_long_monthly, price_band, expected_cost, expected_risk, budget_used, coverage` | 中长期持仓结果（无财务日前仓位） |
| `BidLadder` | `direction, bid_qty, bid_price, clear_prob, expected_cost, expected_revenue` | 月度集中竞价阶梯 |
| `CorridorAdvice` | `direction, qty_range, price_range, reason` | 无 orderbook 时的透明量价走廊 |
| `DRDecision` | `participate, response_qty, window, expected_compensation, arbitrage_opportunity_cost, expected_penalty, degradation_cost, net_margin, fulfill_risk, reject_reason` | DR 参与决策 |
| `DecisionTrace` | 见 §3.2 | 决策追溯 |

#### 4.5.3 中长期与月度（策略层）

| 函数 | 输入 → 输出 | 逻辑 |
|------|------------|------|
| `mid_long_planner.plan_mid_long_position(q_load_forecast, p_long_forecast, p_spot_forecast, budget, config, alpha_long_range=(0.7, 0.9)) → PositionPlan` | 负荷/长协价/现货价预测+预算 | 长协覆盖比例 + 实时敞口，alpha 为结果指标而非固定启发式 |
| `monthly_trader.build_bid_ladder(q_low, q_high, p_low, p_high, k, direction, config, clear_prob_model='uniform') → BidLadder` | 量价区间+档数 | 集中竞价阶梯（含成交概率） |
| `monthly_trader.build_position_corridor(*, position_gap, tolerance, price_band, config) → CorridorAdvice` | 持仓缺口 | 无 orderbook 时输出量价走廊，不伪造成交概率 |
| `monthly_trader.rebalance_position_gap(gap, pos_tol, config) → dict` | 缺口序列 | 持仓再平衡建议 |

#### 4.5.4 日前运行计划（`day_ahead_coupled.py`）

`solve_day_ahead_operational(load_forecast, realtime_price_forecast, bess, config, *, explanatory_price_signal=None, q_long, p_long, p_ref, scenario_set=None, dr_adjustment, decision_time, input_versions, config_version, solver) → OperationalPlan`

- 目标：min 次日实时购电成本 + 中长期差价影响 + 退化成本（+ 联合场景 CVaR 项，可选）；
- 复用 `optimization.bess_model` 共享内核；受 `operational_power_margin`（功率裕度）、`throughput_max_ratio`、`exclusive_charge_discharge`、`soc_terminal_min` 约束；
- 日前预出清价只能经 `explanatory_price_signal` 作为解释性特征，**不进入结算**。

#### 4.5.5 日内滚动（`intraday_rolling.py`）

`solve_intraday_rolling(*, load_forecast, realtime_price_forecast, current_soc, bess, config, previous_plan, executed_prefix, ..., scenario_set=None, ...) → IntradayPlan`

1. 冻结 `executed_prefix`（已执行段不可改）；
2. 以 `current_soc` 为初值，对剩余窗口重优化（模型与日前同内核）；
3. 输出 `IntradayAdjustment`（计划变化、成本变化、原因）；
4. **求解失败回退**：沿用上次可行计划的剩余段，经物理边界（SOC/功率）裁剪后执行，置 `fallback_used=True`，不返回伪可行解。

#### 4.5.6 需求响应（`day_ahead_coupled.py` + `settlement_mengxi.py`）

主链路联合优化（`dr_enabled=True` 时在 `solve_day_ahead_operational` 内部两阶段求解）：

- **Pass A**：无 DR 激励基线模型 → 求窗口基线放电能量 `Q0 = Σ_{t∈W} p_discharge[t]·dt`（`dr_baseline_mode="fixed"` 时直接用 `dr_baseline_mwh`）；
- **Pass B**：同一模型追加 DR 变量 `y∈{0,1}`（申报）、`inc≥0`（增量放电 MWh）和约束：
  - `inc` 在 `y=1` 时绑定 `max(0, Σ_W p_dis·dt − Q0)`（上下界 big-M 松弛）；
  - `inc ≥ dr_minimum_response_mwh · y`（申报门槛）；
  - 目标函数追加 `−dr_compensation_per_mwh · inc`（补偿为负成本）；
- 退化不单独加项：DR 吞吐已包含在现有线性退化成本中。
- **履约结算** `compute_dr_settlement(committed_qty, executed_window_discharge_mwh, baseline_qty, config)`：
  补偿 = `dr_compensation_per_mwh · min(inc_actual, committed_qty)`（超出不补）；
  罚金 = `dr_penalty_per_mwh · max(0, committed_qty − inc_actual)`；
  `dr_adjustment = penalty − compensation`。
- **日内履约**：剩余窗口与 DR 窗口有交集时，传放电下限约束 `Σ_{剩余∩W} p_dis·dt ≥ committed_qty − 已执行窗口放电`；不可解走 `_clip_fallback`。

独立事后评估工具 `demand_response/allocator.py`（`evaluate_dr_participation`）不参与主链路，保留用于快速经济性分析。

#### 4.5.7 单结算（`settlement_mengxi.py`）

```text
C_energy[t]   = Q_real[t] × p_real[t]                      （实时电能成本）
C_contract[t] = Q_long[t] × (p_long[t] − p_ref[t])         （中长期差价合约）
C_total       = Σ(C_energy + C_contract)
              + long_recovery + dr_adjustment
              + degradation_cost + execution_adjustment
```

- `p_ref == p_real` 时与 `Q_long·p_long + (Q_real−Q_long)·p_real` 恒等（v2 §6.1，测试锁定）；
- `compute_long_recovery(*, q_long_month, p_long_month, q_real_month, p_ref_month, config)`：月度缺额/超额回收，参数全部来自 long_recovery 区段（TODO(rule-confirm) 占位，不得回退日前偏差考核）；
- `build_settlement_report(...)`：逐项列示、每项只计一次；`aggregate_to_settle_periods` 保总量聚合到 96 点。

#### 4.5.8 回测与指标

`run_walk_forward_backtest(calendar_data: Mapping[pd.Timestamp, DataFrame], *, orchestrator, intraday_start, risk_aware_weight=1.0) → pd.DataFrame`

- 逐日回放归档 vintage；真值只进结算/oracle 对照；
- 对照组：无储能 baseline、确定性策略、风险感知策略、oracle（唯一允许用未来真实价格）；
- 回归基线写入 `results/trading/backtest/v2_baseline/`。

`metrics.py`：`summarize_bess_metrics`（回测汇总）、`compute_extended_metrics(dispatch_df, e_cap, dt, ...)`（**必须传正确的 `e_cap`**，否则 EFC 无意义）、`compute_rainflow_degradation(soc_series, e_cap, deg_cost_per_cycle)`（雨流退化，需完整 SOC 序列）。

#### 4.5.9 样例与 demo 数据（`sample_data.py`）

- `SampleTradingDataProvider(data/trading)`：`available_days`、`frame_for_day(day)`、`get_position_state(decision_time, valid_times)`——版本化日 fixture，不向 app 暴露路径；
- `WalkForwardSeasonalNaiveProvider`：按 issue-time vintage 的无前瞻 walk-forward 预测 provider；
- `generate_day / main`：生成 96 点日清分样例 `data/trading/daily_sample_*.csv`。

### 4.6 `utils/` — 通用工具

无业务语义的小函数，按职责分文件：`time_index`（各粒度时间索引、`infer_dt_hours`、bess_cycle 窗口）、`time_splitting`（月/日范围拆分）、`data_alignment`（时序对齐、插值、CSV 读取）、`num_utils`（数值清洗、浮点扫描）、`io`（YAML/文本）、`log_util`（全局 logger）、`day2month`（日期→月份）、`pulp_utils`（PuLP 状态检查）。

---

## 5. 入口与输出

### 5.1 入口脚本（`app/trading/`）

| 入口 | 驱动模块 |
|------|----------|
| `run_mid_long.py` | `mid_long_planner` |
| `run_monthly.py` | `monthly_trader` |
| `run_day_ahead.py` | `day_ahead_coupled` |
| `run_intraday.py` | `intraday_rolling` |
| `run_dr.py` | `demand_response.allocator` |
| `run_backtest.py` | `backtest`（30 天 walk-forward 回归基线 → `results/trading/backtest/v2_baseline/`） |
| `run_pipeline.py` | `TradingOrchestrator` 端到端 demo（`SampleTradingDataProvider` + `SeasonalNaiveTradingForecastProvider` + `build_joint_scenarios`，config_version = 配置文件 sha256） |

生产数据必须经 `data_provider` 注入，入口不得硬编码 `data/` 路径；`data/` 样例只用于接口验证、demo 和回归。

### 5.2 输出布局（v2 §8.3）

```text
results/trading/
├── forecasts/<run_id>/
├── scenarios/<run_id>/
├── decisions/<run_id>/
├── settlement/<run_id>/
└── backtest/<run_id>/        # v2_baseline/ 为 30 天回归基线
```

每个运行目录写机器可读结果 + `manifest.json` + 配置快照。

---

## 6. 关键设计不变量（修改代码前必读）

1. **单结算唯一**：日前价格不进入财务结算；`Q_dayah`/`Cpen_dayah`/`compute_deviation_penalty` 已归档 `trading/todo/`，活动代码不得加回。
2. **无前瞻**：`issue_time ≤ decision_time`、`feature_as_of ≤ issue_time`，orchestrator 逐 ForecastResult 校验；回测只读当时 vintage。
3. **显式市场参数**：偏差考核系数、CVaR 参数、DR 补偿等一律显式传参或来自 `MarketConfig`，optimization 不提供市场默认值。
4. **失败不伪造**：求解失败返回 typed 失败状态 + 回退（日内）或结构化失败（two_stage），绝不返回零计划冒充可行解。
5. **场景两条硬规则**：采样默认 LHS 保留 `mc`；缩减只用 Wasserstein L1 后向缩减，概率守恒。
6. **单位纪律**：风光 MW，15 分钟 `dt=0.25` 换算电量；月度数据用 PeriodIndex。
7. **可追溯**：任何交易决策可回溯到 `as_of` 快照、预测 `issue_time`、场景 seed/版本、配置哈希和求解状态（`DecisionTrace`）。
