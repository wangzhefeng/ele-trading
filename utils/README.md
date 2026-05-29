# 根目录 utils 说明

根目录 `utils/` 是项目级和 legacy 兼容辅助工具目录，不是 `ele_trading` 核心包的一部分。核心包内部通用能力优先放在 `src/ele_trading/utils/`，只有需要兼容历史脚本、根目录导入或独立绘图处理时才使用本目录。

## 当前文件

| 文件 / 目录 | 职责 |
|-------------|------|
| `__init__.py` | 对外暴露根目录工具入口，兼容历史导入 |
| `energy_price.py` | 分时电价处理，当前包含谷/深谷电价拉平工具 |
| `time_index.py` | 生成日、小时、15 分钟、5 分钟时间点，处理储能周期边界和时间索引 |
| `pv_es_plot.py` | PV+储能调度结果可视化，含电价类型背景、月最大需量参考线 |
| `plot_ts.py` | 通用时间序列、负荷/PV/风电/net load、储能调度绘图函数 |
| `day2month.py` | 按月统计策略功率和充放电时间片的 legacy 分析脚本 |
| `time_process.py` | 生成月范围、天范围和按小时切分的 legacy 时间工具 |
| `log_util.py` | 根目录 legacy logger，会写入 `logs/<LOG_NAME>/service*` |
| `charge_discharge_plot/` | 充放电策略可视化脚本、样例数据和输出图 |

## 与 `src/ele_trading/utils` 的区别

- `src/ele_trading/utils/`：包内稳定工具，供 `ele_trading` 模块导入。
- `utils/`：根目录 legacy 工具、绘图脚本和历史兼容入口，供测试或旧脚本导入。

新增可复用能力时，默认放入 `src/ele_trading/utils/`；只有需要保留历史路径或服务独立脚本时，才放入根目录 `utils/`。

## 使用边界

- 不要让核心算法依赖根目录 `utils/` 中的绘图或 legacy 脚本。
- 根目录 logger 会创建日志文件；测试或文档任务不应因为日志生成物改动而把 `logs/` 纳入提交。
- 绘图函数默认依赖 Matplotlib 中文字体环境，跨机器运行前需要检查字体可用性。
