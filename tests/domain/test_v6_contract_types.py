"""V6-0 合同类型：旧 q_long/p_long 只能显式兼容为金融差价合同。"""

from __future__ import annotations

import pandas as pd
import pytest

from ele_trading.domain.contracts import ContractType, PositionState


AS_OF = pd.Timestamp("2026-07-01 00:00", tz="Asia/Shanghai")
INDEX = pd.date_range(AS_OF + pd.Timedelta(minutes=15), periods=2, freq="15min")


def _position(**overrides) -> PositionState:
    values = {
        "as_of": AS_OF,
        "q_long": pd.Series(1.0, index=INDEX),
        "p_long": pd.Series(300.0, index=INDEX),
        "source_version": "position-v1",
    }
    values.update(overrides)
    return PositionState(**values)


def test_legacy_position_state_defaults_to_explicit_financial_difference():
    position = _position()

    assert position.contract_type is ContractType.FINANCIAL_DIFFERENCE


def test_position_state_rejects_string_contract_type_to_avoid_implicit_semantics():
    with pytest.raises(ValueError, match="contract_type"):
        _position(contract_type="physical_delivery")
