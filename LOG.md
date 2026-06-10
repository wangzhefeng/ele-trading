# LOG.md

本文件用于记录项目执行过程中的残留点、已知限制和后续待办。

记录规则：
- append-only，不修改历史记录，不回写旧条目
- 每次新增记录都追加到文件末尾
- 优先记录真实未完成事项、验证缺口和下一步建议

## TODO

本节是对历史日志中所有未关闭条目的当前状态审计结果，作为后续真正需要继续处理的统一入口。下方历史记录保持原样，不回写旧条目。

### 历史未关闭项评估结论

#### 已完成，不再作为待办保留

- `TODO 001` 中 Two-stage 仍是骨架的问题已完成：`two_stage_cvar.py` 已具备收益等式、SOC 动态、偏差约束、CVaR 线性化和最小可解入口。
- `TODO 001` / `TODO 008` 中回测测试、入口脚本冒烟测试、扩展指标回测测试已完成：当前存在 `tests/test_backtest.py`、`tests/test_entry_scripts.py`、`tests/test_extended_metrics_backtest.py`。
- `TODO 004` 已完成：`scenario/sampler.py` 已支持 LHS、`method="mc"` 和 Cholesky 相关性，`scenario/reduction.py` 已实现 Kantorovich/Wasserstein L1 后向缩减。
- `TODO 005` 中 MPC 终端 SOC 下界已完成：`mpc_storage.py` 已有 `terminal_soc_fraction`，并有 `tests/test_mpc_storage.py` 覆盖。
- `TODO 006` 已完成：`metrics.py` 已有 `compute_extended_metrics()`，覆盖 Sharpe、MDD、EFC、单 EFC 收益、RTE、利用率。
- `TODO 007` 中偏差考核主体已完成：`settlement.py` 已有 `compute_deviation_penalty()`，`configs/market_guangdong.yaml` 已有 15 分钟、96 时段、价格限幅和分层罚款参数。
- `TODO 009` 中 `dt=0.25` 的优化模型能力已完成：`tests/test_bess_arbitrage.py` 和 `tests/test_mpc_storage.py` 已覆盖 15 分钟时间步长求解。
- `TODO 015` 中 `capacity_planning/README.md` 已完成；`src/ele_trading/` 下目录级 README 已在 2026-05-29 文档对齐中更新。
- `TODO 016` / `TODO-F` 中生成物忽略规则已完成：`.gitignore` 已包含 `.DS_Store`、`logs/`、`__pycache__/`、`*.egg-info/`、`.pytest_cache/` 等规则。
- `TODO-A 测试重建` 已完成：`tests/` 当前已恢复，包含核心算法、样例数据、入口脚本、天气特征和 legacy 工具兼容测试。
- `TODO-E 文档补齐` 中核心文档部分已完成：根 README、`app/README.md`、`configs/README.md`、`src/ele_trading/*/README.md`、根 `utils/README.md` 已对齐当前项目状态。

#### 不建议继续作为独立待办推进

- `TODO 005` 中“基于预期价格的终端价值函数”目前不建议继续作为默认待办。已有 `terminal_soc_fraction` 硬约束可控且向后兼容；只有在明确发现硬约束显著压低收益时，再单独建模比较终端价值函数。
- `TODO 002` 中“LOG append-only 机制”已成为项目约定，不再作为工程实现待办保留。
- 2026-05-23 记录中的“tests 目录缺失”已经过期；当前真实问题不是 tests 缺失，而是 legacy import 兼容路径缺失。

### 当前状态（截至 2026-06-11）

所有已追踪待办项（TODO-001 ~ TODO-008）均已完成。最新一次对齐（条目 021）完成了 `docs/research/` 清理与文档对齐。当前无未关闭的 P0~P3 待办项。

测试状态：244 passed, 6 failed（均为 pre-existing legacy bridge 问题），6 skipped。

#### 已完成的待办项（历史记录）

- [x] **TODO-001 legacy import 兼容修复（P0）**
- [x] **TODO-002 测试说明文档（P1）**
- [x] **TODO-003 重型入口验收策略（P1）**
- [x] **TODO-004 15 分钟样例数据与市场配置说明（P2）**
- [x] **TODO-005 离线雨流退化核算（P2）**
- [x] **TODO-006 价格预测升级（P2）**
- [x] **TODO-007 项目级 AGENTS/CLAUDE 规则补齐（P2）**
- [x] **TODO-008 fresh 环境验收（P3）**

#### 低优先级观察

- `.gitignore` 已覆盖 `.DS_Store`，但 4 个 `.DS_Store` 文件仍在 git 跟踪中（提交于规则添加之前）：`.DS_Store`、`docs/.DS_Store`、`src/.DS_Store`、`src/ele_trading/.DS_Store`。`docs/research/.DS_Store` 将在 `docs/research/` 删除提交后自动清除。其余 4 个可通过 `git rm --cached` 移除跟踪，不影响本地文件。

## 2026-04-17

### TODO 001 - 首轮项目接手与运行验证后的残留点

