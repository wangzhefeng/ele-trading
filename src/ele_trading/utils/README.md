# utils — 当前通用工具

本包只保存当前 `ele_trading` 活动代码复用的通用工具，不承载市场规则或业务策略。

## 当前文件

| 文件 | 当前职责 |
|---|---|
| `io.py` | `read_yaml`、`write_text` |
| `log_util.py` | 共享 `logger` |
| `num_utils.py` | 求解器数值清洗和包含端点的浮点扫描 |
| `pulp_utils.py` | `check_pulp_status` 兼容状态检查 |
| `time_index.py` | 时间点生成、粒度推断、月电量和 BESS 周期窗口 |
| `time_splitting.py` | 月/日时间范围拆分 |
| `time_process.py` | 包内时间处理辅助 |
| `data_alignment.py` | 时间序列规范化、对齐、合并和 CSV 读取 |
| `day2month.py` | 日期到月份映射 |

## 包级公开导出

当前 `ele_trading.utils` 直接导出：

- IO/日志：`read_yaml`、`write_text`、`logger`；
- 数值/求解：`clean_value`、`clean_list`、`inclusive_float_range`、`check_pulp_status`；
- 时间：`infer_dt_hours`、`monthly_kwh`、`generate_time_points`、`generate_days`、`generate_hours`、`generate_quarters`、`generate_5mins`、`end_of_that_day`、`start_of_this_bess_cycle`、`end_of_this_bess_cycle`、`bess_cycle_window`、`process_time_index`、`extract_timestamp_hours`；
- 拆分：`generate_month_ranges`、`generate_day_pairs`、`get_time_ranges`；
- 对齐：`as_time_series`、`normalize_time_and_load`、`align_to_time`、`align_and_merge`、`ensure_datetime_index`、`read_time_value_csv`。

`time_process.py` 和 `day2month.py` 当前未从包根直接转出，使用者应按模块显式导入。新增通用工具前应先确认至少有真实跨模块消费者，避免把单一业务逻辑下沉到 utils。
