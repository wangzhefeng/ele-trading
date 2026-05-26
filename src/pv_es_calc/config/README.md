# pv_es_calc 配置说明

`pv_es_calc.yaml` 是光伏储能收益测算的统一配置入口，用于新的 `optimization.py` 和 `simulation.py`。

## 字段说明

- `data`：测试数据目录、文件名、字段名和 CSV 编码。
- `run`：测算时间范围、时间粒度、版本、容量列表、并行数和输出目录。
- `storage`：储能效率、可用深度、变压器容量和容量倍率。
- `market`：需量电价和光伏上网电价。
- `objective`：LP 模型的平滑、放电优先级和 SOC 软目标权重。
- `version_methods`：v1-v5 的实际生效版本差异参数。
- `plot`：策略明细图的默认绘图范围和输出目录。

## 版本映射

- `v1`：基础 LP。
- `v2`：午间奖励 `pv_to_battery`。
- `v3`：午间奖励 `pv_to_load`。
- `v4`：午间惩罚 `pv_to_grid`。
- `v5`：规则调度，不求解 LP。

## 运行约定

`optimization.py` 和 `simulation.py` 不接收命令行参数，所有运行参数都从 `pv_es_calc.yaml` 读取。

- 切换版本：修改 `run.method_version`，该值必须存在于 `version_methods`。
- 修改版本算法：调整 `version_methods.<version>` 下的权重或 `dispatch_mode`。
- 控制容量搜索：修改 `run.es_scale_list`。
- 控制并行：修改 `run.max_workers`；`max_workers <= 1` 串行，`max_workers > 1` 并行，输出顺序仍按 `es_scale_list` 保持。
- 控制输出：修改 `run.output_dir` 和 `run.strategy_dir`。
