"""V5 synthetic 仿真资产的可重复生成契约。"""

from __future__ import annotations

import json

import pandas as pd

from ele_trading.trading.v5_simulation_fixtures import write_v5_simulation_fixtures


def test_v5_simulation_fixtures_are_isolated_and_explicitly_non_production(tmp_path):
    output_dir = write_v5_simulation_fixtures(tmp_path, days=2, seed=17)

    manifest = json.loads((output_dir / "manifest.yaml").read_text())
    assert output_dir == tmp_path / "v5_simulation"
    assert manifest["source_id"] == "v5_synthetic_fixture"
    assert manifest["quality_flag"] == "synthetic"
    assert manifest["production_eligible"] is False
    assert manifest["formal_billing_eligible"] is False

    rule = json.loads(
        (output_dir / "market" / "simulated_rule_snapshot.yaml").read_text()
    )
    assert rule["market"] == "simulated_v5_market"
    assert rule["confirmed"] is False

    metering = pd.read_csv(output_dir / "metering" / "resource_metering.csv")
    assert set(metering["quality_flag"]) == {"synthetic"}
    assert {"event_time", "available_at", "source_version", "revision"} <= set(
        metering.columns
    )
    assert set(metering["resource_id"]) == {"bess-a", "bess-b"}

    statement = pd.read_csv(
        output_dir / "settlement" / "simulated_billing_statement.csv"
    )
    assert not bool(statement["confirmed"].astype(bool).any())

    status_events = pd.read_csv(output_dir / "market" / "bid_status_events.csv")
    assert status_events["status"].tolist() == [
        "submitted",
        "accepted",
        "awarded",
        "submitted",
        "amended",
        "rejected",
        "submitted",
        "cancelled",
    ]
    assert status_events.groupby("bid_id")["revision"].apply(list).to_dict() == {
        "synthetic-bid-001": [1, 2, 3],
        "synthetic-bid-002": [1, 2, 3],
        "synthetic-bid-003": [1, 2],
    }
    assert set(status_events["quality_flag"]) == {"synthetic"}


def test_v5_simulation_fixture_generation_is_deterministic(tmp_path):
    first = write_v5_simulation_fixtures(tmp_path / "first", days=1, seed=23)
    second = write_v5_simulation_fixtures(tmp_path / "second", days=1, seed=23)

    first_metering = (first / "metering" / "resource_metering.csv").read_bytes()
    second_metering = (second / "metering" / "resource_metering.csv").read_bytes()
    assert first_metering == second_metering
