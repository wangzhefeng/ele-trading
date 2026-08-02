"""v3 M2 配置迁移脚本：旧扁平单结算 YAML → 六区段 schema v1。

旧格式（v2）：market/long_recovery/scenario/bess/dr/monthly/solver 区段，
叶子字段扁平、由 loader 全局去重拼装。
新格式（v3 D-003）：schema_version + 六区段，long_recovery 并入 market，
每区段字段与 typed config 子对象一一对应。

用法：

    PYTHONPATH=src python scripts/migrate_market_config_v3.py OLD.yaml NEW.yaml

注意：转换只保留结构与取值，旧文件注释（含 TODO(rule-confirm) 标记）
需人工誊写到新文件。
"""

from __future__ import annotations

import sys
from dataclasses import fields
from pathlib import Path

import yaml

from ele_trading.markets.single_settlement.contracts import (
    CURRENT_SCHEMA_VERSION,
    BessSection,
    DrSection,
    MarketSection,
    MonthlySection,
    ScenarioSection,
    SolverSection,
)

SECTION_TYPES = {
    "market": MarketSection,
    "scenario": ScenarioSection,
    "bess": BessSection,
    "dr": DrSection,
    "monthly": MonthlySection,
    "solver": SolverSection,
}


def migrate_legacy_config(raw: dict) -> dict:
    """把旧扁平区段结构转换为 schema v1 六区段结构。"""
    # ---------------- 旧格式：区段叶子扁平合并（含 long_recovery 独立区段） ----------------
    flat: dict = {}
    for section_name, section in raw.items():
        if not isinstance(section, dict):
            raise ValueError(f"legacy config section {section_name!r} must be a mapping")
        for key, value in section.items():
            if key in flat:
                raise ValueError(f"duplicate legacy config field {key!r}")
            flat[key] = value

    # ---------------- 新格式：按子对象字段归属重新分区 ----------------
    migrated: dict = {"schema_version": CURRENT_SCHEMA_VERSION}
    assigned: set[str] = set()
    for section_name, section_type in SECTION_TYPES.items():
        section_fields = [f.name for f in fields(section_type)]
        migrated[section_name] = {}
        for field_name in section_fields:
            if field_name not in flat:
                raise ValueError(f"legacy config missing field {field_name!r}")
            migrated[section_name][field_name] = flat[field_name]
            assigned.add(field_name)
    leftover = sorted(set(flat) - assigned)
    if leftover:
        raise ValueError(f"legacy config has unmigratable fields: {leftover}")
    return migrated


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    migrated = migrate_legacy_config(raw)
    target.write_text(
        yaml.safe_dump(migrated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"migrated {source} -> {target} (schema_version={CURRENT_SCHEMA_VERSION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
