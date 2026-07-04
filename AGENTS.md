# AGENTS.md
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.
**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.
## 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**
Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.
## 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.**
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.
## 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**
When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.
## 4. Goal-Driven Execution
**Define success criteria. Loop until verified.**
Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
---
**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Agent skills

### Issue tracker

Issues and PRDs are tracked as local markdown files under `.scratch/`; external PRs are not a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

The triage vocabulary uses Chinese label strings: `待评估`, `需补充信息`, `可交给 agent`, `需人工实现`, `不处理`. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context domain docs layout. See `docs/agents/domain.md`.

## 电力交易项目特有约束

以下为本项目的硬边界规则，违反会导致不可运行或错误结果。

### 求解器要求

- Two-stage + CVaR 模型需系统安装 `glpk`（`brew install glpk`）或 `cbc`。
- 调度/优化模型统一通过建模框架构建（PuLP+CBC 为调度类默认，Pyomo+SCIP 用于容量 sizing 类，CVXPY 用于凸规划变体），禁止直接调用底层求解器 C API。
- `cvxpy` 是可选依赖：CVXPY 路径通过 `__getattr__` 延迟导入，缺失时 PuLP/Pyomo 路径正常可用，不阻塞项目主链路。
- 入口脚本需使用 `app/<分类>/` 目录下的 `run_*.py`（如 `app/capacity_planning/`、`app/optimization/`），不得在测试或 notebook 中直接调用求解器。

### 场景模块兼容

- 场景采样默认使用 LHS（`method='lhs'`），新代码必须保留 `method='mc'` 向后兼容参数。
- 场景缩减使用 Kantorovich/Wasserstein L1 后向缩减，不得简单 Top-K 剔除。

### 扩展指标参数

- `compute_extended_metrics()` 调用必须传入正确的 `e_cap`（储能容量）参数，否则 EFC 计算无意义。
- 雨流退化核算 `compute_rainflow_degradation()` 需传入完整 SOC 序列和 `deg_cost_per_cycle`。

### 偏差考核参数

- `compute_deviation_penalty()` 的 `dead_band_pct`、`tier1_threshold_pct` 参数必须与 `configs/market_*.yaml` 保持一致。
- 禁止在代码中硬编码市场参数（如罚款系数、价格限幅）。

### 配置与数据一致性

- `configs/` 中的 YAML 文件必须与对应入口脚本的参数字段一一对应。
- `dt` 参数在 15 分钟颗粒度场景下必须设为 0.25，并在配置中明确注释。
- 新增环境变量或配置字段必须同步更新 `configs/README.md`。
- 交易/调度侧通用内核归属 `src/ele_trading/optimization/`；容量规划实际使用的公共合同、资源输入、规划专用调度模型、容量扫描、场景编排、收益测算和 CSV 导出归属 `src/ele_trading/capacity_planning/`。
- 项目级 agent 规则的唯一权威是根目录 `AGENTS.md`；`CLAUDE.md` 为指针，不在此文件之外另立内容副本。

### 数据边界

- `data/` 中的样例数据仅用于接口验证、demo 和回归测试，不代表真实市场数据，不能直接用于生产策略评估。
- 生产数据通过 `data_provider` 统一接入，禁止 app 脚本直接硬编码文件路径。
