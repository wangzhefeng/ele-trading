# settlement

`settlement` 负责把时序仿真结果汇总为月度结算指标。

当前计算项：

1. 无项目基准电网购电成本：`load_kw * dt_hours * price`。
2. 有项目电网购电成本：`grid_buy_kwh * price`。
3. PPA 电量：风光直接供负荷电量 + 风光给储能充电电量。
4. PPA 成本和投资方 PPA 收入：`ppa_energy_kwh * ppa_price`。
5. 余电上网收益：`grid_sell_kwh * export_price`。
6. 业主节费：无项目电网购电成本 - 有项目业主成本。
7. 投资方收入：PPA 收入 + 余电上网收入。

使用方式：

```python
from invest_est_models.settlement import settle_monthly

monthly = settle_monthly(dispatch, case.project)
```

暂未纳入：

1. 基本电费。
2. 力调电费。
3. 输配电价拆分。
4. 偏差考核。

这些不确定项后续如有需要，先进入 YAML 配置并用模拟字段标明含义。

## 实现进度

MVP 版本已实现：

1. 月度电量聚合。
2. 无项目电网购电成本。
3. 有项目电网购电成本。
4. 固定单价 PPA 成本和投资方收入。
5. 余电上网收益。
6. 业主节费和节费比例。

v1 版本已实现：

1. 基本电费占位口径。
2. 最大需量电费占位口径。
3. 输配电价附加占位口径。
4. 偏差考核费用占位口径。

后续待扩展：

1. 政府性基金拆分。
2. 储能收益分成口径。
3. 真实账单口径校准。