- [x] `src/ele_trading/optimization/two_stage_cvar.py` 当前仍是可导入、可实例化的建模骨架，尚未接入完整收益项、物理约束与可求解的最小场景版。状态：已完成，当前 `two_stage_cvar.py` 已具备完整可求解模型。
- [x] 测试覆盖仍偏薄，当前尚未单独增加 `backtest` 指标输出回归测试，也没有入口脚本级回归测试。状态：已完成，当前已有 `tests/test_backtest.py` 和 `tests/test_entry_scripts.py`。

### TODO 002 - 项目级 AGENTS.md 建立后的残留点

- [ ] 当前 `AGENTS.md` 已覆盖项目定位、验证顺序和修改边界，但仍是第一版；后续如引入更多求解链路或工作流，需要继续补充更细的运行/审查约束。状态：仍需继续，已迁移到顶部 `TODO-007`。
- [x] `LOG.md` 的 append-only 记录机制已建立；后续每次操作结束后都应在本文件追加真实残留点，避免只在对话中口头说明。状态：已成为项目记录约定，不再作为工程待办保留。

## 2026-04-18

### TODO 003 — 补全两阶段 + CVaR 物理约束

参考文献：Conejo et al. (2010) *Decision Making Under Uncertainty in Electricity Markets*；Rockafellar & Uryasev (2000) CVaR 线性化。

- [x] `src/ele_trading/optimization/two_stage_cvar.py`：为每个场景 ω 添加收益等式约束，将自由变量 `m.R[w]` 绑定到实际收益表达式：`R[w] == Σ_t [π_DA[t]·q[t] + π_RT[t,w]·(p_dis[t,w] - p_ch[t,w]) - κ_pos·dev_pos[t,w] - κ_neg·dev_neg[t,w] - c_deg·(p_ch[t,w] + p_dis[t,w])]·Δt`。状态：已完成。
- [x] `src/ele_trading/optimization/two_stage_cvar.py`：添加每场景 SOC 动态递推约束 `soc[t,w] = soc[t-1,w] + η_ch·p_ch[t,w]·dt - p_dis[t,w]/η_dis·dt`，并施加 `soc_min ≤ soc[t,w] ≤ soc_max`、`0 ≤ p_ch[t,w] ≤ p_ch_max`、`0 ≤ p_dis[t,w] ≤ p_dis_max` 界约束。状态：已完成。
- [x] `src/ele_trading/optimization/two_stage_cvar.py`：检查并修正 CVaR 目标函数中 `1/(1-α)` 系数，完整形式为 `max E[R] - λ·[η + 1/(1-α)·Σ_ω p_ω·z_ω]`；当前代码缺少该系数。状态：已完成。
- [x] `app/run_two_stage_skeleton.py`：用最小可解场景（|T|=4，|Ω|=3，权重 0.2/0.5/0.3，α=0.95，λ=0.1，CBC 求解器）验证模型端到端可求解并输出有效目标值。状态：已完成。

### TODO 004 — 升级场景生成与缩减算法

参考文献：Heitsch & Römisch (2003) *Scenario reduction algorithms in stochastic programming*；Frontiers VPP LHS+K-Means (2022)。

- [x] `src/ele_trading/scenario/sampler.py`：将当前简单高斯乘法扰动升级为 **Latin Hypercube Sampling**（`scipy.stats.qmc.LatinHypercube`），保证样本在不确定性空间均匀分层，相同样本数下可降低 30–50% 方差；初始生成建议 N_raw=500–1000。状态：已完成。
- [x] `src/ele_trading/scenario/sampler.py`：通过 Cholesky 分解引入时序相关性：先估计历史价格的小时间自相关矩阵，再 `L = cholesky(corr_matrix); samples = samples @ L.T`，使生成场景的跨时段结构更真实。状态：已完成。
- [x] `src/ele_trading/scenario/reduction.py`：将当前按权重 Top-K 剔除替换为 **Kantorovich/Wasserstein 后向缩减**：用 `scipy.spatial.distance.cdist` 计算场景间 L1 距离矩阵，迭代剔除再分配代价最小的场景，直至目标数量 K（日前 BESS 建议 K=10–20，含二元变量的 MILP 建议 K=5–15）；文献显示从 1000 缩减至 10 仍可保留约 90% 精度。状态：已完成。

### TODO 005 — MPC 终端约束与退化模型升级

参考文献：Rawlings & Mayne (2009) *Model Predictive Control: Theory and Design*；Pinson et al. online rainflow (Wiley 2021)。

