"""V5 新领域契约的公共包导出。"""

from __future__ import annotations

from ele_trading.domain import (
    AwardFulfillment,
    AwardedCommitment,
    MatchedAward,
    ResourceExecutionDeviation,
    ResourceMetering,
    match_award_receipt,
)


def test_award_commitment_contracts_are_exported_from_domain() -> None:
    """上层消费者只通过领域包导入公开契约。"""
    assert AwardedCommitment.__name__ == "AwardedCommitment"
    assert AwardFulfillment.__name__ == "AwardFulfillment"
    assert MatchedAward.__name__ == "MatchedAward"
    assert ResourceMetering.__name__ == "ResourceMetering"
    assert ResourceExecutionDeviation.__name__ == "ResourceExecutionDeviation"
    assert callable(match_award_receipt)
