from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident


ISSUER_RESOLUTION_VERSION = "news_issuer_passage_resolution_v2"
EXCHANGE_TICKER_RE = re.compile(
    r"\b(?:NASDAQ|NYSE|NYSEAMERICAN|NYSE\s+AMERICAN|AMEX|OTC(?:QX|QB)?|"
    r"TSX|TSXV|CSE)\s*[:\-]\s*([A-Z][A-Z0-9.-]{0,9})\b",
    re.IGNORECASE,
)
CASHTAG_RE = re.compile(r"(?<![A-Z0-9])\$([A-Z][A-Z0-9.-]{0,9})\b")
ARTICLE_ISSUER_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9&'.,-]*(?:[ \t]+[A-Z][A-Za-z0-9&'.,-]*){0,8})"
    r"[ \t]*\((?i:NASDAQ|NYSE|NYSEAMERICAN|NYSE[ \t]+AMERICAN|AMEX|"
    r"OTC(?:QX|QB)?|TSX|TSXV|CSE)[ \t]*[:\-][ \t]*"
    r"(?P<ticker>[A-Z][A-Z0-9.-]{0,9})"
    r"(?:[ \t]*,[ \t]*[A-Z][A-Z0-9.-]{0,9})*\)+",
)
WORD_RE = re.compile(r"[A-Za-z0-9]+")
CORPORATE_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "ltd",
    "plc",
}
UNSAFE_SINGLE_TOKEN_ALIASES = {
    "american",
    "capital",
    "first",
    "general",
    "global",
    "group",
    "international",
    "national",
    "new",
    "one",
    "united",
}


@dataclass(frozen=True, slots=True)
class IssuerIdentity:
    ticker: str
    issuer_id: str
    aliases: tuple[str, ...]
    list_date: dt.date | None = None
    delisted_date: dt.date | None = None

    def valid_on(self, day: dt.date | None) -> bool:
        if day is None:
            return True
        if self.list_date is not None and day < self.list_date:
            return False
        if self.delisted_date is not None and day > self.delisted_date:
            return False
        return True


@dataclass(frozen=True, slots=True)
class IssuerMatch:
    ticker: str
    evidence: tuple[str, ...]


class NewsIssuerResolver:
    """Resolve article text to point-in-time issuer tickers.

    Provider ticker links are candidates, not semantic ownership. Resolution
    requires a symbol or an unambiguous issuer alias in the actual passage.
    """

    def __init__(self, identities: Iterable[IssuerIdentity]) -> None:
        identity_rows = tuple(identities)
        ticker_entries: dict[str, list[IssuerIdentity]] = {}
        alias_entries: dict[str, list[IssuerIdentity]] = {}
        max_alias_tokens = 1
        for identity in identity_rows:
            ticker = identity.ticker.upper().strip()
            if not ticker:
                continue
            ticker_entries.setdefault(ticker, []).append(identity)
            for raw_alias in identity.aliases:
                alias = normalize_issuer_alias(raw_alias)
                if not is_safe_alias(alias, ticker):
                    continue
                alias_entries.setdefault(alias, []).append(identity)
                max_alias_tokens = max(max_alias_tokens, len(alias.split()))
        self._ticker_entries = {
            key: tuple(value) for key, value in ticker_entries.items()
        }
        self._alias_entries = {
            key: tuple(value) for key, value in alias_entries.items()
        }
        self._max_alias_tokens = min(max_alias_tokens, 10)
        self._identities = identity_rows

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, object]) -> "NewsIssuerResolver":
        identities: list[IssuerIdentity] = []
        for raw in metadata.get("issuer_identities") or ():
            if not isinstance(raw, Mapping):
                continue
            identities.append(
                IssuerIdentity(
                    ticker=str(raw.get("ticker") or "").upper(),
                    issuer_id=str(raw.get("issuer_id") or ""),
                    aliases=tuple(
                        str(value)
                        for value in raw.get("aliases") or ()
                        if value
                    ),
                    list_date=_date_or_none(raw.get("list_date")),
                    delisted_date=_date_or_none(raw.get("delisted_date")),
                )
            )
        return cls(identities)

    def resolve(
        self,
        text: str,
        *,
        timestamp: str = "",
        linked_tickers: Sequence[str] = (),
    ) -> tuple[IssuerMatch, ...]:
        day = _timestamp_date(timestamp)
        evidence: dict[str, set[str]] = {}
        explicit = {
            match.group(1).upper()
            for match in EXCHANGE_TICKER_RE.finditer(text)
        }
        explicit.update(match.group(1).upper() for match in CASHTAG_RE.finditer(text))
        for ticker in linked_tickers:
            normalized = ticker.upper().strip()
            if normalized and re.search(
                rf"(?<![A-Z0-9]){re.escape(normalized)}(?![A-Z0-9])",
                text,
            ):
                explicit.add(normalized)
        for ticker in explicit:
            entries = self._valid_ticker_entries(ticker, day)
            if entries or ticker in {value.upper() for value in linked_tickers}:
                evidence.setdefault(ticker, set()).add(f"symbol:{ticker}")

        words = [value.casefold() for value in WORD_RE.findall(text)]
        for start in range(len(words)):
            for width in range(1, min(self._max_alias_tokens, len(words) - start) + 1):
                alias = " ".join(words[start : start + width])
                entries = tuple(
                    entry
                    for entry in self._alias_entries.get(alias, ())
                    if entry.valid_on(day)
                )
                tickers = {entry.ticker for entry in entries}
                if len(tickers) != 1:
                    continue
                ticker = next(iter(tickers))
                evidence.setdefault(ticker, set()).add(f"issuer_alias:{alias}")
        return tuple(
            IssuerMatch(ticker=ticker, evidence=tuple(sorted(values)))
            for ticker, values in sorted(evidence.items())
        )

    def with_article_identities(self, text: str) -> "NewsIssuerResolver":
        """Add exact issuer-name/symbol pairs stated by the article itself."""
        local: list[IssuerIdentity] = []
        searchable = re.sub(
            r"(?im)^\s*(?:Title|Teaser|Summary|Body)\s*:\s*",
            "",
            text,
        )
        for match in ARTICLE_ISSUER_RE.finditer(searchable):
            name = match.group("name").strip(" ,")
            ticker = match.group("ticker").upper()
            if not is_safe_alias(normalize_issuer_alias(name), ticker):
                continue
            local.append(
                IssuerIdentity(
                    ticker=ticker,
                    issuer_id=f"article:{ticker}",
                    aliases=(name,),
                )
            )
        if not local:
            return self
        return NewsIssuerResolver((*self._identities, *local))

    def issuer_group_count(
        self,
        tickers: Sequence[str],
        *,
        timestamp: str = "",
    ) -> int:
        """Count point-in-time issuers without equating issuer and symbol."""
        day = _timestamp_date(timestamp)
        groups: list[set[str]] = []
        for ticker in dict.fromkeys(
            value.upper().strip() for value in tickers if value
        ):
            keys = {
                entry.issuer_id
                for entry in self._valid_ticker_entries(ticker, day)
                if entry.issuer_id
            } or {f"unresolved-symbol:{ticker}"}
            overlaps = [
                index for index, group in enumerate(groups) if group & keys
            ]
            if not overlaps:
                groups.append(set(keys))
                continue
            merged = set(keys)
            for index in reversed(overlaps):
                merged.update(groups.pop(index))
            groups.append(merged)
        return len(groups)

    def one_provider_linked_issuer(
        self,
        subject_ticker: str,
        linked_tickers: Sequence[str],
        *,
        timestamp: str = "",
    ) -> bool:
        linked = tuple(value for value in linked_tickers if value)
        return bool(linked) and self.issuer_group_count(
            (subject_ticker, *linked),
            timestamp=timestamp,
        ) == 1

    def _valid_ticker_entries(
        self,
        ticker: str,
        day: dt.date | None,
    ) -> tuple[IssuerIdentity, ...]:
        return tuple(
            entry
            for entry in self._ticker_entries.get(ticker, ())
            if entry.valid_on(day)
        )