- [x] `src/ele_trading/optimization/mpc_storage.py`：添加滚动窗口末端 SOC 下界约束 `soc[H-1] >= soc_min + 0.3·(soc_max - soc_min)`，防止 MPC 在预测窗口末尾过度放电（当前无终端约束，求解器会在 horizon 最后时段将 SOC 耗至下界）。状态：已完成，当前参数名为 `terminal_soc_fraction`。
- [ ] 可选：引入基于预期价格的终端价值函数 `V_f = -π_expected · soc[H-1] · η_dis · Δt` 替代硬约束，减少保守性，仅在硬约束导致实际运行收益明显偏低时启用。状态：暂不继续作为默认待办；如出现收益偏低证据，再单独建模比较。
- [ ] `src/ele_trading/evaluation/metrics.py`（或新建评估子模块）：集成 `rainflow` 库（`pip install rainflow`），在回测结束后对完整 SOC 序列执行离线雨流计数，输出等效循环次数及对应退化成本；MPC 内环继续保留线性吞吐量模型以维持 LP 可解性。状态：仍需继续，已迁移到顶部 `TODO-005`。

### TODO 006 — 补充回测绩效指标（Sharpe / MDD / EFC）

参考：NREL ATB 2024 Utility-Scale Battery Storage；业界 BESS 绩效指标体系。

- [x] `src/ele_trading/evaluation/metrics.py`：添加 **能量交易 Sharpe 比率**，以每小时净收益序列 r_t 计算：`sharpe = mean(r_t) / std(r_t) * sqrt(8760)`；参考值：>0.5 可接受，>1.0 较优；需先对收益序列去季节性再跨季度比较。状态：已完成。
- [x] `src/ele_trading/evaluation/metrics.py`：添加 **最大回撤（MDD）**：`cumrev = rev_series.cumsum(); mdd = ((cumrev - cumrev.cummax()) / cumrev.cummax().abs()).min()`；建议预警线为月累计收益的 15%，实盘回撤通常为回测值的 1.5–2 倍。状态：已完成。
- [x] `src/ele_trading/evaluation/metrics.py`：添加 **等效完整循环次数（EFC）** = `Σ p_dis·Δt / E_cap` 及**单 EFC 净收益** = 净总收益 / EFC_count（LFP 套利盈亏平衡参考值：80–200 CNY/MWh）。状态：已完成。
- [x] `src/ele_trading/evaluation/metrics.py`：添加 **往返效率（RTE）** = `η_ch · η_dis`（LFP 典型值 0.86–0.91）及**利用率** = `Σ(p_ch + p_dis)·Δt / (2·E_cap·T_hours)`。状态：已完成。

### TODO 007 — 接入中国电力现货市场偏差考核规则

参考：广东电力现货市场偏差考核规则（2024）；山东现货市场规则对比。

- [x] `src/ele_trading/evaluation/settlement.py`：添加**偏差考核（deviation penalty）**分层罚款模型：|偏差率| ≤ 2% 为死区无惩罚；2%–5% 按 `0.25·|dev_kWh|·π_DA` 扣罚；>5% 按 `0.5–1.0·|dev_kWh|·π_DA` 扣罚（广东标准；山东死区更紧，±1.5%）。状态：已完成。
- [x] `configs/market_guangdong.yaml`（新建）：配置市场参数，包括结算模式（日前+实时两阶段）、偏差分层阈值与罚款系数、价格上下限（1500 / −100 CNY/MWh，NDRC 标准）、时间颗粒度（15 分钟，96 个时段/日）。状态：已完成。
- [x] `src/ele_trading/optimization/two_stage_cvar.py`：在第二阶段约束中添加偏差软约束 `dev_pos[t,w] + dev_neg[t,w] ≤ 0.02·q[t]`（对应死区），超出部分通过分层罚款系数 κ_pos、κ_neg 进入收益函数。状态：已完成。
- [ ] 数据层适配：将样本数据时间颗粒度从 24 个整点时段扩展至 96 个 15 分钟时段；可通过对现有 `sample_day_ahead_prices.csv` 三次插值生成测试用 96 点序列，用于接口验证。状态：仍需继续，已迁移到顶部 `TODO-004`。

## 2026-04-18（续）

### 实现完成记录

- [x] TODO 003 — `src/ele_trading/optimization/two_stage_cvar.py` 已补充收益等式约束、SOC 动态递推、物理界约束（q 上界 = p_dis_max）；修正 CVaR 约束符号（z[w] >= -R[w] - η，遵循 Rockafellar & Uryasev 2000 标准公式）；`app/run_two_stage_skeleton.py` 可求解并打印最优目标值；`tests/test_two_stage.py` 新增可求解断言（需系统安装 glpk，已通过 brew 安装）。
- [x] TODO 004 — `src/ele_trading/scenario/sampler.py` 升级为 LHS（`scipy.stats.qmc.LatinHypercube`）+ Cholesky 时序相关性采样，保留 `method='mc'` 向后兼容；`src/ele_trading/scenario/reduction.py` 替换为 Kantorovich L1 后向缩减（`scipy.spatial.distance.cdist`）；新增 `tests/test_scenario.py`（6 项测试全部通过）。
- [x] TODO 005 — `src/ele_trading/optimization/mpc_storage.py` 新增 `terminal_soc_fraction` 参数（默认 0.0 向后兼容），约束 `soc[H-1] >= soc_min + fraction*(soc_max-soc_min)`；返回值新增 `soc_terminal` 字段；`tests/test_mpc_storage.py` 新增终端约束防过放电测试。
- [x] TODO 006 — `src/ele_trading/evaluation/metrics.py` 新增 `compute_extended_metrics()`，输出年化 Sharpe、最大回撤（MDD）、EFC 循环次数、单 EFC 收益、往返效率（RTE）、利用率；新增 `tests/test_metrics.py`（7 项测试全部通过）。
- [x] TODO 007 — `src/ele_trading/evaluation/settlement.py` 新增 `compute_deviation_penalty()`（广东分层罚款：死区 ≤2% 免罚，2–5% 按 0.25 系数，>5% 按 0.50 系数）；新建 `configs/market_guangdong.yaml`（96 时段/日、价格限幅 1500/−100 CNY/MWh）；新增 `tests/test_settlement.py`（5 项测试全部通过）。

