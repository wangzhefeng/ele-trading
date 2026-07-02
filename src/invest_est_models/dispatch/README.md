# dispatch

`dispatch` 负责运行仿真层的规则型储能调度和能量平衡。

当前最小可行版本的逻辑：

1. 风光出力优先满足负荷。
2. 剩余风光电量优先给储能充电。
3. 用户确认允许储能从电网充电，充电时段由 `charge_price_types` 配置。
4. 储能在 `discharge_price_types` 配置的高价时段放电供负荷。
5. 储能充放电受功率、容量、效率和 SOC 上下限约束。
6. 储能无法消纳的剩余风光电量按余电上网处理。

使用方式：

```python
from invest_est_models.dispatch import dispatch_rule_based

dispatch = dispatch_rule_based(timeseries, case.project.bess)
```

边界说明：

1. 当前不是优化调度，仅用于打通测算闭环。
2. 当前不计算储能退化成本，储能更换成本在财务模块按年度配置处理。

## 实现进度

MVP 版本已实现：

1. 无储能场景的风光、负荷、电网购售电平衡。
2. 规则型储能充放电。
3. 风光余电优先充电。
4. 允许储能按配置从电网充电。
5. SOC 上下限、功率和效率约束。

后续待扩展：

1. 优化调度。
2. 削峰需量控制。
3. 保电 SOC 约束。
4. 储能退化成本进入调度目标。
