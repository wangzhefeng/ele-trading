# PROGRESS.md

## 开工回执（任务 0 通过，2026-08-01）
- 理解的目标：trading/ 裂解为 domain（契约）+ markets/single_settlement（规则插件）+ positions + operations + backtest，trading 只留编排与 demo fixtures；活动代码去 mengxi 命名；测试全绿。
- 顺序：R0 domain+markets → R1 positions+operations → R2 backtest → R3 trading 沉淀 → R4 归位+命名清查 → R5 文档。
- 最大风险：contracts.py 13 个 dataclass 的归属拆分与 22 个外部引用文件的 import 更新遗漏；guard 测试路径断言需同步重写且强度不减。
- 基线：450 passed / 0 failed / 4 skipped / 3 deselected（86s，2026-08-01）。

## 命名约定（R4 用）
- `market_name`/`settlement_mode`：`mengxi`/`mengxi_single` → `single_settlement`（含 config_loader 校验字符串与 yaml 值）。
- configs/trading/market_mengxi.yaml → configs/markets/single_settlement.yaml。

## 进度
- [x] 任务 0：基线核对通过
- [x] R0 domain/ + markets/single_settlement/（455 passed 含 5 个新守卫；反向验证红→绿已贴）
- [x] R1 positions/ + operations/
- [x] R2 backtest/
- [x] R3 trading/ 沉淀 + demo_fixtures 改名
- [x] R4 configs/tests 归位 + mengxi 命名清查（活动代码零命中，455 passed）
- [x] R5 文档同步

## 完工（2026-08-01）
- pytest：455 passed / 0 failed / 4 skipped / 3 deselected（89.59s）。
- 端到端 run_backtest：strategy=8623429.89 no_storage=8651609.94 deterministic=8623429.89 risk_aware=8626028.95 oracle=8628173.64 fallback_days=0，与 v2_baseline manifest 逐项一致。
- mengxi grep：仅命中 trading/todo/ 归档。
- 未 commit（工作树待 review）。

## 追加任务：v1 双结算归档处置（2026-08-01 完工）
- `markets/dual_settlement/` 插件建成：结算引擎 C/C2/Cpen_dayah/Cpen_long 逐行迁移（唯一权威实现）+ 结算子集 MarketConfig + config_loader + `configs/markets/dual_settlement.yaml`。
- `markets/shared.py`：`aggregate_to_settle_periods` 统一实现，两插件共用（接缝 1）。
- `tests/markets/` 18 个新测试全过；归档 `trading/todo/` 整目录删除（git 历史保留）。
- 文档同步：AGENTS/README/MEMORY/trading·markets·tests·configs README/v2 设计文档/架构文档/守卫测试断言。
- 验证：473 passed / 0 failed / 4 skipped / 3 deselected（93.65s）；mengxi grep src/ele_trading 零命中；未 commit。
