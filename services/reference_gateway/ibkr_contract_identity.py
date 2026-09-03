from __future__ import annotations

import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable


MASSIVE_TO_IBKR_LISTING_EXCHANGE: dict[str, str] = {
    "XNAS": "NASDAQ",
    "XNGS": "NASDAQ",
    "XNCM": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "XNYQ": "NYSE",
    "XNYS": "NYSE",
    "NYSE": "NYSE",
    "ARCX": "ARCA",
    "NYSEARCA": "ARCA",
    "XASE": "AMEX",
    "AMEX": "AMEX",
    "BATS": "BATS",
}

_LEGAL_NAME_WORDS = {
    "AG",
    "CLASS",
    "CL",
    "CO",
    "COMPANY",
    "CORP",
    "CORPORATION",
    "COMMON",
    "DE",
    "INC",
    "INCORPORATED",
    "LIMITED",
    "LLC",
    "LP",
    "LTD",
    "NV",
    "ORD",
    "ORDINARY",
    "PLC",
    "SA",
    "SHARES",
    "SHARE",
    "SHS",
    "STOCK",
    "THE",
    "VOTING",
}


@dataclass(frozen=True, slots=True)
class IbkrContractResolution:
    status: str
    reason: str
    expected_listing_exchange: str
    conid: int | None
    candidate_conids: tuple[int, ...]
    broker_symbol: str = ""
    broker_company_name: str = ""
    broker_listing_exchange: str = ""
    broker_currency: str = ""
    company_name_match: bool | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted" and self.conid is not None


