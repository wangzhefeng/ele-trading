# AGENTS.md

> 本文件是 ele-trading 项目 **agent 规则的唯一权威**，仅含**项目特有**硬约束。
> 通用编码准则（Think Before Coding / Simplicity First / Surgical Changes / Goal-Driven Execution）
> 由各 agent 的全局配置提供（`~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md`），此处不复制。
>
> 违反下列硬边界会导致不可运行或错误结果。

## 电力交易项目特有约束

### 求解器要求

- Two-stage + CVaR 模型需系统安装 `glpk`（`brew install glpk`）或 `cbc`。
- 调度/优化模型统一通过建模框架构建（PuLP+CBC 为调度类默认，Pyomo+SCIP 用于容量 sizing 类，CVXPY 用于凸规划变体），禁止直接调用底层求解器 C API。
- `cvxpy` 是可选依赖：CVXPY 路径通过 `__getattr__` 延迟导入，缺失时 PuLP/Pyomo 路径正常可用，不阻塞项目主链路。
- 入口脚本需使用 `app/<分类>/` 目录下的 `run_*.py`（如 `app/trading/`、`app/optimization/`），不得在测试或 notebook 中直接调用求解器。容量规划与资源仿真入口位于 `src/investment_estimation/app/`，其中容量规划入口指向 `investment_estimation.todo`。

### 场景模块兼容

- 场景采样默认使用 LHS（`method='lhs'`），新代码必须保留 `method='mc'` 向后兼容参数。
- 场景缩减使用 Kantorovich/Wasserstein L1 后向缩减，不得简单 Top-K 剔除。

### 扩展指标参数

- `compute_extended_metrics()` 调用必须传入正确的 `e_cap`（储能容量）参数，否则 EFC 计算无意义。
- 雨流退化核算 `compute_rainflow_degradation()` 需传入完整 SOC 序列和 `deg_cost_per_cycle`。

### 偏差考核参数

- 活动结算口径以蒙西单结算为唯一实现：`trading/settlement_mengxi.py` 的 `build_settlement_report`（实时电能 `Q_real*p_real` + 中长期差价 `Q_long*(p_long-p_ref)` + 长协回收 + DR/退化/执行分项，`p_ref==p_real` 时与单结算恒等式等价，v2 §6.1）。v1.3 双结算的 `compute_settlement_C`/`compute_settlement_C2`/`compute_cpen_dayah`/`compute_cpen_long` 已迁入 `trading/todo/dual_settlement_v1/`，活动代码不得加回；广东式分层偏差考核 `compute_deviation_penalty()` 同样已移除。
- 蒙西偏差带、考核系数、申报风控等市场参数统一经 `configs/trading/market_mengxi.yaml` + `trading/config_loader.load_market_config()` 加载，字段与 `trading/contracts.MarketConfig` 一一对应；标 `TODO(rule-confirm)` 的参数为待规则确认的默认值（v1.3 §3.5）。
- 禁止在代码中硬编码市场参数（如罚款系数、价格限幅）。

### 配置与数据一致性

- `configs/` 中的 YAML 文件必须与对应入口脚本的参数字段一一对应。
- `dt` 参数在 15 分钟颗粒度场景下必须设为 0.25，并在配置中明确注释。
- 新增环境变量或配置字段必须同步更新 `configs/README.md`。
- 交易/调度侧通用内核归属 `src/ele_trading/optimization/`；蒙西电力交易主线（中长期/月度/日前/日内/结算/回测/DR，v1.3 设计）归属 `src/ele_trading/trading/` 子包；投资收益测算（IRR/NPV、容量扫描、场景编排、收益测算、资源仿真、CSV 导出及老版容量规划的全部内容）归属平级自包含包 `src/investment_estimation/`，其中老版 `ele_trading/capacity_planning` 已整体并入 `src/investment_estimation/todo/`（待整合暂存区）。`src/ele_trading/capacity_planning/` 已删除，不要在 `ele_trading` 下重建容量规划/收益测算模块。
- 项目级 agent 规则的唯一权威是根目录 `AGENTS.md`；`CLAUDE.md` 为指针，不在此文件之外另立内容副本。

### 数据边界

- `data/` 中的样例数据仅用于接口验证、demo 和回归测试，不代表真实市场数据，不能直接用于生产策略评估。
- 生产数据通过 `data_provider` 统一接入，禁止 app 脚本直接硬编码文件路径。
