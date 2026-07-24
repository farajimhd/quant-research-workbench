from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo

EXCHANGE_TZ = ZoneInfo("America/New_York")


# V9 deliberately contains only point-in-time raw observations. Issuer identity,
# current reference attributes, derived health scores, float, market cap, and
# undated short-interest values are excluded from this contract.
SEC_CONCEPT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "current_debt": ("ShortTermBorrowings", "LongTermDebtCurrent"),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "shares_outstanding": ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"),
    "weighted_basic_shares": ("WeightedAverageNumberOfSharesOutstandingBasic", "WeightedAverageShares"),
    "weighted_diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"),
}

SEC_CONCEPTS = tuple(SEC_CONCEPT_TAGS)
SEC_TAG_PRIORITY = {
    tag: (concept, priority)
    for concept, tags in SEC_CONCEPT_TAGS.items()
    for priority, tag in enumerate(tags)
}
SEC_TAGS = tuple(SEC_TAG_PRIORITY)

SEC_FIELDS_PER_CONCEPT = ("value", "present", "age", "period")
MARKET_FIELDS = (
    "anchor_price", "anchor_present", "anchor_age",
    "prior_close", "prior_volume", "prior_bar_age", "prior_bar_present",
    "short_volume", "short_total_volume", "short_exempt_volume", "short_ratio", "short_age", "short_present",
)
STOCK_STATE_NAMES = tuple(
    f"sec_{concept}_{field}" for concept in SEC_CONCEPTS for field in SEC_FIELDS_PER_CONCEPT
) + MARKET_FIELDS
STOCK_STATE_DIM = len(STOCK_STATE_NAMES)


def contract_payload() -> dict[str, Any]:
    return {
        "version": "point_in_time_stock_state_v1",
        "sec_concept_tags": SEC_CONCEPT_TAGS,
        "sec_fields_per_concept": SEC_FIELDS_PER_CONCEPT,
        "market_fields": MARKET_FIELDS,
        "availability": {
            "sec": "filed_at_utc strictly before news published_at_utc",
            "anchor": "last clean market observation strictly before publication from reaction labels",
            "daily_bar": "latest completed 1d trade bar with bar_end strictly before publication",
            "short_volume": "latest trade_date strictly before exchange-local publication date",
        },
        "excluded": [
            "ticker", "company_name", "country", "sector", "market_cap", "float",
            "short_interest", "derived_fundamentals", "health_scores",
        ],
        "value_transform": "clip(sign(value)*log1p(abs(value))/20,-4,4)",
        "age_transform": "clip(log1p(age_days)/10,0,4)",
        "dimension": STOCK_STATE_DIM,
        "names": STOCK_STATE_NAMES,
    }


def contract_sha256() -> str:
    body = json.dumps(contract_payload(), sort_keys=True, separators=(",", ":"), default=list)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse_timestamp(value: Any) -> datetime:
    text = str(value).replace(" ", "T")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def signed_log(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(-4.0, min(4.0, math.copysign(math.log1p(abs(number)) / 20.0, number)))


def age_feature(days: float) -> float:
    if not math.isfinite(days) or days < 0:
        return 0.0
    return min(4.0, math.log1p(days) / 10.0)


def period_feature(fiscal_period: Any) -> float:
    period = str(fiscal_period or "").upper()
    return {"Q1": 0.25, "Q2": 0.50, "Q3": 0.75, "FY": 1.0}.get(period, 0.0)


@dataclass(frozen=True, slots=True)
class Observation:
    at: datetime
    value: float
    period: float = 0.0
    priority: int = 0


class ObservationIndex:
    def __init__(self, observations: Iterable[Observation]) -> None:
        ordered = sorted(observations, key=lambda item: (item.at, item.priority))
        self._rows = ordered
        self._times = [item.at for item in ordered]

    def before(self, timestamp: datetime) -> Observation | None:
        index = bisect.bisect_left(self._times, timestamp) - 1
        return self._rows[index] if index >= 0 else None


def encode_stock_state(
    published_at: datetime,
    sec: dict[str, Observation | None],
    *,
    anchor_price: Any = None,
    anchor_at: datetime | None = None,
    prior_bar: dict[str, Any] | None = None,
    short_volume: dict[str, Any] | None = None,
) -> list[float]:
    values: list[float] = []
    for concept in SEC_CONCEPTS:
        observation = sec.get(concept)
        if observation is None:
            values.extend((0.0, 0.0, 0.0, 0.0))
        else:
            age_days = (published_at - observation.at).total_seconds() / 86400.0
            values.extend((signed_log(observation.value), 1.0, age_feature(age_days), observation.period))

    anchor_ok = anchor_price is not None and anchor_at is not None and anchor_at < published_at
    values.extend((
        signed_log(anchor_price) if anchor_ok else 0.0,
        1.0 if anchor_ok else 0.0,
        age_feature((published_at - anchor_at).total_seconds() / 86400.0) if anchor_ok else 0.0,
    ))

    bar = prior_bar or {}
    bar_at = bar.get("bar_end")
    bar_ok = isinstance(bar_at, datetime) and bar_at < published_at
    values.extend((
        signed_log(bar.get("close")) if bar_ok else 0.0,
        signed_log(bar.get("volume")) if bar_ok else 0.0,
        age_feature((published_at - bar_at).total_seconds() / 86400.0) if bar_ok else 0.0,
        1.0 if bar_ok else 0.0,
    ))

    short = short_volume or {}
    short_date = short.get("trade_date")
    exchange_date = published_at.astimezone(EXCHANGE_TZ).date()
    short_ok = isinstance(short_date, date) and short_date < exchange_date
    values.extend((
        signed_log(short.get("short_volume")) if short_ok else 0.0,
        signed_log(short.get("total_volume")) if short_ok else 0.0,
        signed_log(short.get("exempt_volume")) if short_ok else 0.0,
        float(short.get("short_volume_ratio") or 0.0) if short_ok else 0.0,
        age_feature(float((exchange_date - short_date).days)) if short_ok else 0.0,
        1.0 if short_ok else 0.0,
    ))
    if len(values) != STOCK_STATE_DIM or any(not math.isfinite(value) for value in values):
        raise ValueError(f"Invalid stock-state vector: dimension={len(values)} expected={STOCK_STATE_DIM}.")
    return values
