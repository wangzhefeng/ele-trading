"""可重复生成 V5 synthetic 仿真资产。

这些资产只用于契约、编排、失败路径和验收 runner 的工程验证；不代表真实
市场规则、正式账单、校准结果或生产证据。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


_SIMULATION_DIR = "v5_simulation"
_TIMEZONE = "Asia/Shanghai"


def _write_json(path: Path, payload: object) -> None:
    """JSON 是 YAML 的合法子集，避免为 fixture 生成器引入新依赖。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_readme(directory: Path) -> None:
    (directory / "README.md").write_text(
        "# V5 synthetic 仿真资产\n\n"
        "> **仅适用于工程测试、接口验证和模拟运行。**\n\n"
        "所有资产均标记为 `synthetic`，不得用于真实市场规则、预测/收益校准、"
        "正式账单验收、影子运行结论或生产默认切换。模拟账单固定为 "
        "`confirmed=false`。权威边界见 `docs/策略算法框架详细设计-v5.md`。\n",
        encoding="utf-8",
    )


def _time_index(days: int) -> pd.DatetimeIndex:
    if not isinstance(days, int) or days < 1:
        raise ValueError("days must be a positive integer")
    return pd.date_range(
        "2026-07-01 00:00:00",
        periods=days * 96,
        freq="15min",
        tz=_TIMEZONE,
    )


def _resource_catalog() -> dict[str, object]:
    return {
        "source_id": "v5_synthetic_fixture",
        "quality_flag": "synthetic",
        "resources": [
            {
                "resource_id": "bess-a",
                "resource_type": "bess",
                "soc_min_mwh": 1.0,
                "soc_max_mwh": 8.0,
                "p_charge_max_mw": 2.0,
                "p_discharge_max_mw": 2.0,
                "version": "synthetic-resource-v1",
            },
            {
                "resource_id": "bess-b",
                "resource_type": "bess",
                "soc_min_mwh": 0.5,
                "soc_max_mwh": 5.0,
                "p_charge_max_mw": 1.5,
                "p_discharge_max_mw": 1.5,
                "version": "synthetic-resource-v1",
            },
        ],
    }