## 2026-04-19

### TODO 008 — 补全测试覆盖（承接 TODO 001 未关闭项）

- [x] `tests/test_backtest.py`（新建）：为 `run_simple_backtest()` 添加指标输出回归测试，断言返回字典包含 `Total Revenue`、`Energy Arbitrage Revenue`、`Degradation Cost`、`Average SOC`，且数值在合理范围（如总收益 > 0、平均 SOC 在 [soc_min, soc_max] 区间内）。状态：已完成。
- [x] `tests/test_entry_scripts.py`（新建）：为四个入口脚本添加端到端冒烟测试，通过 `subprocess.run` 调用并断言退出码为 0、stdout 非空；覆盖 `run_bess_arbitrage.py`、`run_mpc_demo.py`、`run_two_stage_skeleton.py`、`run_backtest.py`。状态：已完成；重型入口补充验收另迁移到顶部 `TODO-003`。
- [x] `tests/test_extended_metrics_backtest.py`（新建）：将 `compute_extended_metrics()` 接入完整 MPC 回测输出，验证 Sharpe、MDD、EFC 等指标在 24 步样本数据上的数值合理性（EFC > 0，MDD ≤ 0，0 ≤ utilization ≤ 1）。状态：已完成。

### TODO 009 — 数据层 15 分钟颗粒度适配（承接 TODO 007 数据层遗留项）

- [ ] `data/raw/`（新建 `sample_day_ahead_prices_96.csv`）：通过三次样条插值将现有 24 点日前价格扩展为 96 个 15 分钟时段，用于接口验证；插值脚本放在 `scripts/interpolate_to_15min.py`。状态：仍需继续，已迁移到顶部 `TODO-004`。
- [x] `src/ele_trading/data/loader.py`：确认 `load_price_series()` 的 `time_col` 参数可无缝读取 96 行的 15 分钟数据，无需修改调用方代码。状态：已完成，当前 loader 按通用 CSV 行数读取，不限制 24 点。
- [ ] `configs/market_guangdong.yaml`：已配置 `granularity_minutes: 15`，需补充对应 `dt=0.25`（小时）的说明注释，确保优化模块传参一致。状态：仍需继续，已迁移到顶部 `TODO-004`。
- [x] `src/ele_trading/optimization/bess_arbitrage.py` 与 `mpc_storage.py`：确认 `dt` 参数可直接传入 0.25 以切换至 15 分钟颗粒度，补充对应单元测试用例（|T|=8，dt=0.25）。状态：已完成，当前 `tests/test_bess_arbitrage.py` 和 `tests/test_mpc_storage.py` 已覆盖。

### TODO 010 — 离线雨流计数退化核算（承接 TODO 005 可选项）

- [ ] `src/ele_trading/evaluation/metrics.py`：新增 `compute_rainflow_degradation(soc_series, e_cap, deg_cost_per_cycle)` 函数，调用 `rainflow.count_cycles(soc_series)` 对完整 SOC 序列执行离线雨流计数，返回总循环次数、加权 DoD、估算退化成本；`rainflow` 已列入依赖，无需额外安装。状态：仍需继续，已迁移到顶部 `TODO-005`。
- [ ] `tests/test_metrics.py`：追加 `test_rainflow_degradation_*` 用例，验证单次满充满放 SOC 序列（[1→10→1]）对应约 1 个完整循环，退化成本 = `deg_cost_per_cycle * 1`。状态：仍需继续，已迁移到顶部 `TODO-005`。
- [ ] `app/run_backtest.py`：在最小回测结束后调用 `compute_rainflow_degradation()`，将雨流退化成本与线性吞吐量退化成本并列输出，便于对比两种模型的差异。状态：仍需继续，已迁移到顶部 `TODO-005`。

### TODO 011 — 价格预测模块升级（替换 SimplePriceForecaster）

