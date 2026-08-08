"""V6-0 市场 profile 骨架：规则缺失时只允许 plan-only，默认拒绝正式路径。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

import pandas as pd

from ele_trading.data_provider.contracts import DataCatalog


class MarketPhase(StrEnum):
    """市场时序中需要规则确认的正式阶段。"""

    REGISTRATION = "registration"
    BID = "bid"
    AMEND = "amend"
    CANCEL = "cancel"
    CLEARING = "clearing"
    DISPATCH = "dispatch"
    METER_CLOSE = "meter_close"
    SETTLEMENT_CLOSE = "settlement_close"


class MarketProfileRejected(ValueError):
    """市场 profile 缺少已确认规则时的结构化拒绝。"""


@dataclass(frozen=True, slots=True)
class MarketTimelinePolicy:
    """声明已获确认、可进入的市场阶段；空集表示尚无正式规则。"""

    supported_phases: frozenset[MarketPhase] = frozenset()

    def require_supported(self, phase: MarketPhase) -> None:
        if phase not in self.supported_phases:
            raise MarketProfileRejected(
                f"market phase {phase.value!r} is not confirmed by the profile"
            )


@dataclass(frozen=True, slots=True)
class MarketInputEvidence:
    """任一市场输入的来源、可用时刻、质量、修订和权限证据。"""

    source: str
    available_at: pd.Timestamp
    quality: str
    complete: bool
    revision: str
    permission: str
    rule_version: str
    catalog_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "source",
            "quality",
            "revision",
            "permission",
            "rule_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-empty")
        available_at = pd.Timestamp(self.available_at)
        if pd.isna(available_at) or available_at.tzinfo is None:
            raise ValueError("available_at must be timezone-aware")
        if not isinstance(self.complete, bool):
            raise ValueError("complete must be bool")
        object.__setattr__(self, "available_at", available_at)
        if self.catalog_id is not None and (
            not isinstance(self.catalog_id, str) or not self.catalog_id.strip()
        ):
            raise ValueError("catalog_id must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class DataAdmissionPolicy:
    """正式路径的来源、质量、完整性、修订、权限和时间准入规则。"""

    required_rule_version: str | None = None
    admitted_sources: frozenset[str] = frozenset()
    catalog: DataCatalog | None = None

    def require_rule_version(self, rule_version: str | None) -> None:
        if self.required_rule_version is None:
            raise MarketProfileRejected(
                "data admission policy is not configured for a formal path"
            )
        if rule_version != self.required_rule_version:
            raise MarketProfileRejected("input rule_version is not admitted")

    def admit(
        self,
        *,
        evidence: MarketInputEvidence,
        decision_time: pd.Timestamp,
    ) -> None:
        """拒绝在本次决策后才可见、未确认或未获授权的输入。"""
        if not isinstance(evidence, MarketInputEvidence):
            raise ValueError("evidence must be MarketInputEvidence")
        decision_at = pd.Timestamp(decision_time)
        if pd.isna(decision_at) or decision_at.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        self.require_rule_version(evidence.rule_version)
        if evidence.source not in self.admitted_sources:
            raise MarketProfileRejected("input source is not admitted")
        if evidence.available_at > decision_at:
            raise MarketProfileRejected("input became available after decision_time")
        if evidence.quality != "approved":
            raise MarketProfileRejected("input quality is not approved")
        if not evidence.complete:
            raise MarketProfileRejected("input is incomplete")
        if evidence.permission != "authorized":
            raise MarketProfileRejected("input permission is not authorized")
        if self.catalog is not None:
            if evidence.catalog_id is None:
                raise MarketProfileRejected("input catalog_id is required")
            entry = self.catalog.require_available(
                evidence.catalog_id,
                as_of=decision_at,
            )
            if entry.availability.source_id != evidence.source:
                raise MarketProfileRejected("catalog source does not match input source")
            if entry.availability.version != evidence.revision:
                raise MarketProfileRejected("catalog version does not match input revision")
            if entry.availability.available_at != evidence.available_at:
                raise MarketProfileRejected(
                    "catalog availability does not match input available_at"
                )


@dataclass(frozen=True, slots=True)
class PricingAndNetworkPolicy:
    """价格点、交割位置和安全结果的显式占位，避免曲线被跨位置复用。"""

    price_point: str | None = None
    delivery_location: str | None = None
    requires_authorized_security_result: bool = True


@dataclass(frozen=True, slots=True)
class PreTradeRiskPolicy:
    """信用、担保和资源敞口规则的显式占位。"""

    submission_enabled: bool = False


@dataclass(frozen=True, slots=True)
class ProductCatalog:
    """产品资格目录；空目录不等价于允许任意字符串产品。"""

    products: frozenset[str] = frozenset()

    def require_product(self, product: str) -> None:
        if product not in self.products:
            raise MarketProfileRejected(
                f"product {product!r} is not confirmed by the profile"
            )


@dataclass(frozen=True, slots=True)
class CommitmentProjectionPolicy:
    """产品×方向到资源约束的规则化映射；未映射时必须拒绝。"""

    mappings: Mapping[tuple[str, str], str] = field(default_factory=dict)

    def project(self, *, product: str, direction: str) -> str:
        try:
            return self.mappings[(product, direction)]
        except KeyError as exc:
            raise MarketProfileRejected(
                "unmapped product/direction: "
                f"{product!r}/{direction!r}"
            ) from exc


@dataclass(frozen=True, slots=True)
class MarketProfile:
    """市场模式共享的规则版本与准入骨架。"""

    market_id: str
    rule_version: str
    timezone: str | None
    plan_only: bool
    timeline: MarketTimelinePolicy = field(default_factory=MarketTimelinePolicy)
    data_admission: DataAdmissionPolicy = field(
        default_factory=DataAdmissionPolicy
    )
    products: ProductCatalog = field(default_factory=ProductCatalog)
    pricing_and_network: PricingAndNetworkPolicy = field(
        default_factory=PricingAndNetworkPolicy
    )
    pre_trade_risk: PreTradeRiskPolicy = field(
        default_factory=PreTradeRiskPolicy
    )
    commitment_projection: CommitmentProjectionPolicy = field(
        default_factory=CommitmentProjectionPolicy
    )

    def __post_init__(self) -> None:
        if not isinstance(self.market_id, str) or not self.market_id.strip():
            raise ValueError("market_id must be non-empty")
        if not isinstance(self.rule_version, str) or not self.rule_version.strip():
            raise ValueError("rule_version must be non-empty")
        if self.timezone is not None and (
            not isinstance(self.timezone, str) or not self.timezone.strip()
        ):
            raise ValueError("timezone must be non-empty when provided")
        if not isinstance(self.plan_only, bool):
            raise ValueError("plan_only must be bool")

    @property
    def is_formal_path(self) -> bool:
        """只有完整、明确启用的规则 profile 才有资格进入正式路径。"""
        return (
            not self.plan_only
            and self.timezone is not None
            and self.rule_version != "unconfirmed"
            and self.data_admission.required_rule_version is not None
            and self.pre_trade_risk.submission_enabled
        )

    @classmethod
    def plan_only_profile(cls, *, market_id: str) -> "MarketProfile":
        """为既有研究 mode 创建明确不可提交的 profile。"""
        return cls(
            market_id=market_id,
            rule_version="unconfirmed",
            timezone=None,
            plan_only=True,
        )

    def require_formal_phase(self, phase: MarketPhase) -> None:
        """进入正式市场阶段前验证 profile 完整性和阶段授权。"""
        if self.plan_only:
            raise MarketProfileRejected(
                "plan-only market profile cannot admit formal market phases"
            )
        if not self.is_formal_path:
            raise MarketProfileRejected(
                "market profile is incomplete for a formal path"
            )
        self.timeline.require_supported(phase)

    def project_commitment(self, *, product: str, direction: str) -> str:
        """只按 profile 显式映射投影产品义务。"""
        projection = self.commitment_projection.project(
            product=product,
            direction=direction,
        )
        self.products.require_product(product)
        return projection