def _build_timeseries(index: pd.DatetimeIndex, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    steps = np.arange(len(index), dtype=float)
    phase = 2.0 * np.pi * (steps % 96.0) / 96.0
    plan_rows: list[dict[str, object]] = []
    metering_rows: list[dict[str, object]] = []
    availability_rows: list[dict[str, object]] = []
    for resource_id, capacity, scale in (("bess-a", 8.0, 1.0), ("bess-b", 5.0, 0.7)):
        planned = np.maximum(0.0, np.sin(phase - 0.8)) * 0.30 * scale
        shortfall = np.where((steps.astype(int) % 19) == 0, 0.04 * scale, 0.0)
        actual = np.maximum(0.0, planned - shortfall)
        actual += rng.normal(0.0, 0.002, len(index))
        actual = np.maximum(0.0, actual)
        available_capacity = np.where((steps.astype(int) % 53) == 0, capacity * 0.85, capacity)
        soc = np.clip(capacity * 0.55 - np.cumsum(actual) * 0.02, capacity * 0.2, capacity * 0.8)
        for valid_time, planned_mwh, actual_mwh, cap, actual_soc in zip(
            index, planned, actual, available_capacity, soc, strict=True
        ):
            event_time = pd.Timestamp(valid_time)
            available_at = event_time + pd.Timedelta(minutes=5)
            plan_rows.append(
                {
                    "resource_id": resource_id,
                    "interval_start": event_time.isoformat(),
                    "interval_end": (event_time + pd.Timedelta(minutes=15)).isoformat(),
                    "planned_discharge_energy_mwh": round(float(planned_mwh), 6),
                    "plan_version": "synthetic-da-v1",
                    "quality_flag": "synthetic",
                }
            )
            metering_rows.append(
                {
                    "resource_id": resource_id,
                    "event_time": event_time.isoformat(),
                    "available_at": available_at.isoformat(),
                    "interval_start": event_time.isoformat(),
                    "interval_end": (event_time + pd.Timedelta(minutes=15)).isoformat(),
                    "actual_charge_energy_mwh": 0.0,
                    "actual_discharge_energy_mwh": round(float(actual_mwh), 6),
                    "actual_power_mw": round(float(actual_mwh * 4.0), 6),
                    "actual_soc_mwh": round(float(actual_soc), 6),
                    "quality_flag": "synthetic",
                    "source_version": "synthetic-meter-v1",
                    "revision": 1,
                }
            )
            availability_rows.append(
                {
                    "resource_id": resource_id,
                    "event_time": event_time.isoformat(),
                    "available_capacity_mwh": round(float(cap), 6),
                    "quality_flag": "synthetic",
                    "source_version": "synthetic-availability-v1",
                }
            )
    return (
        pd.DataFrame(plan_rows),
        pd.DataFrame(metering_rows),
        pd.DataFrame(availability_rows),
    )


def write_v5_simulation_fixtures(
    root_directory: str | Path,
    *,
    days: int = 30,
    seed: int = 42,
) -> Path:
    """写入隔离的、固定种子 V5 synthetic 仿真资产。"""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    output = Path(root_directory) / _SIMULATION_DIR
    for relative in (
        "resources",
        "plans",
        "metering",
        "market",
        "settlement",
        "forecasts",
        "grid",
        "governance",
    ):
        (output / relative).mkdir(parents=True, exist_ok=True)

    _write_readme(output)
    index = _time_index(days)
    first_interval = pd.Timestamp(index[0])
    final_interval = pd.Timestamp(index[-1])
    plans, metering, availability = _build_timeseries(index, seed)
    manifest = {
        "source_id": "v5_synthetic_fixture",
        "quality_flag": "synthetic",
        "production_eligible": False,
        "formal_billing_eligible": False,
        "timezone": _TIMEZONE,
        "frequency": "15min",
        "seed": seed,
        "days": days,
        "assets": [
            "resources/resource_catalog.yaml",
            "resources/resource_availability.csv",
            "plans/day_ahead_plan.csv",
            "metering/resource_metering.csv",
            "market/simulated_rule_snapshot.yaml",
            "market/bid_submissions.csv",
            "market/bid_status_events.csv",
            "market/award_receipts.csv",
            "settlement/simulated_billing_statement.csv",
        ],
    }
    _write_json(output / "manifest.yaml", manifest)
    _write_json(output / "resources" / "resource_catalog.yaml", _resource_catalog())
    availability.to_csv(output / "resources" / "resource_availability.csv", index=False)
    plans.to_csv(output / "plans" / "day_ahead_plan.csv", index=False)
    metering.to_csv(output / "metering" / "resource_metering.csv", index=False)

    rule = {
        "market": "simulated_v5_market",
        "rule_version": "synthetic-v1",
        "published_at": first_interval.isoformat(),
        "effective_from": first_interval.isoformat(),
        "effective_to": None,
        "confirmed": False,
        "source_document": "synthetic fixture; not a market rule",
        "parameters": {
            "product": "energy/sell",
            "interval_minutes": 15,
            "minimum_quantity_mwh": 0.01,
            "shortfall_charge_per_mwh": 100.0,
        },
    }
    _write_json(output / "market" / "simulated_rule_snapshot.yaml", rule)

    bid_rows = [
        {
            "bid_id": "synthetic-bid-001",
            "market": "simulated_v5_market",
            "resource_id": "bess-a",
            "product": "energy/sell",
            "submitted_at": first_interval.isoformat(),
            "quantity_mwh": round(float(plans.loc[plans["resource_id"] == "bess-a", "planned_discharge_energy_mwh"].sum()), 6),
            "status": "accepted",
            "quality_flag": "synthetic",
        },
        {
            "bid_id": "synthetic-bid-002",
            "market": "simulated_v5_market",
            "resource_id": "bess-b",
            "product": "energy/sell",
            "submitted_at": first_interval.isoformat(),
            "quantity_mwh": 0.25,
            "status": "rejected",
            "quality_flag": "synthetic",
        },
        {
            "bid_id": "synthetic-bid-003",
            "market": "simulated_v5_market",
            "resource_id": "bess-b",
            "product": "energy/sell",
            "submitted_at": first_interval.isoformat(),
            "quantity_mwh": 0.25,
            "status": "cancelled",
            "quality_flag": "synthetic",
        },
    ]
    award_rows = [{
        "award_id": "synthetic-award-001",
        "bid_id": "synthetic-bid-001",
        "resource_id": bid_rows[0]["resource_id"],
        "receipt_time": (first_interval + pd.Timedelta(minutes=10)).isoformat(),
        "delivery_start": first_interval.isoformat(),
        "delivery_end": (final_interval + pd.Timedelta(minutes=15)).isoformat(),
        "cleared_quantity_mwh": bid_rows[0]["quantity_mwh"],
        "status": "cleared",
        "quality_flag": "synthetic",
    }]
    pd.DataFrame(bid_rows).to_csv(output / "market" / "bid_submissions.csv", index=False)
    pd.DataFrame([
        {"bid_id": "synthetic-bid-001", "status": "submitted", "event_time": first_interval.isoformat(), "revision": 1, "quality_flag": "synthetic"},
        {"bid_id": "synthetic-bid-001", "status": "accepted", "event_time": (first_interval + pd.Timedelta(minutes=5)).isoformat(), "revision": 2, "quality_flag": "synthetic"},
        {"bid_id": "synthetic-bid-001", "status": "awarded", "event_time": (first_interval + pd.Timedelta(minutes=10)).isoformat(), "revision": 3, "quality_flag": "synthetic"},
        {"bid_id": "synthetic-bid-002", "status": "submitted", "event_time": first_interval.isoformat(), "revision": 1, "quality_flag": "synthetic"},
        {"bid_id": "synthetic-bid-002", "status": "amended", "event_time": (first_interval + pd.Timedelta(minutes=2)).isoformat(), "revision": 2, "quality_flag": "synthetic"},
        {"bid_id": "synthetic-bid-002", "status": "rejected", "event_time": (first_interval + pd.Timedelta(minutes=3)).isoformat(), "revision": 3, "quality_flag": "synthetic"},
        {"bid_id": "synthetic-bid-003", "status": "submitted", "event_time": first_interval.isoformat(), "revision": 1, "quality_flag": "synthetic"},
        {"bid_id": "synthetic-bid-003", "status": "cancelled", "event_time": (first_interval + pd.Timedelta(minutes=1)).isoformat(), "revision": 2, "quality_flag": "synthetic"},
    ]).to_csv(output / "market" / "bid_status_events.csv", index=False)
    pd.DataFrame(award_rows).to_csv(output / "market" / "award_receipts.csv", index=False)

    bess_a = metering[metering["resource_id"] == "bess-a"]
    awarded = float(bid_rows[0]["quantity_mwh"])
    metered = float(
        bess_a["actual_discharge_energy_mwh"].to_numpy(dtype=float).sum()
    )
    shortfall = max(0.0, awarded - metered)
    pd.DataFrame([{
        "statement_id": "synthetic-statement-001",
        "revision": 1,
        "resource_id": "bess-a",
        "award_id": "synthetic-award-001",
        "awarded_mwh": round(awarded, 6),
        "metered_mwh": round(metered, 6),
        "shortfall_mwh": round(shortfall, 6),
        "simulated_shortfall_charge": round(shortfall * 100.0, 6),
        "rule_version": "synthetic-v1",
        "confirmed": False,
        "quality_flag": "synthetic",
    }]).to_csv(output / "settlement" / "simulated_billing_statement.csv", index=False)
    _write_json(output / "settlement" / "settlement_rule_cases.yaml", {
        "rule_version": "synthetic-v1",
        "shortfall_charge_cny_per_mwh": 100.0,
        "quality_flag": "synthetic",
        "confirmed": False,
        "source_document": "synthetic fixture; not a market rule",
    })

    pd.DataFrame({
        "event_time": [pd.Timestamp(item).isoformat() for item in index],
        "published_at": [
            (pd.Timestamp(item) - pd.Timedelta(hours=2)).isoformat()
            for item in index
        ],
        "available_at": [
            (pd.Timestamp(item) - pd.Timedelta(hours=1)).isoformat()
            for item in index
        ],
        "source_version": "synthetic-forecast-v1",
        "quality_flag": "synthetic",
    }).to_csv(output / "forecasts" / "archived_vintages.csv", index=False)
    _write_json(output / "grid" / "grid_snapshot.yaml", {
        "source_id": "v5_synthetic_fixture",
        "quality_flag": "synthetic",
        "topology_version": "synthetic-grid-v1",
    })
    _write_json(output / "governance" / "champion.yaml", {
        "version": "synthetic-champion-v1",
        "environment": "synthetic-only",
        "production_eligible": False,
    })
    _write_json(output / "governance" / "challenger.yaml", {
        "version": "synthetic-challenger-v1",
        "environment": "synthetic-only",
        "production_eligible": False,
    })
    _write_json(output / "governance" / "drift_baseline.yaml", {
        "version": "synthetic-drift-v1",
        "environment": "synthetic-only",
        "max_total_shortfall_mwh": 1.0,
        "production_eligible": False,
    })
    _write_json(output / "governance" / "rollback_runbook.yaml", {
        "environment": "synthetic-only",
        "production_eligible": False,
        "rollback_target": "synthetic champion fixture",
    })
    return output


if __name__ == "__main__":
    data_root = Path(__file__).resolve().parents[3] / "data" / "trading"
    written = write_v5_simulation_fixtures(data_root)
    print(f"wrote V5 synthetic fixtures under {written}")
