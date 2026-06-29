# Two-stage + CVaR 工程说明

> 实现文件：`src/ele_trading/optimization/two_stage_cvar.py`
> 入口脚本：`app/optimization/run_two_stage_skeleton.py`
> 参考文献：Conejo et al. (2010) *Decision Making Under Uncertainty in Electricity Markets*；Rockafellar & Uryasev (2000) CVaR 线性化

## 目标

把日前承诺与实时修正拆成两层随机规划决策：

- **第一阶段**（here-and-now）：在未来不确定性暴露前确定日前申报量 `q[t]`（仅日前市场，不含实时偏差）。
- **第二阶段**（wait-and-see）：在每个场景 ω 发生后，确定充放电 `p_ch[t,ω]` / `p_dis[t,ω]`、SOC 轨迹 `soc[t,ω]`、偏差 `dev_pos/neg[t,ω]`，以及场景收益 `R[ω]`。

## 模型结构

### 变量

| 阶段 | 变量 | 含义 |
|------|------|------|
| 第一阶段 | `q[t]` | 日前市场申报量 |
| 第二阶段 | `p_ch[t,ω]`, `p_dis[t,ω]` | 每场景充/放电功率 |
| 第二阶段 | `soc[t,ω]` | 每场景荷电状态 |
| 第二阶段 | `dev_pos[t,ω]`, `dev_neg[t,ω]` | 每场景正/负偏差 |
| 第二阶段 | `R[ω]` | 每场景总收益 |
| CVaR | `η`, `z[ω]` | CVaR 辅助变量 |

### 约束

1. **收益等式**：`R[ω] = Σ_t [π_DA[t]·q[t] + π_RT[t,ω]·(p_dis - p_ch) - κ_pos·dev_pos - κ_neg·dev_neg - c_deg·(p_ch + p_dis)]·Δt`
2. **SOC 动态递推**：`soc[t,ω] = soc[t-1,ω] + η_ch·p_ch[t,ω]·Δt - p_dis[t,ω]/η_dis·Δt`，并受 `soc_min ≤ soc ≤ soc_max` 约束
3. **偏差约束**：`dev_pos + dev_neg ≤ dead_band·q[t]`（死区 2%），超出部分按分层罚款系数 κ_pos/κ_neg 进入收益
4. **CVaR 线性化**：`z[ω] ≥ -R[ω] - η`，目标函数中 `max E[R] - λ·[η + 1/(1-α)·Σ_ω p_ω·z_ω]`
5. **物理界约束**：`0 ≤ p_ch ≤ p_ch_max`，`0 ≤ p_dis ≤ p_dis_max`

### 求解

- 使用 `pyomo` + `glpk`（LP）或 `cbc`（MILP，如含二元变量扩展）。
- 最小可解示例：|T|=4，|Ω|=3，权重 0.2/0.5/0.3，α=0.95，λ=0.1。