def normalize_equity_symbol(value: Any) -> str:
    """Normalize only separator syntax while preserving the share-class suffix."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).split())


def ibkr_search_symbols(massive_ticker: str) -> tuple[str, ...]:
    ticker = str(massive_ticker or "").strip().upper()
    if not ticker:
        return ()
    values = [ticker]
    class_form = re.sub(r"[.\-/]+", " ", ticker)
    if class_form != ticker:
        values.append(class_form)
    return tuple(dict.fromkeys(values))


def expected_ibkr_listing_exchange(massive_exchange: Any) -> str:
    return MASSIVE_TO_IBKR_LISTING_EXCHANGE.get(str(massive_exchange or "").strip().upper(), "")


def search_row_is_stock(row: dict[str, Any]) -> bool:
    sections = row.get("sections") if isinstance(row.get("sections"), list) else []
    return any(
        isinstance(section, dict)
        and str(section.get("secType") or section.get("sec_type") or "").strip().upper() in {"STK", "STOCK"}
        for section in sections
    ) or str(row.get("secType") or row.get("sec_type") or row.get("assetClass") or "").strip().upper() in {"STK", "STOCK"}


def positive_conids(rows: Iterable[dict[str, Any]]) -> tuple[int, ...]:
    values: set[int] = set()
    for row in rows:
        if not search_row_is_stock(row):
            continue
        try:
            conid = int(row.get("conid") or row.get("con_id") or 0)
        except (TypeError, ValueError):
            continue
        if conid > 0:
            values.add(conid)
    return tuple(sorted(values))


def company_names_compatible(massive_name: Any, broker_name: Any) -> bool:
    massive = _normalized_company_name(massive_name)
    broker = _normalized_company_name(broker_name)
    if not massive or not broker:
        return False
    if massive == broker:
        return True
    if min(len(massive), len(broker)) >= 8 and (massive.startswith(broker) or broker.startswith(massive)):
        return True
    massive_tokens = massive.split()
    broker_tokens = broker.split()
    if not massive_tokens or not broker_tokens or massive_tokens[0] != broker_tokens[0]:
        return False
    shared = set(massive_tokens) & set(broker_tokens)
    token_coverage = len(shared) / max(1, min(len(set(massive_tokens)), len(set(broker_tokens))))
    return token_coverage >= 0.8 and SequenceMatcher(None, massive, broker).ratio() >= 0.72


def resolve_massive_ibkr_contract(
    *,
    massive_ticker: str,
    massive_name: str,
    massive_exchange: str,
    definitions: Iterable[dict[str, Any]],
) -> IbkrContractResolution:
    expected_exchange = expected_ibkr_listing_exchange(massive_exchange)
    if not expected_exchange:
        return IbkrContractResolution(
            status="blocked",
            reason="unsupported_massive_exchange",
            expected_listing_exchange="",
            conid=None,
            candidate_conids=(),
        )

    ticker_key = normalize_equity_symbol(massive_ticker)
    valid: list[dict[str, Any]] = []
    definition_by_conid: dict[int, dict[str, Any]] = {}
    seen_conids: set[int] = set()
    rejected_reasons: set[str] = set()
    all_positive: set[int] = set()
    for row in definitions:
        try:
            conid = int(row.get("conid") or row.get("con_id") or 0)
        except (TypeError, ValueError):
            continue
        if conid <= 0:
            continue
        all_positive.add(conid)
        definition_by_conid[conid] = row
        if str(row.get("assetClass") or row.get("instrument_type") or row.get("secType") or row.get("sec_type") or "").upper() not in {"STK", "STOCK"}:
            rejected_reasons.add("not_stock")
            continue
        if str(row.get("currency") or "").upper() != "USD":
            rejected_reasons.add("not_usd")
            continue
        country_code = str(row.get("countryCode") or row.get("country_code") or "").upper()
        is_us = row.get("isUS") if "isUS" in row else row.get("is_us")
        if country_code != "US" or is_us is False:
            rejected_reasons.add("not_us_contract")
            continue
        if row.get("restricted") is True:
            rejected_reasons.add("broker_restricted")
            continue
        listing_exchange = str(row.get("listingExchange") or row.get("listing_exchange") or "").upper()
        if listing_exchange != expected_exchange:
            rejected_reasons.add("wrong_primary_exchange")
            continue
        broker_symbol = row.get("ticker") or row.get("symbol") or row.get("local_symbol")
        if normalize_equity_symbol(broker_symbol) != ticker_key:
            rejected_reasons.add("wrong_symbol_or_share_class")
            continue
        broker_name = row.get("name") or row.get("companyName") or row.get("company_name")
        if conid in seen_conids:
            continue
        seen_conids.add(conid)
        valid.append(row)

    if len(valid) != 1:
        if len(valid) > 1:
            reason = "multiple_matching_primary_contracts"
        elif not all_positive:
            reason = "no_ibkr_contract_candidate"
        elif rejected_reasons:
            reason = "+".join(sorted(rejected_reasons))
        else:
            reason = "no_matching_primary_contract"
        diagnostic = definition_by_conid[next(iter(all_positive))] if len(all_positive) == 1 else {}
        return IbkrContractResolution(
            status="blocked",
            reason=reason,
            expected_listing_exchange=expected_exchange,
            conid=None,
            candidate_conids=tuple(sorted(all_positive)),
            broker_symbol=str(diagnostic.get("ticker") or diagnostic.get("symbol") or diagnostic.get("local_symbol") or ""),
            broker_company_name=str(diagnostic.get("name") or diagnostic.get("companyName") or diagnostic.get("company_name") or ""),
            broker_listing_exchange=str(diagnostic.get("listingExchange") or diagnostic.get("listing_exchange") or ""),
            broker_currency=str(diagnostic.get("currency") or ""),
            company_name_match=(
                company_names_compatible(
                    massive_name,
                    diagnostic.get("name") or diagnostic.get("companyName") or diagnostic.get("company_name"),
                )
                if diagnostic
                else None
            ),
        )

    row = valid[0]
    conid = int(row.get("conid") or row.get("con_id"))
    return IbkrContractResolution(
        status="accepted",
        reason=(
            "unique_same_security_primary_contract"
            if company_names_compatible(
                massive_name,
                row.get("name") or row.get("companyName") or row.get("company_name"),
            )
            else "unique_primary_listing_company_name_differs"
        ),
        expected_listing_exchange=expected_exchange,
        conid=conid,
        candidate_conids=tuple(sorted(all_positive)),
        broker_symbol=str(row.get("ticker") or row.get("symbol") or row.get("local_symbol") or ""),
        broker_company_name=str(row.get("name") or row.get("companyName") or row.get("company_name") or ""),
        broker_listing_exchange=str(row.get("listingExchange") or row.get("listing_exchange") or ""),
        broker_currency=str(row.get("currency") or ""),
        company_name_match=company_names_compatible(
            massive_name,
            row.get("name") or row.get("companyName") or row.get("company_name"),
        ),
    )


def _normalized_company_name(value: Any) -> str:
    decoded = html.unescape(str(value or "")).upper().replace("&", " AND ")
    words = re.sub(r"[^A-Z0-9]+", " ", decoded).split()
    return " ".join(word for word in words if word not in _LEGAL_NAME_WORDS)
