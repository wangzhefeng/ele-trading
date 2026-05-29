# utils — 包内通用工具模块

`src/ele_trading/utils/` 提供核心包内部复用的小工具。它与根目录 `utils/` 不同：本目录面向 `ele_trading` 包内模块，根目录 `utils/` 主要服务 legacy/项目级脚本兼容。

## 当前文件

| 文件 | 职责 |
|------|------|
| `io.py` | YAML 读取和文本写入 |
| `log_util.py` | 项目包内 logger |
| `time_index.py` | 时间步长推断、月度电量统计 |
| `time_splitting.py` | 按月、按日和策略生成时间范围 |
| `data_alignment.py` | 时间序列转 Series、时间对齐、负荷归一、合并 |

## 使用边界

- 新增包内通用能力优先放在本目录。
- 只被单个业务模块使用的 helper 应留在业务模块内，不要提前抽象到 `utils`。
- 不要从本目录反向依赖 `optimization`、`capacity_planning` 等业务模块。

## 根目录 utils 的区别

根目录 `utils/` 包含 legacy 兼容工具、绘图脚本和项目级辅助函数；不要把两者混淆。需要对外导入稳定包能力时，优先使用 `ele_trading.utils`。