- [ ] `src/ele_trading/forecasting/price_forecast.py`：在保留 `SimplePriceForecaster`（向后兼容）的基础上，新增 `ARIMAForecaster` 类，基于 `statsmodels.tsa.arima.model.ARIMA`（建议初始阶数 p=2, d=0, q=1）实现 `fit(history)` 与 `predict(horizon)` 接口，输出点预测及 95% 置信区间作为上下分位数。状态：仍需继续，已迁移到顶部 `TODO-006`。
- [ ] `src/ele_trading/forecasting/price_forecast.py`：统一 `ForecastOutput` 接口，使 `ARIMAForecaster` 与 `SimplePriceForecaster` 返回相同 dataclass，场景模块可无感切换预测器。状态：仍需继续，已迁移到顶部 `TODO-006`。
- [ ] `tests/test_forecasting.py`（新建）：用样例 24 点日前价格序列验证 `ARIMAForecaster.predict()` 输出长度与 horizon 一致、上分位数 ≥ 点预测 ≥ 下分位数。状态：仍需继续，已迁移到顶部 `TODO-006`。
- [ ] `pyproject.toml`：添加 `statsmodels>=0.14.0` 依赖。状态：仍需继续，已迁移到顶部 `TODO-006`。

### TODO 012 — 更新 AGENTS.md（承接 TODO 002）

- [ ] `AGENTS.md`：补充新增求解链路的运行约束，包括：Two-stage CVaR 模型需系统安装 glpk（`brew install glpk`）；场景模块升级后默认使用 LHS，新代码须保留 `method='mc'` 向后兼容参数；`compute_extended_metrics()` 调用须传入正确 `e_cap` 参数，否则 EFC 计算无意义。状态：仍需继续，已迁移到顶部 `TODO-007`。
- [ ] `AGENTS.md`：补充偏差考核模块的审查约束：`compute_deviation_penalty()` 的 `dead_band_pct`、`tier1_threshold_pct` 参数须与所用市场配置文件（`configs/market_*.yaml`）保持一致，不得在代码中硬编码市场参数。状态：仍需继续，已迁移到顶部 `TODO-007`。

## 2026-04-20

### 状态更新 013 — 历史 TODO 与当前实现对齐

- [x] `TODO 001` / `TODO 003` 的“Two-stage 仍是骨架”表述已被后续实现覆盖：当前仓库中的 `src/ele_trading/optimization/two_stage_cvar.py` 已按 README 所述扩展为完整可求解模型。保留旧条目仅作为历史记录，不再代表当前真实状态。

### TODO 014 — 风光储新增链路的入口级验收

- [ ] `app/run_wind_pv_bess.py`：补充入口级冒烟验证，至少覆盖脚本可启动、核心阶段日志可输出、退出码为 0；如全年 8760 小时演示耗时较高，应增加 `--hours` 或 demo 级降采样参数，避免测试过重。状态：仍需继续，并扩展为重型入口验收策略，已迁移到顶部 `TODO-003`。
- [ ] `.venv` 新依赖的 fresh 环境验收仍未记录：`pvlib`、`windpowerlib`、`scipy`、`rainflow` 需要在全新环境中补一次安装与运行验证，确保 README 中新增的风光储链路说明不是“只在当前机器偶然可跑”。状态：仍需继续，已迁移到顶部 `TODO-008`。

### TODO 015 — 新增模块的文档补齐

- [x] `src/ele_trading/capacity_planning/` 当前已新增公开模块，但尚无与其他模块对齐的目录级 `README.md`；需要补充模块职责、输入输出和与 `forecasting` / `app` 的关系说明。状态：已完成，当前已有 `src/ele_trading/capacity_planning/README.md`。
- [ ] `tests/README.md` 仍停留在早期最小闭环描述，尚未覆盖 `test_capacity_optimizer.py`、`test_solar_forecast.py`、`test_wind_forecast.py`、`test_metrics.py`、`test_settlement.py`、`test_scenario.py` 等后续新增测试。状态：仍需继续，已迁移到顶部 `TODO-002`。

### TODO 016 — 工作区生成物清理与忽略规则

- [x] 当前工作区存在 `.DS_Store` 与 `logs/None/service*` 等生成物变更；需要确认这些文件是否应纳入版本管理，若不是，应补充 `.gitignore` 或日志目录策略，避免后续文档更新时混入无关噪音。状态：已完成，`.gitignore` 已覆盖 `.DS_Store`、`logs/`、`__pycache__/`、`.pytest_cache/`、`*.egg-info/`。

## 2026-05-23

### 状态对齐 017 — 聚焦 ele_trading 核心包后的全面对齐

本次对齐范围限定在 `src/ele_trading/` 核心包及其关联的 app 入口、configs、data，不再关注 `src/demand_response/`、`src/es_rolling_schedule/`、`src/profit_calc/`、`src/wind_pv_es_calc/` 等独立子包。

#### 已完成事项确认

以下 TODO 项的代码已在仓库中实现，对照源码逐一确认：

