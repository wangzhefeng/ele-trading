# capacity_search

`capacity_search` 负责风光储容量和固定 PPA 单价粗网格搜索，并在 V2-V5 阶段通过 `objective_mode` 切换最优排序口径。

核心逻辑：

1. 从 YAML 的 `search` 配置读取候选风电容量、光伏容量、储能功率、储能容量和 PPA 单价。
2. 对候选值做笛卡尔积枚举。
3. 对每个候选方案复用已有调度、结算、财务测算链路。
4. 计算投资方 IRR、NPV、回收期、业主节费比例、自发自用比例和余电上网比例。
5. 按约束输出可行状态和不可行原因。
6. 按 `objective_mode` 选择最优方案。

当前支持的目标模式：

1. `investor_irr_first`：先比较 `project_irr`，再比较 `owner_saving_pct`。
2. `owner_saving_first`：先比较 `owner_saving_pct`，再比较 `project_irr`。
3. `investor_irr_uplift`：先比较 `irr_uplift`，再比较 `candidate_project_irr` 和 `candidate_owner_saving_pct`。

## 实现进度

MVP 版本已实现：

1. 无。该模块是 v1 新增模块。

v1 版本已实现：

1. 粗网格容量搜索。
2. 候选方案指标计算。
3. 投资方 IRR 和业主节费比例约束。
4. 自发自用比例和余电上网比例可选约束。
5. 不可行原因输出。

V2-V5 版本已实现：

1. `objective_mode` 排序模式切换。
2. 投资方 IRR 优先场景。
3. 业主节费比例优先场景。
4. `baseline_project` 基准方案和 `irr_uplift` 计算。
5. 候选结果中输出 `objective_value`、排序指标、约束阈值、基准 IRR 和 uplift 指标。

后续待扩展：

1. 分层粗细搜索。
2. Pareto 前沿输出。
3. 更复杂的政策约束。
4. 与优化调度联动。
5. V5 基准方案的真实业务口径确认。
