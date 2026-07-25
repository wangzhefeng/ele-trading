# trading — 蒙西电力交易主线模块

本模块实现《策略算法框架详细设计 v1.3》（`docs/策略算法框架详细设计_v1.md`）的蒙西交易策略链：中长期 → 月度 → 日前 → 日内 → 结算 → 回测，外加需求响应与回测加噪预测。算法内核全部在此，入口脚本（`app/trading/run_*.py`）尚未建（见文末缺口）。

## 当前文件

| 文件 | 职责 | 对应文档章节 |
|------|------|--------------|
| `contracts.py` | 全部 8 个数据契约 dataclass（`MarketConfig`/`ForecastResult`/`DayAheadPlan`/`IntradayPlan`/`SettlementReport`/`PositionPlan`/`BidLadder`/`CorridorAdvice`/`DRDecision`） | §3.1 |
| `config_loader.py` | `load_market_config()` 加载并校验 `configs/market_mengxi.yaml`（嵌套 sections 展平到 `MarketConfig`） | §3.2 |
| `settlement_mengxi.py` | 蒙西带状结算：`compute_settlement_C` / `compute_settlement_C2` / `compute_cpen_dayah` / `compute_cpen_long` | §5 |
| `day_ahead_coupled.py` | 日前储售联动：模式 A（实时价套利）/ B（有效边际价，默认）/ C（联合优化申报量）+ 申报规则 + 风控裁剪 | §6 |
| `intraday_rolling.py` | 日内滚动重优化：终端 SOC、偏差考核线性化、平滑项（delta_pos/delta_neg） | §7 |
| `mid_long_planner.py` | 中长期仓位结构（α_long/α_dayah/α_real）与分月分解 | §8.1 |
| `monthly_trader.py` | 集中竞价阶梯申报（`BidLadder`）与持仓缺口再平衡 | §8.2/§8.3 |
| `dr_allocator.py` | 需求响应参与决策（ΔR_DR vs ΔR_arb，仅储能） | §9 |
| `noisy_backcast.py` | 回测用加噪预测：用 `sca_price`/`sca_power` 对历史真实量价加乘性噪声生成 `*_pre` | §4.3 |
| `sample_data.py` | 生成 96 点日清分样例 `data/trading/daily_sample_*.csv`（seed 固定可复现） | §11.4.4 |

## 数据来源与输出

- 输入样例：`data/trading/`（价格/储能配置/场景/96 点日清分，见该目录 README）。
- 回测与计划结果落 `results/trading/`（见该目录 README）。
- 结算口径：蒙西带状为唯一实现；广东分层偏差考核已从 `evaluation/settlement.py` 移除（§3.6）。

## 典型流向

```text
configs/market_mengxi.yaml → config_loader.load_market_config
  → mid_long_planner / monthly_trader（中长期建仓）
  → forecasting.provider.ForecastProvider（或回测用 noisy_backcast 生成 *_pre）
  → day_ahead_coupled → intraday_rolling → settlement_mengxi
  → evaluation.backtest.run_mengxi_backtest（forecast-aware 两阶段回测）
```

## 使用边界

- 本模块只含算法内核与数据契约，不含命令行入口。调用方（未来的 `app/trading/run_*.py`）负责解析配置、组装数据、写 `results/trading/`。
- 所有市场参数经 `MarketConfig` 传入，禁止硬编码；`market_mengxi.yaml` 中标 `TODO(rule-confirm)` 的参数为待规则确认的默认值（§3.5）。
- 数据契约的 DataFrame 列名与 §2.2 符号表严格一致，优化/结算/回测据此对齐。

## 已知缺口

- **入口脚本未建**：`app/trading/` 目录尚不存在（v1.3 §11.1/§11.4.6 列了 7 个 `run_*.py`）。算法可经 Python API 直接调用，补薄入口脚本即可命令行演示。
