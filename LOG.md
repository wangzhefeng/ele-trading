# LOG.md

本文件用于记录项目执行过程中的残留点、已知限制和后续待办。

记录规则：
- append-only，不修改历史记录，不回写旧条目
- 每次新增记录都追加到文件末尾
- 优先记录真实未完成事项、验证缺口和下一步建议

## 2026-04-17

### TODO 001 - 首轮项目接手与运行验证后的残留点

- [ ] `src/ele_trading/optimization/two_stage_cvar.py` 当前仍是可导入、可实例化的建模骨架，尚未接入完整收益项、物理约束与可求解的最小场景版。
- [ ] 测试覆盖仍偏薄，当前尚未单独增加 `backtest` 指标输出回归测试，也没有入口脚本级回归测试。

### TODO 002 - 项目级 AGENTS.md 建立后的残留点

- [ ] 当前 `AGENTS.md` 已覆盖项目定位、验证顺序和修改边界，但仍是第一版；后续如引入更多求解链路或工作流，需要继续补充更细的运行/审查约束。
- [ ] `LOG.md` 的 append-only 记录机制已建立；后续每次操作结束后都应在本文件追加真实残留点，避免只在对话中口头说明。

## 2026-04-18

### TODO 003 — 补全两阶段 + CVaR 物理约束

参考文献：Conejo et al. (2010) *Decision Making Under Uncertainty in Electricity Markets*；Rockafellar & Uryasev (2000) CVaR 线性化。

- [ ] `src/ele_trading/optimization/two_stage_cvar.py`：为每个场景 ω 添加收益等式约束，将自由变量 `m.R[w]` 绑定到实际收益表达式：`R[w] == Σ_t [π_DA[t]·q[t] + π_RT[t,w]·(p_dis[t,w] - p_ch[t,w]) - κ_pos·dev_pos[t,w] - κ_neg·dev_neg[t,w] - c_deg·(p_ch[t,w] + p_dis[t,w])]·Δt`
- [ ] `src/ele_trading/optimization/two_stage_cvar.py`：添加每场景 SOC 动态递推约束 `soc[t,w] = soc[t-1,w] + η_ch·p_ch[t,w]·dt - p_dis[t,w]/η_dis·dt`，并施加 `soc_min ≤ soc[t,w] ≤ soc_max`、`0 ≤ p_ch[t,w] ≤ p_ch_max`、`0 ≤ p_dis[t,w] ≤ p_dis_max` 界约束
- [ ] `src/ele_trading/optimization/two_stage_cvar.py`：检查并修正 CVaR 目标函数中 `1/(1-α)` 系数，完整形式为 `max E[R] - λ·[η + 1/(1-α)·Σ_ω p_ω·z_ω]`；当前代码缺少该系数
- [ ] `app/run_two_stage_skeleton.py`：用最小可解场景（|T|=4，|Ω|=3，权重 0.2/0.5/0.3，α=0.95，λ=0.1，CBC 求解器）验证模型端到端可求解并输出有效目标值

### TODO 004 — 升级场景生成与缩减算法

参考文献：Heitsch & Römisch (2003) *Scenario reduction algorithms in stochastic programming*；Frontiers VPP LHS+K-Means (2022)。

- [ ] `src/ele_trading/scenario/sampler.py`：将当前简单高斯乘法扰动升级为 **Latin Hypercube Sampling**（`scipy.stats.qmc.LatinHypercube`），保证样本在不确定性空间均匀分层，相同样本数下可降低 30–50% 方差；初始生成建议 N_raw=500–1000
- [ ] `src/ele_trading/scenario/sampler.py`：通过 Cholesky 分解引入时序相关性：先估计历史价格的小时间自相关矩阵，再 `L = cholesky(corr_matrix); samples = samples @ L.T`，使生成场景的跨时段结构更真实
- [ ] `src/ele_trading/scenario/reduction.py`：将当前按权重 Top-K 剔除替换为 **Kantorovich/Wasserstein 后向缩减**：用 `scipy.spatial.distance.cdist` 计算场景间 L1 距离矩阵，迭代剔除再分配代价最小的场景，直至目标数量 K（日前 BESS 建议 K=10–20，含二元变量的 MILP 建议 K=5–15）；文献显示从 1000 缩减至 10 仍可保留约 90% 精度

### TODO 005 — MPC 终端约束与退化模型升级

参考文献：Rawlings & Mayne (2009) *Model Predictive Control: Theory and Design*；Pinson et al. online rainflow (Wiley 2021)。

- [ ] `src/ele_trading/optimization/mpc_storage.py`：添加滚动窗口末端 SOC 下界约束 `soc[H-1] >= soc_min + 0.3·(soc_max - soc_min)`，防止 MPC 在预测窗口末尾过度放电（当前无终端约束，求解器会在 horizon 最后时段将 SOC 耗至下界）
- [ ] 可选：引入基于预期价格的终端价值函数 `V_f = -π_expected · soc[H-1] · η_dis · Δt` 替代硬约束，减少保守性，仅在硬约束导致实际运行收益明显偏低时启用
- [ ] `src/ele_trading/evaluation/metrics.py`（或新建评估子模块）：集成 `rainflow` 库（`pip install rainflow`），在回测结束后对完整 SOC 序列执行离线雨流计数，输出等效循环次数及对应退化成本；MPC 内环继续保留线性吞吐量模型以维持 LP 可解性

