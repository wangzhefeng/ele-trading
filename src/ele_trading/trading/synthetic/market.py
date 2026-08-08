"""仅用于 V5 fixture 的 synthetic 市场状态回放账本。

此模块不实现市场接口，不属于 ``MarketMode`` capability，也不产生可用于
正式报价、正式账单或生产晋级的证据。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pandas as pd

from ele_trading.domain.contracts import BillingStatement


class SyntheticBidStatus(str, Enum):
    """模拟报价的最小状态集合。"""

    SUBMITTED = "submitted"
    AMENDED = "amended"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    AWARDED = "awarded"


@dataclass(frozen=True, slots=True)
class SyntheticAward:
    """幂等回放的模拟成交记录。"""

    bid_id: str
    award_id: str
    simulation_only: bool = True


@dataclass(frozen=True, slots=True)
class SyntheticBidEvent:
    """Synthetic bid 的版本化状态事件。"""

    bid_id: str
    status: SyntheticBidStatus
    revision: int


class SyntheticBidLedger:
    """受限的模拟报价账本，不向真实市场提交任何请求。"""

    formal_submission_eligible = False

    def __init__(self) -> None:
        self._statuses: dict[str, SyntheticBidStatus] = {}
        self._awards: dict[str, SyntheticAward] = {}
        self._events: dict[str, list[SyntheticBidEvent]] = {}

    def _record_event(
        self,
        *,
        bid_id: str,
        status: SyntheticBidStatus,
    ) -> SyntheticBidEvent:
        events = self._events.setdefault(bid_id, [])
        event = SyntheticBidEvent(
            bid_id=bid_id,
            status=status,
            revision=len(events) + 1,
        )
        events.append(event)
        self._statuses[bid_id] = status
        return event

    @staticmethod
    def _bid_id(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("bid_id must not be empty")
        return value.strip()

    @staticmethod
    def _award_id(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("award_id must not be empty")
        return value.strip()

    def submit(self, *, bid_id: str) -> None:
        normalized = self._bid_id(bid_id)
        if normalized in self._statuses:
            raise ValueError(f"synthetic bid already exists: {normalized!r}")
        self._record_event(
            bid_id=normalized,
            status=SyntheticBidStatus.SUBMITTED,
        )

    def accept(self, *, bid_id: str) -> None:
        normalized = self._bid_id(bid_id)
        if self._statuses.get(normalized) not in {
            SyntheticBidStatus.SUBMITTED,
            SyntheticBidStatus.AMENDED,
        }:
            raise ValueError("only open synthetic bids can be accepted")
        self._record_event(bid_id=normalized, status=SyntheticBidStatus.ACCEPTED)

    def amend(self, *, bid_id: str) -> SyntheticBidEvent:
        """记录模拟报价的版本修订；不修改任何正式市场对象。"""
        normalized = self._bid_id(bid_id)
        if self._statuses.get(normalized) not in {
            SyntheticBidStatus.SUBMITTED,
            SyntheticBidStatus.ACCEPTED,
        }:
            raise ValueError("only open synthetic bids can be amended")
        return self._record_event(
            bid_id=normalized,
            status=SyntheticBidStatus.AMENDED,
        )

    def reject(self, *, bid_id: str) -> None:
        normalized = self._bid_id(bid_id)
        if self._statuses.get(normalized) not in {
            SyntheticBidStatus.SUBMITTED,
            SyntheticBidStatus.AMENDED,
            SyntheticBidStatus.ACCEPTED,
        }:
            raise ValueError("only open synthetic bids can be rejected")
        self._record_event(bid_id=normalized, status=SyntheticBidStatus.REJECTED)

    def cancel(self, *, bid_id: str) -> None:
        normalized = self._bid_id(bid_id)
        status = self._statuses.get(normalized)
        if status not in {
            SyntheticBidStatus.SUBMITTED,
            SyntheticBidStatus.AMENDED,
            SyntheticBidStatus.ACCEPTED,
        }:
            raise ValueError("only open synthetic bids can be cancelled")
        self._record_event(bid_id=normalized, status=SyntheticBidStatus.CANCELLED)

    def record_award(self, *, bid_id: str, award_id: str) -> SyntheticAward:
        normalized_bid = self._bid_id(bid_id)
        normalized_award = self._award_id(award_id)
        existing = self._awards.get(normalized_award)
        if existing is not None:
            if existing.bid_id != normalized_bid:
                raise ValueError("synthetic award ID is already associated with another bid")
            return existing
        status = self._statuses.get(normalized_bid)
        if status is SyntheticBidStatus.CANCELLED:
            raise ValueError("cannot award a cancelled synthetic bid")
        if status is not SyntheticBidStatus.ACCEPTED:
            raise ValueError("only accepted synthetic bids can receive an award")
        award = SyntheticAward(bid_id=normalized_bid, award_id=normalized_award)
        self._awards[normalized_award] = award
        self._record_event(bid_id=normalized_bid, status=SyntheticBidStatus.AWARDED)
        return award

    def status_of(self, bid_id: str) -> SyntheticBidStatus:
        normalized = self._bid_id(bid_id)
        try:
            return self._statuses[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown synthetic bid: {normalized!r}") from exc

    def events_for(self, bid_id: str) -> tuple[SyntheticBidEvent, ...]:
        """返回不可变的 synthetic 状态审计序列。"""
        normalized = self._bid_id(bid_id)
        try:
            return tuple(self._events[normalized])
        except KeyError as exc:
            raise ValueError(f"unknown synthetic bid: {normalized!r}") from exc

    def bid_ids(self) -> tuple[str, ...]:
        """返回回放得到的 bid ID，供 simulation-only 检查读取。"""
        return tuple(sorted(self._statuses))


def replay_synthetic_market_assets(directory: str | Path) -> SyntheticBidLedger:
    """回放 synthetic fixture 的状态事件和 Award，不连接任何外部市场。"""
    root = Path(directory)
    events = pd.read_csv(root / "market" / "bid_status_events.csv")
    awards = pd.read_csv(root / "market" / "award_receipts.csv")
    required_event_columns = {"bid_id", "status", "quality_flag"}
    if not required_event_columns <= set(events.columns):
        raise ValueError("synthetic bid status events are missing required columns")
    if "quality_flag" not in awards or set(events["quality_flag"]) != {"synthetic"}:
        raise ValueError("synthetic market replay requires synthetic-only events")
    if set(awards["quality_flag"]) != {"synthetic"}:
        raise ValueError("synthetic market replay requires synthetic-only awards")

    ledger = SyntheticBidLedger()
    awarded_bid_ids: set[str] = set()
    for row in events.itertuples(index=False):
        status = str(row.status)
        bid_id = str(row.bid_id)
        if status == SyntheticBidStatus.SUBMITTED.value:
            ledger.submit(bid_id=bid_id)
        elif status == SyntheticBidStatus.AMENDED.value:
            ledger.amend(bid_id=bid_id)
        elif status == SyntheticBidStatus.ACCEPTED.value:
            ledger.accept(bid_id=bid_id)
        elif status == SyntheticBidStatus.REJECTED.value:
            ledger.reject(bid_id=bid_id)
        elif status == SyntheticBidStatus.CANCELLED.value:
            ledger.cancel(bid_id=bid_id)
        elif status == SyntheticBidStatus.AWARDED.value:
            awarded_bid_ids.add(bid_id)
        else:
            raise ValueError(f"unsupported synthetic bid status: {status!r}")
    for row in awards.itertuples(index=False):
        bid_id = str(row.bid_id)
        if bid_id not in awarded_bid_ids:
            raise ValueError("synthetic award has no awarded status event")
        ledger.record_award(bid_id=bid_id, award_id=str(row.award_id))
    return ledger


def load_synthetic_billing_statement(directory: str | Path) -> BillingStatement:
    """加载 synthetic 账单，并强制保持为非正式账单。"""
    statements = pd.read_csv(
        Path(directory) / "settlement" / "simulated_billing_statement.csv"
    )
    if len(statements) != 1:
        raise ValueError("synthetic billing fixture must contain exactly one statement")
    row = statements.iloc[0]
    if str(row.get("quality_flag", "")).strip() != "synthetic":
        raise ValueError("synthetic billing statement must be marked synthetic")
    if str(row.get("confirmed", "")).strip().lower() not in {"false", "0"}:
        raise ValueError("synthetic billing statement must remain unconfirmed")
    return BillingStatement(
        statement_version=str(row["statement_id"]),
        lines={
            "simulated_shortfall_charge": float(row["simulated_shortfall_charge"]),
        },
        confirmed=False,
    )