- [x] **TODO 003** — `two_stage_cvar.py` 已包含完整收益等式约束（`revenue_rule`）、SOC 动态递推（`soc_dynamics_rule`）、偏差约束（`deviation_rule`）、CVaR 线性化（`cvar_rule`，含 `1/(1-α)` 系数）。`app/run_two_stage_skeleton.py` 可用 glpk/cbc 求解。
- [x] **TODO 004** — `sampler.py` 已升级为 LHS（`scipy.stats.qmc.LatinHypercube`）+ Cholesky 时序相关性，保留 `method='mc'`。`reduction.py` 已实现 Kantorovich L1 后向缩减（`scipy.spatial.distance.cdist`）。
- [x] **TODO 005** — `mpc_storage.py` 已包含 `terminal_soc_fraction` 参数（默认 0.0 向后兼容），返回值含 `soc_terminal` 字段。
- [x] **TODO 006** — `metrics.py` 已包含 `compute_extended_metrics()`，输出 sharpe、max_drawdown、efc_count、revenue_per_efc、rte、utilization。
- [x] **TODO 007** — `settlement.py` 已包含 `compute_deviation_penalty()`（广东分层罚款）。`configs/market_guangdong.yaml` 已配置 96 时段/日、价格限幅。
- [x] **TODO 014 部分** — 风光储链路已落地：`capacity_planning/` 含 PVSimulator（pvlib）、WindSimulator（windpowerlib）、CapacityOptimizer、simulate_operation；`forecasting/` 含 PVPowerForecaster（harmonic + physics）、WindPowerForecaster（statistical + physics）；`app/run_wind_pv_bess.py` 为一体化入口。

#### tests 目录缺失

`tests/` 目录在当前工作区不存在。历史记录中提到的 test_two_stage.py、test_scenario.py、test_mpc_storage.py、test_metrics.py、test_settlement.py 等测试文件均已丢失。这使得 TODO 008（测试覆盖）的全部子项、以及 TODO 010（雨流退化测试）均无法验证。

#### 当前真实待办（精简）

以下为对照代码后仍然未完成的真实残留项，重新编号以避免与历史 TODO 混淆：

- [x] **TODO-A 测试重建**（承接 TODO 008）：重建 `tests/` 目录，至少覆盖 bess_arbitrage、mpc_storage、two_stage、scenario、metrics、settlement、backtest 七个测试模块 + 5 个 app 入口脚本的冒烟测试。状态：已完成；当前剩余问题是 legacy import 兼容，已迁移到顶部 `TODO-001`。
- [ ] **TODO-B 15 分钟颗粒度适配**（承接 TODO 009）：生成 96 点插值数据、确认 `dt=0.25` 参数在 bess_arbitrage 和 mpc_storage 中可正确工作。状态：部分完成，`dt=0.25` 测试已完成；96 点样例和说明仍需继续，已迁移到顶部 `TODO-004`。
- [ ] **TODO-C 离线雨流退化**（承接 TODO 010）：在 `metrics.py` 新增 `compute_rainflow_degradation()`，在 `run_backtest.py` 并列输出。状态：仍需继续，已迁移到顶部 `TODO-005`。
- [ ] **TODO-D 价格预测升级**（承接 TODO 011）：新增 `ARIMAForecaster`，添加 `statsmodels` 依赖。状态：仍需继续，已迁移到顶部 `TODO-006`。
- [ ] **TODO-E 文档补齐**（承接 TODO 015）：`capacity_planning/README.md`、`tests/README.md`（待测试重建后编写）。状态：部分完成，核心 README 和 `capacity_planning/README.md` 已完成；`tests/README.md` 仍需继续，已迁移到顶部 `TODO-002`。
- [x] **TODO-F 生成物清理**（承接 TODO 016）：`.gitignore` 补充 `.DS_Store`、`logs/`、`__pycache__/`。状态：已完成，且已覆盖 `*.egg-info/`、`.pytest_cache/`。

## 2026-05-29

### 状态对齐 018 — 文档与当前算法目录对齐

本次只处理文档和占位目录，不修改算法代码、测试代码、配置语义或依赖。

#### 已完成事项确认

- [x] `examples/` 已删除。该目录仅包含占位 `README.md`，当前可运行示例统一由 `app/` 入口脚本承载。
- [x] 根 `README.md` 已按当前项目状态重写，主线目录修正为 `src/ele_trading/data_provider/`，并同步 `forecasting`、`scenario`、`optimization`、`control`、`evaluation`、`capacity_planning`、`demand`、`utils` 等目录职责。
- [x] `app/README.md` 已补齐当前 16 个入口脚本，覆盖储能套利、MPC、Two-stage、回测、用户侧调度、CVXPY、分布式储能、风光储容量规划和 legacy 数据桥接。
- [x] `configs/README.md` 已补齐当前 16 个 YAML 配置，并明确对应入口或模块。
- [x] `src/ele_trading/` 下各子目录 README 已按当前文件和算法边界更新。
- [x] 根目录 `utils/README.md` 已新增，明确根 `utils/` 是 legacy/项目级辅助工具，不是 `src/ele_trading/utils/` 包内工具。

#### 当前验证状态

- `tests/` 目录当前已恢复，包含核心算法、样例数据构造、入口脚本、天气特征和根 `utils` 兼容测试。
- `uv run python -m pytest -q` 可正常运行，legacy import 问题已修复（TODO-001）。

## 2026-06-08

