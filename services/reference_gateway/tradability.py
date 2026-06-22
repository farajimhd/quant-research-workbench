from __future__ import annotations

from dataclasses import dataclass


SUPPORTED_PRODUCT_TYPES = {"STK", "STOCK", "STOCKS"}
SUPPORTED_CURRENCIES = {"USD"}
SUPPORTED_COUNTRIES = {"US"}


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
    TradabilityRule("missing_or_invalid_ibkr_conid", "The listing does not have a valid positive IBKR conid."),
    TradabilityRule("open_mapping_issue", "A source mapping issue is still open for this security/listing/symbol."),
    TradabilityRule("ambiguous_ibkr_contract", "IBKR returned more than one plausible contract and no unique listing was accepted."),
    TradabilityRule("exchange_mapping_unresolved", "Massive/IBKR exchange evidence cannot be mapped to one canonical exchange."),
)


def tradability_rule_markdown() -> str:
    lines = [
        "| Code | Meaning |",
        "| --- | --- |",
    ]
    for rule in TRADABILITY_RULES:
        lines.append(f"| `{rule.code}` | {rule.description} |")
    return "\n".join(lines)

