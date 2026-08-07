from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_PRODUCT_TYPES = {"STK", "STOCK", "STOCKS"}
SUPPORTED_CURRENCIES = {"USD"}
SUPPORTED_COUNTRIES = {"US"}
OTC_EXACT_VENUE_CODES = {
    "ARCAEDGE",
    "GREY",
    "NQB",
    "OTC",
    "OTCBB",
    "OTCLNKECN",
    "OTCM",
    "OTCQB",
    "OTCQX",
    "PINK",
    "PINX",
    "XOTC",
}
OTC_VENUE_MARKERS = ("OTC", "PINK")


@dataclass(frozen=True, slots=True)
class TradabilityRule:
    code: str
    description: str


TRADABILITY_RULES: tuple[TradabilityRule, ...] = (
    TradabilityRule("inactive_symbol", "The provider symbol is not active."),
    TradabilityRule("inactive_listing", "The exchange listing is not active."),
    TradabilityRule("inactive_security", "The canonical security is not active."),
    TradabilityRule("unsupported_product_type", "The security is not a supported US stock/common-stock instrument."),
    TradabilityRule("unsupported_currency", "The listing currency is not USD."),
    TradabilityRule("unsupported_country", "The listing exchange is not a US exchange."),
    TradabilityRule("unsupported_otc_venue", "The listing trades over the counter and is outside the configured US-listed-stock scope."),
    TradabilityRule("missing_or_invalid_ibkr_conid", "The listing does not have a valid positive IBKR conid."),
    TradabilityRule("open_mapping_issue", "A source mapping issue is still open for this security/listing/symbol."),
    TradabilityRule("ambiguous_ibkr_contract", "IBKR returned more than one plausible contract and no unique listing was accepted."),
    TradabilityRule("exchange_mapping_unresolved", "Massive/IBKR exchange evidence cannot be mapped to one canonical exchange."),
)


def is_otc_venue(*values: object) -> bool:
    for value in values:
        normalized = str(value or "").strip().upper().replace(" ", "")
        if not normalized:
            continue
        if normalized in OTC_EXACT_VENUE_CODES:
            return True
        if any(marker in normalized for marker in OTC_VENUE_MARKERS):
            return True
    return False


def otc_venue_predicate_sql(*expressions: str) -> str:
    exact = ", ".join(f"'{value}'" for value in sorted(OTC_EXACT_VENUE_CODES))
    predicates: list[str] = []
    for expression in expressions:
        normalized = f"upper(replaceAll(ifNull(toString({expression}), ''), ' ', ''))"
        marker_checks = " OR ".join(f"position({normalized}, '{marker}') > 0" for marker in OTC_VENUE_MARKERS)
        predicates.append(f"({normalized} IN ({exact}) OR {marker_checks})")
    return "(" + " OR ".join(predicates) + ")" if predicates else "0"


def non_otc_venue_predicate_sql(*expressions: str) -> str:
    return f"NOT {otc_venue_predicate_sql(*expressions)}"


def tradability_rule_markdown() -> str:
    lines = [
        "| Code | Meaning |",
        "| --- | --- |",
    ]
    for rule in TRADABILITY_RULES:
        lines.append(f"| `{rule.code}` | {rule.description} |")
    return "\n".join(lines)