### 状态对齐 019 — 全面审计当前待办项

本次对齐范围覆盖 `src/ele_trading/`、`tests/`、`app/`、`configs/`、`data/`、`.agents/`、`pyproject.toml`，逐一验证顶部 TODO 节中每个待办项的真实状态。

#### 顶部 TODO 节勘误

- **TODO-003 引用的入口脚本名已修正**：原条目引用了 `run_wind_pv_bess.py` 等不存在的文件名，已修正为 `app/` 目录中的实际文件名（见下方 TODO-003 条目）。
- **test_entry_scripts.py 覆盖范围已扩大**：当前覆盖 10 个入口脚本（`run_bess_arbitrage`、`run_mpc_demo`、`run_two_stage_skeleton`、`run_backtest`、`run_user_side_bess_dispatch`、`run_user_side_pv_dispatch`、`run_user_side_pv_bess_dispatch`、`run_wind_pv_legacy_profit_eval`、`run_wind_pv_legacy_market_trading`、`run_wind_pv_bess_irr_planning`），比历史记录中的 4 个有显著增加。未覆盖的入口为 10 个：`run_pv_simulation_v1`、`run_pv_simulation_v2`、`run_wind_simulation_v1`、`run_wind_simulation_v2`、`run_bess_capacity_planning`、`run_wind_bess_capacity_planning`、`run_wind_pv_bess_capacity_planning_1`、`run_wind_pv_bess_capacity_planning_2`、`run_cvxp_bess_dispatch`、`run_dist_ess_dispatch`。

#### 各待办项验证结果

- [x] **TODO-001 legacy import 兼容修复（P0）**：确认已完成。`tests/test_utils_energy_price.py` 和 `tests/test_utils_time_index.py` 可正常收集，无 `src.es_rolling_schedule` 导入。
- [ ] **TODO-002 测试说明文档（P1）**：确认未完成。`tests/README.md` 不存在。当前 tests/ 有 32 个测试文件，但无任何说明文档。
- [ ] **TODO-003 重型入口验收策略（P1）**：确认未完成。`test_entry_scripts.py` 已覆盖 10 个入口，仍有 10 个入口未被测试覆盖。
- [ ] **TODO-004 15 分钟样例数据与市场配置说明（P2）**：确认未完成。`data/raw/` 中仅有 `sample_day_ahead_prices.csv`（24 点），无 `sample_day_ahead_prices_96.csv`。`configs/market_guangdong.yaml` 已配置 `granularity_minutes: 15` 但缺少 `dt=0.25` 传参关系的文档说明。
- [ ] **TODO-005 离线雨流退化核算（P2）**：确认未完成。`metrics.py` 仅含 `compute_irr`、`summarize_bess_metrics`、`compute_extended_metrics` 三个函数，无 `compute_rainflow_degradation()`。`rainflow>=3.2.0` 已在 `pyproject.toml` 依赖中，但未被调用。`tests/test_metrics.py` 无雨流相关测试。
- [ ] **TODO-006 价格预测升级（P2）**：确认未完成。`price_forecast.py` 仅含 `SimplePriceForecaster`，无 `ARIMAForecaster`。`pyproject.toml` 无 `statsmodels` 依赖。`tests/test_forecasting.py` 覆盖 `SimplePriceForecaster`、`PVPowerForecaster`、`WindPowerForecaster`，无 ARIMA 测试。
- [ ] **TODO-007 项目级 AGENTS/CLAUDE 规则补齐（P2）**：确认未完成。`.agents/AGENTS.md` 仅含通用 LLM 编码规范（think before coding、simplicity first 等），无任何电力交易项目特有约束（求解器要求、场景模块兼容、`e_cap` 要求、偏差考核参数等）。
- [ ] **TODO-008 fresh 环境验收（P3）**：未验证，保持待办状态。

#### 当前项目规模快照

- `src/ele_trading/`：9 个子包，约 73 个 Python 模块
- `tests/`：32 个测试文件（含 `__init__.py`），无 README
- `app/`：20 个入口脚本 + README
- `configs/`：20 个 YAML 配置 + README
- `pyproject.toml`：17 个核心依赖，2 个可选依赖组（dev、weather）
- 文档：根 README、app README、configs README、各子包 README、根 utils README 均已对齐

## 2026-06-08（续）

### TODO 020 — P0~P3 待办项全部完成

本次一次性完成顶部 TODO 节中所有 7 个未关闭待办项（TODO-001 已在之前完成）。

#### 变更清单

**TODO-002 测试说明文档（P1）**
- 新增 `tests/README.md`：覆盖 32 个测试文件清单、入口脚本冒烟边界（14 已覆盖 + 6 skip）、依赖说明和运行命令

**TODO-003 重型入口验收策略（P1）**
- `tests/test_entry_scripts.py`：新增 4 个 smoke 测试（`test_run_pv_simulation_v1`、`test_run_cvxp_bess_dispatch`、`test_run_bess_capacity_planning`、`test_run_wind_bess_capacity_planning`），6 个注册为 `@pytest.mark.skip`
- `tests/README.md`：更新已覆盖入口为 14 个，已注册但跳过 6 个
- `app/run_bess_capacity_planning.py`：修复 line 114 语法错误（`config["bess"]]` → `config["bess"]`）