### TODO 006 — 补充回测绩效指标（Sharpe / MDD / EFC）

参考：NREL ATB 2024 Utility-Scale Battery Storage；业界 BESS 绩效指标体系。

- [ ] `src/ele_trading/evaluation/metrics.py`：添加 **能量交易 Sharpe 比率**，以每小时净收益序列 r_t 计算：`sharpe = mean(r_t) / std(r_t) * sqrt(8760)`；参考值：>0.5 可接受，>1.0 较优；需先对收益序列去季节性再跨季度比较
- [ ] `src/ele_trading/evaluation/metrics.py`：添加 **最大回撤（MDD）**：`cumrev = rev_series.cumsum(); mdd = ((cumrev - cumrev.cummax()) / cumrev.cummax().abs()).min()`；建议预警线为月累计收益的 15%，实盘回撤通常为回测值的 1.5–2 倍
- [ ] `src/ele_trading/evaluation/metrics.py`：添加 **等效完整循环次数（EFC）** = `Σ p_dis·Δt / E_cap` 及**单 EFC 净收益** = 净总收益 / EFC_count（LFP 套利盈亏平衡参考值：80–200 CNY/MWh）
- [ ] `src/ele_trading/evaluation/metrics.py`：添加 **往返效率（RTE）** = `η_ch · η_dis`（LFP 典型值 0.86–0.91）及**利用率** = `Σ(p_ch + p_dis)·Δt / (2·E_cap·T_hours)`

### TODO 007 — 接入中国电力现货市场偏差考核规则

参考：广东电力现货市场偏差考核规则（2024）；山东现货市场规则对比。

- [ ] `src/ele_trading/evaluation/settlement.py`：添加**偏差考核（deviation penalty）**分层罚款模型：|偏差率| ≤ 2% 为死区无惩罚；2%–5% 按 `0.25·|dev_kWh|·π_DA` 扣罚；>5% 按 `0.5–1.0·|dev_kWh|·π_DA` 扣罚（广东标准；山东死区更紧，±1.5%）
- [ ] `configs/market_guangdong.yaml`（新建）：配置市场参数，包括结算模式（日前+实时两阶段）、偏差分层阈值与罚款系数、价格上下限（1500 / −100 CNY/MWh，NDRC 标准）、时间颗粒度（15 分钟，96 个时段/日）
- [ ] `src/ele_trading/optimization/two_stage_cvar.py`：在第二阶段约束中添加偏差软约束 `dev_pos[t,w] + dev_neg[t,w] ≤ 0.02·q[t]`（对应死区），超出部分通过分层罚款系数 κ_pos、κ_neg 进入收益函数
- [ ] 数据层适配：将样本数据时间颗粒度从 24 个整点时段扩展至 96 个 15 分钟时段；可通过对现有 `sample_day_ahead_prices.csv` 三次插值生成测试用 96 点序列，用于接口验证

## 2026-04-18（续）

### 实现完成记录

- [x] TODO 003 — `src/ele_trading/optimization/two_stage_cvar.py` 已补充收益等式约束、SOC 动态递推、物理界约束（q 上界 = p_dis_max）；修正 CVaR 约束符号（z[w] >= -R[w] - η，遵循 Rockafellar & Uryasev 2000 标准公式）；`app/run_two_stage_skeleton.py` 可求解并打印最优目标值；`tests/test_two_stage.py` 新增可求解断言（需系统安装 glpk，已通过 brew 安装）。
- [x] TODO 004 — `src/ele_trading/scenario/sampler.py` 升级为 LHS（`scipy.stats.qmc.LatinHypercube`）+ Cholesky 时序相关性采样，保留 `method='mc'` 向后兼容；`src/ele_trading/scenario/reduction.py` 替换为 Kantorovich L1 后向缩减（`scipy.spatial.distance.cdist`）；新增 `tests/test_scenario.py`（6 项测试全部通过）。
- [x] TODO 005 — `src/ele_trading/optimization/mpc_storage.py` 新增 `terminal_soc_fraction` 参数（默认 0.0 向后兼容），约束 `soc[H-1] >= soc_min + fraction*(soc_max-soc_min)`；返回值新增 `soc_terminal` 字段；`tests/test_mpc_storage.py` 新增终端约束防过放电测试。
- [x] TODO 006 — `src/ele_trading/evaluation/metrics.py` 新增 `compute_extended_metrics()`，输出年化 Sharpe、最大回撤（MDD）、EFC 循环次数、单 EFC 收益、往返效率（RTE）、利用率；新增 `tests/test_metrics.py`（7 项测试全部通过）。
- [x] TODO 007 — `src/ele_trading/evaluation/settlement.py` 新增 `compute_deviation_penalty()`（广东分层罚款：死区 ≤2% 免罚，2–5% 按 0.25 系数，>5% 按 0.50 系数）；新建 `configs/market_guangdong.yaml`（96 时段/日、价格限幅 1500/−100 CNY/MWh）；新增 `tests/test_settlement.py`（5 项测试全部通过）。