def load_news_issuer_resolver(
    client: ClickHouseHttpClient,
    database: str = "q_live",
) -> NewsIssuerResolver:
    """Load the reference identity authority once for a bounded pipeline run."""
    db = quote_ident(database)
    rows = _json_rows(client.execute(f"""
SELECT
 upperUTF8(sym.ticker_normalized) AS ticker,
 sec.issuer_id AS issuer_id,
 issuer.issuer_name AS issuer_name,
 ifNull(issuer.legal_name, '') AS legal_name,
 ifNull(issuer.branding_name, '') AS branding_name,
 sec.security_name AS security_name,
 sym.display_name AS display_name,
 toString(listing.list_date) AS list_date,
 toString(listing.delisted_date) AS delisted_date
FROM {db}.id_symbol_v1 AS sym FINAL
INNER JOIN {db}.id_listing_v1 AS listing FINAL
 ON listing.listing_id=sym.listing_id
INNER JOIN {db}.id_security_v1 AS sec FINAL
 ON sec.security_id=listing.security_id
INNER JOIN {db}.id_issuer_v1 AS issuer FINAL
 ON issuer.issuer_id=sec.issuer_id
WHERE sym.ticker_normalized != ''
  AND sec.issuer_id != ''
  AND listing.currency_code='USD'
  AND sym.asset_type IN ('stock', 'fund', 'otc')
FORMAT JSONEachRow
"""))
    identities = [
        IssuerIdentity(
            ticker=str(row["ticker"]).upper(),
            issuer_id=str(row["issuer_id"]),
            aliases=tuple(dict.fromkeys(
                str(row.get(name) or "").strip()
                for name in (
                    "issuer_name",
                    "legal_name",
                    "branding_name",
                    "security_name",
                    "display_name",
                )
                if str(row.get(name) or "").strip()
            )),
            list_date=_date_or_none(row.get("list_date")),
            delisted_date=_date_or_none(row.get("delisted_date")),
        )
        for row in rows
    ]
    if not identities:
        raise RuntimeError(
            "News issuer-resolution preflight returned no reference identities."
        )
    return NewsIssuerResolver(identities)


def normalize_issuer_alias(value: str) -> str:
    tokens = [token.casefold() for token in WORD_RE.findall(str(value or ""))]
    while tokens and tokens[-1] in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def is_safe_alias(alias: str, ticker: str) -> bool:
    if not alias or alias == ticker.casefold():
        return False
    tokens = alias.split()
    if len(tokens) == 1:
        return len(alias) >= 5 and alias not in UNSAFE_SINGLE_TOKEN_ALIASES
    return len(alias) >= 5


def _timestamp_date(value: str) -> dt.date | None:
    clean = str(value or "").replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(clean).date()
    except ValueError:
        return _date_or_none(clean[:10])


def _date_or_none(value: object) -> dt.date | None:
    clean = str(value or "").strip()
    if not clean or clean in {"0000-00-00", "None"}:
        return None
    try:
        return dt.date.fromisoformat(clean[:10])
    except ValueError:
        return None


def _json_rows(text: str) -> list[dict]:
    import json

    return [
        json.loads(line)
        for line in text.splitlines()
        if line.strip()
    ]