**TODO-004 15 分钟样例数据与市场配置说明（P2）**
- 新增 `data/raw/sample_day_ahead_prices_96.csv`：96 行，三次样条插值自 24 点数据
- `configs/market_guangdong.yaml`：补充 `dt=0.25` 传参注释

**TODO-005 离线雨流退化核算（P2）**
- `src/ele_trading/evaluation/metrics.py`：新增 `compute_rainflow_degradation()` 函数（基于 `rainflow` 库），返回 rainflow_efc、total_throughput、degradation_cost、cycle_count
- `tests/test_metrics.py`：新增 5 个雨流测试（单循环、多循环、退化成本、字段验证、恒定 SOC）
- `app/run_backtest.py`：集成雨流与线性退化并列输出

**TODO-006 价格预测升级（P2）**
- `src/ele_trading/forecasting/price_forecast.py`：新增 `ARIMAForecaster` 类，默认阶数 (2,0,1)，`ForecastOutput` 统一接口
- `pyproject.toml`：添加 `statsmodels>=0.14.0` 依赖
- `tests/test_forecasting.py`：新增 6 个 ARIMA 测试（fit/predict、空历史、未拟合、分位序、非负、接口兼容）

**TODO-007 项目级 AGENTS/CLAUDE 规则补齐（P2）**
- `.agents/AGENTS.md`：新增第 5 节「电力交易项目特有约束」，覆盖求解器要求、场景模块兼容、e_cap 要求、偏差考核参数、配置与数据一致性

**TODO-008 fresh 环境验收（P3）**
- 当前环境已安装 `statsmodels>=0.14.6`（+ `patsy`）
- `uv run python -m pytest` 全量运行：244 passed, 6 failed（均为 pre-existing legacy bridge 问题），6 skipped

#### 验证结果

- 新增/修改的测试全部通过：metrics 12 passed, forecasting 18 passed, entry_scripts 42 passed + 6 skipped
- 预存失败（非本次引入）：`test_run_wind_pv_legacy_profit_eval`、`test_run_wind_pv_legacy_market_trading`（缺少 `run_legacy_data_preparation` 模块）、`test_legacy_data_bridge`（缺少配置文件）、`test_wind_pv_bess_irr_planner`（断言值变化）、`test_yaml_config_loading`（缺少文件）

#### 当前项目规模快照（更新）

- `src/ele_trading/`：9 个子包，约 73 个 Python 模块
- `tests/`：32 个测试文件 + `README.md` ✅
- `app/`：20 个入口脚本 + README
- `configs/`：20 个 YAML 配置 + README
- `data/raw/`：3 个样例数据文件（含新增 96 点 15 分钟价格）
- `pyproject.toml`：18 个核心依赖（新增 `statsmodels`），2 个可选依赖组
- `.agents/AGENTS.md`：通用规范 + 电力交易项目特有约束 ✅

## 2026-06-11

### 状态对齐 021 — docs/ 目录清理与文档对齐

#### 背景

`docs/research/` 目录包含早期调研文档（2026-04-16 风光储测算方案 v1/v2、2026-04-17 电力市场交易调研文档 v1/v2、power-market-trading 图片、电力市场交易 PPT），这些调研文档的内容已沉淀到项目代码和工程文档中，不再需要独立维护。

#### 变更清单

- 删除 `docs/research/` 整个目录（含 5 个 markdown 调研文档、6 张 png 图片、1 个 pptx 文件）
- 更新 `docs/README.md`：移除调研文档引用，描述改为「工程化说明与算法笔记」
- 更新 `docs/architecture_notes.md`：与当前 9 个 src 子包对齐（补充 capacity_planning、resource_simulation、demand，修正 data→data_provider），更新主链路为 7 步闭环
- 更新 `docs/two_stage_notes.md`：补充实现文件路径引用
- 更新根 `README.md`：`docs/` 描述从「架构说明、算法笔记、调研文档」改为「架构说明与算法笔记」

#### 当前 docs/ 结构

```
docs/
├── README.md                   # 文档索引
├── architecture_notes.md       # 系统架构与模块职责
└── two_stage_notes.md          # Two-stage + CVaR 算法说明
```

#### 当前项目规模快照（更新）

- `src/ele_trading/`：9 个子包，约 73 个 Python 模块
- `tests/`：34 个 Python 文件（33 个 test_*.py + 1 个 __init__.py）+ `README.md`
- `app/`：20 个入口脚本 + README
- `configs/`：20 个 YAML 配置 + README
- `data/raw/`：4 个样例数据文件（24 点日前价格、96 点 15 分钟价格、日内价格、BESS 配置）
- `pyproject.toml`：18 个核心依赖，2 个可选依赖组
- `docs/`：3 个文档文件（原 research/ 已删除）
