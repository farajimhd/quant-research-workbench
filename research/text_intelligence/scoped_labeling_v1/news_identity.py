from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident


ISSUER_RESOLUTION_VERSION = "news_issuer_passage_resolution_v6"
ISSUER_IDENTITY_AUTHORITY_VERSION = "news_issuer_identity_authority_v5"
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
ARTICLE_QUOTED_ALIAS_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9&'.,-]*(?:[ \t]+[A-Z][A-Za-z0-9&'.,-]*){0,8})"
    r"[ \t]*\([^)]*?\"(?P<alias>[A-Z][A-Za-z0-9&'. -]{2,60})\"[^)]*\)"
    r"[ \t]*\((?i:NASDAQ|NYSE|NYSEAMERICAN|NYSE[ \t]+AMERICAN|AMEX|"
    r"OTC(?:QX|QB)?|TSX|TSXV|CSE)[ \t]*[:\-][ \t]*"
    r"(?P<ticker>[A-Z][A-Z0-9.-]{0,9})",
)
ARTICLE_ALIAS_GROUP_RE = re.compile(
    r"(?P<name>[A-Z][A-Za-z0-9&'.,-]*(?:[ \t]+[A-Z][A-Za-z0-9&'.,-]*){0,8})"
    r"[ \t]*(?P<aliases>\([^)]*\"[^)]*\))"
    r"[ \t]*\((?i:NASDAQ|NYSE|NYSEAMERICAN|NYSE[ \t]+AMERICAN|AMEX|"
    r"OTC(?:QX|QB)?|TSX|TSXV|CSE)[ \t]*[:\-][ \t]*"
    r"(?P<ticker>[A-Z][A-Z0-9.-]{0,9})",
)
ANNOUNCED_TICKER_RE = re.compile(
    r"(?:\b(?:under\s+(?:the\s+)?ticker(?:\s+symbol)?|"
    r"(?:ticker(?:\s+symbol)?|symbol)\s+(?:(?:will|to)\s+be|under)))\s*[\"']?"
    r"(?P<ticker>[A-Z][A-Z0-9.-]{0,9})[\"']?\b|"
    r"\b(?:shares?|units?)\s+(?:will\s+|to\s+)?trade\s+on\s+"
    r"(?:NASDAQ|NYSE|AMEX)\s+under\s+[\"']?(?P<trade_ticker>[A-Z][A-Z0-9.-]{0,9})[\"']?\b",
    re.IGNORECASE,
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
    exchange_code: str = ""
    cik: str = ""
    entity_type: str = ""
    domicile_country_code: str = ""
    state_of_incorporation: str = ""
    sic_code: str = ""
    sic_description: str = ""
    sector: str = ""
    industry: str = ""
    website_url: str = ""
    investor_website_url: str = ""
    status: str = ""
    source_authority: str = ""
    # Short forms learned from an explicit full-name mention in the current
    # article only. They never enter the durable issuer authority.
    article_local_aliases: tuple[str, ...] = ()

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

    def __init__(
        self,
        identities: Iterable[IssuerIdentity],
        *,
        article_tickers: Iterable[str] = (),
    ) -> None:
        identity_rows = tuple(identities)
        ticker_entries: dict[str, list[IssuerIdentity]] = {}
        alias_entries: dict[str, list[IssuerIdentity]] = {}
        raw_alias_entries: dict[str, list[tuple[IssuerIdentity, str]]] = {}
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
                raw_alias_entries.setdefault(alias, []).append(
                    (identity, raw_alias)
                )
                max_alias_tokens = max(max_alias_tokens, len(alias.split()))
                leading = alias.split()[0] if alias else ""
                if (
                    len(alias.split()) >= 2
                    and len(leading) >= 6
                    and leading not in UNSAFE_SINGLE_TOKEN_ALIASES
                ):
                    alias_entries.setdefault(leading, []).append(identity)
                    raw_alias_entries.setdefault(leading, []).append(
                        (identity, raw_alias)
                    )
            for raw_alias in identity.article_local_aliases:
                alias = normalize_issuer_alias(raw_alias)
                if not is_safe_article_local_alias(alias):
                    continue
                alias_entries.setdefault(alias, []).append(identity)
                max_alias_tokens = max(max_alias_tokens, len(alias.split()))
        self._ticker_entries = {
            key: tuple(value) for key, value in ticker_entries.items()
        }
        self._alias_entries = {
            key: tuple(value) for key, value in alias_entries.items()
        }
        self._raw_alias_entries = {
            key: tuple(value) for key, value in raw_alias_entries.items()
        }
        self._max_alias_tokens = min(max_alias_tokens, 10)
        self._identities = identity_rows
        self._article_tickers = frozenset(
            value.upper().strip() for value in article_tickers if value
        )

    @property
    def identity_count(self) -> int:
        return len(self._identities)

    @property
    def ticker_count(self) -> int:
        return len(self._ticker_entries)

    def reference_snapshot(
        self,
        tickers: Sequence[str],
        *,
        timestamp: str = "",
    ) -> tuple[dict[str, object], ...]:
        """Return the in-memory point-in-time facts used for ticker resolution."""
        day = _timestamp_date(timestamp)
        rows: list[dict[str, object]] = []
        for ticker in dict.fromkeys(
            value.upper().strip() for value in tickers if value
        ):
            for identity in self._valid_ticker_entries(ticker, day):
                rows.append(
                    {
                        "ticker": identity.ticker,
                        "issuer_id": identity.issuer_id,
                        "aliases": identity.aliases,
                        "list_date": identity.list_date.isoformat()
                        if identity.list_date
                        else "",
                        "delisted_date": identity.delisted_date.isoformat()
                        if identity.delisted_date
                        else "",
                        "exchange_code": identity.exchange_code,
                        "cik": identity.cik,
                        "entity_type": identity.entity_type,
                        "domicile_country_code": identity.domicile_country_code,
                        "state_of_incorporation": identity.state_of_incorporation,
                        "sic_code": identity.sic_code,
                        "sic_description": identity.sic_description,
                        "sector": identity.sector,
                        "industry": identity.industry,
                        "website_url": identity.website_url,
                        "investor_website_url": identity.investor_website_url,
                        "status": identity.status,
                        "source_authority": identity.source_authority,
                    }
                )
        return tuple(rows)

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
                    exchange_code=str(raw.get("exchange_code") or ""),
                    cik=str(raw.get("cik") or ""),
                    entity_type=str(raw.get("entity_type") or ""),
                    domicile_country_code=str(
                        raw.get("domicile_country_code") or ""
                    ),
                    state_of_incorporation=str(
                        raw.get("state_of_incorporation") or ""
                    ),
                    sic_code=str(raw.get("sic_code") or ""),
                    sic_description=str(raw.get("sic_description") or ""),
                    sector=str(raw.get("sector") or ""),
                    industry=str(raw.get("industry") or ""),
                    website_url=str(raw.get("website_url") or ""),
                    investor_website_url=str(
                        raw.get("investor_website_url") or ""
                    ),
                    status=str(raw.get("status") or ""),
                    source_authority=str(raw.get("source_authority") or ""),
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
        linked = {
            value.upper().strip() for value in linked_tickers if value
        }
        evidence: dict[str, set[str]] = {}
        explicit = {
            match.group(1).upper()
            for match in EXCHANGE_TICKER_RE.finditer(text)
        }
        explicit.update(match.group(1).upper() for match in CASHTAG_RE.finditer(text))
        announced = {
            (match.group("ticker") or match.group("trade_ticker")).upper()
            for match in ANNOUNCED_TICKER_RE.finditer(text)
        }
        explicit.update(announced)
        for ticker in linked_tickers:
            normalized = ticker.upper().strip()
            if len(normalized) >= 2 and re.search(
                rf"(?<![A-Z0-9]){re.escape(normalized)}(?![A-Z0-9])",
                text,
            ):
                explicit.add(normalized)
        for ticker in explicit:
            entries = self._valid_ticker_entries(ticker, day)
            if (
                entries
                or ticker in {value.upper() for value in linked_tickers}
                or ticker in announced
            ):
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
                preferred = tickers & (
                    linked | self._article_tickers | explicit
                )
                if len(preferred) == 1:
                    ticker = next(iter(preferred))
                elif len(tickers) == 1:
                    ticker = next(iter(tickers))
                else:
                    continue
                # A free-standing single word is too weak to introduce an
                # issuer absent from provider links and exact
                # article-declared exchange pairs. This blocks collisions
                # such as Vertex/VERX and ordinary domain words such as
                # marijuana/MAJI without weakening explicit identities.
                if (
                    len(alias.split()) == 1
                    and ticker not in linked
                    and ticker not in self._article_tickers
                    and ticker not in explicit
                ):
                    continue
                evidence.setdefault(ticker, set()).add(
                    f"issuer_alias:{alias}"
                )
        return tuple(
            IssuerMatch(ticker=ticker, evidence=tuple(sorted(values)))
            for ticker, values in sorted(evidence.items())
        )

    def with_article_identities(self, text: str) -> "NewsIssuerResolver":
        """Add exact identities and conservative article-local short names.

        A short name is introduced only after the same article spells out an
        existing full issuer alias. This lets a later ``H&E`` resolve to the
        earlier ``H&E Equipment Services`` without promoting that ambiguous
        shorthand into the global identity authority.
        """
        local: list[IssuerIdentity] = []
        searchable = re.sub(
            r"(?im)^\s*(?:Title|Teaser|Summary|Body)\s*:\s*",
            "",
            text,
        )
        matched_aliases: dict[str, set[str]] = {}
        for match in self.resolve(searchable):
            for value in match.evidence:
                kind, separator, normalized = value.partition(":")
                if not separator or kind != "issuer_alias":
                    continue
                matched_aliases.setdefault(match.ticker, set()).add(normalized)
        for ticker, normalized_aliases in matched_aliases.items():
            for normalized in normalized_aliases:
                for identity, raw_alias in self._raw_alias_entries.get(
                    normalized, ()
                ):
                    if identity.ticker != ticker:
                        continue
                    local_aliases = _derived_article_short_aliases(raw_alias)
                    if not local_aliases:
                        continue
                    local.append(
                        IssuerIdentity(
                            ticker=identity.ticker,
                            issuer_id=identity.issuer_id,
                            aliases=(raw_alias,),
                            list_date=identity.list_date,
                            delisted_date=identity.delisted_date,
                            exchange_code=identity.exchange_code,
                            cik=identity.cik,
                            entity_type=identity.entity_type,
                            domicile_country_code=identity.domicile_country_code,
                            state_of_incorporation=identity.state_of_incorporation,
                            sic_code=identity.sic_code,
                            sic_description=identity.sic_description,
                            sector=identity.sector,
                            industry=identity.industry,
                            website_url=identity.website_url,
                            investor_website_url=identity.investor_website_url,
                            status=identity.status,
                            source_authority=identity.source_authority,
                            article_local_aliases=local_aliases,
                        )
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
        for match in ARTICLE_QUOTED_ALIAS_RE.finditer(searchable):
            name = match.group("name").strip(" ,")
            alias = match.group("alias").strip(" ,")
            ticker = match.group("ticker").upper()
            aliases = tuple(
                value
                for value in (name, alias)
                if is_safe_alias(normalize_issuer_alias(value), ticker)
            )
            if aliases:
                local.append(
                    IssuerIdentity(
                        ticker=ticker,
                        issuer_id=f"article:{ticker}",
                        aliases=aliases,
                    )
                )
        for match in ARTICLE_ALIAS_GROUP_RE.finditer(searchable):
            ticker = match.group("ticker").upper()
            values = (
                match.group("name").strip(" ,"),
                *re.findall(r'"([^"]+)"', match.group("aliases")),
            )
            aliases = tuple(
                value
                for value in values
                if is_safe_alias(normalize_issuer_alias(value), ticker)
            )
            if aliases:
                local.append(
                    IssuerIdentity(
                        ticker=ticker,
                        issuer_id=f"article:{ticker}",
                        aliases=aliases,
                    )
                )
        # A registration/IPO article may announce a symbol before the listing
        # exists in the point-in-time identity graph.  This creates an
        # article-local identity for semantic attribution only; valid_on and
        # forecast eligibility still use the durable listing authority.
        for match in ANNOUNCED_TICKER_RE.finditer(searchable):
            ticker = (match.group("ticker") or match.group("trade_ticker")).upper()
            prefix = searchable[max(0, match.start() - 180):match.start()]
            names = tuple(
                value.strip(" ,.")
                for value in re.findall(
                    r"([A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*){0,7}"
                    r"\s+(?:Inc\.?|Corp\.?|Corporation|Ltd\.?|Limited|plc|Company))",
                    prefix,
                )
            )
            local.append(
                IssuerIdentity(
                    ticker=ticker,
                    issuer_id=f"article:announced:{ticker}",
                    aliases=names or (ticker,),
                )
            )
        if not local:
            return self
        return self._with_local_identities(local)

    def _with_local_identities(
        self,
        local: Sequence[IssuerIdentity],
    ) -> "NewsIssuerResolver":
        """Clone only touched index buckets instead of rebuilding the authority."""
        resolver = object.__new__(NewsIssuerResolver)
        ticker_entries = dict(self._ticker_entries)
        alias_entries = dict(self._alias_entries)
        raw_alias_entries = dict(self._raw_alias_entries)
        max_alias_tokens = self._max_alias_tokens
        for identity in local:
            ticker = identity.ticker.upper().strip()
            if not ticker:
                continue
            ticker_entries[ticker] = (*ticker_entries.get(ticker, ()), identity)
            for raw_alias in identity.aliases:
                alias = normalize_issuer_alias(raw_alias)
                if not is_safe_alias(alias, ticker):
                    continue
                alias_entries[alias] = (*alias_entries.get(alias, ()), identity)
                raw_alias_entries[alias] = (
                    *raw_alias_entries.get(alias, ()),
                    (identity, raw_alias),
                )
                max_alias_tokens = max(max_alias_tokens, len(alias.split()))
            for raw_alias in identity.article_local_aliases:
                alias = normalize_issuer_alias(raw_alias)
                if not is_safe_article_local_alias(alias):
                    continue
                alias_entries[alias] = (*alias_entries.get(alias, ()), identity)
                max_alias_tokens = max(max_alias_tokens, len(alias.split()))
        resolver._ticker_entries = ticker_entries
        resolver._alias_entries = alias_entries
        resolver._raw_alias_entries = raw_alias_entries
        resolver._max_alias_tokens = min(max_alias_tokens, 10)
        resolver._identities = (*self._identities, *local)
        resolver._article_tickers = frozenset((
            *self._article_tickers,
            *(identity.ticker for identity in local),
        ))
        return resolver

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
    canonical_rows = _json_rows(client.execute(f"""
SELECT
 upperUTF8(sym.ticker_normalized) AS ticker,
 sec.issuer_id AS issuer_id,
 issuer.issuer_name AS issuer_name,
 ifNull(issuer.legal_name, '') AS legal_name,
 ifNull(issuer.branding_name, '') AS branding_name,
 sec.security_name AS security_name,
 sym.display_name AS display_name,
 listing.exchange_code AS exchange_code,
 toString(listing.list_date) AS list_date,
 toString(listing.delisted_date) AS delisted_date,
 ifNull(identifier.cik, '') AS cik,
 ifNull(issuer.entity_type, '') AS entity_type,
 ifNull(issuer.domicile_country_code, '') AS domicile_country_code,
 ifNull(issuer.state_of_incorporation, '') AS state_of_incorporation,
 ifNull(issuer.sic_code, '') AS sic_code,
 ifNull(issuer.sic_description, '') AS sic_description,
 ifNull(issuer.sector, '') AS sector,
 ifNull(issuer.industry, '') AS industry,
 ifNull(issuer.website_url, '') AS website_url,
 ifNull(issuer.investor_website_url, '') AS investor_website_url,
 issuer.status AS status,
 'canonical_identity_graph' AS source_authority
FROM {db}.id_symbol_v1 AS sym FINAL
INNER JOIN {db}.id_listing_v1 AS listing FINAL
 ON listing.listing_id=sym.listing_id
INNER JOIN {db}.id_security_v1 AS sec FINAL
 ON sec.security_id=listing.security_id
INNER JOIN {db}.id_issuer_v1 AS issuer FINAL
 ON issuer.issuer_id=sec.issuer_id
LEFT JOIN
(
 SELECT
  issuer_id,
  argMax(identifier_value_normalized, inserted_at) AS cik
 FROM {db}.id_issuer_identifier_v1 FINAL
 WHERE identifier_kind='cik'
 GROUP BY issuer_id
) AS identifier ON identifier.issuer_id=issuer.issuer_id
WHERE sym.ticker_normalized != ''
  AND sec.issuer_id != ''
  AND listing.currency_code='USD'
  AND sym.asset_type IN ('stock', 'fund', 'otc')
FORMAT JSONEachRow
"""))
    fallback_rows = _json_rows(client.execute(f"""
SELECT
 upperUTF8(entity.current_ticker) AS ticker,
 coalesce(nullIf(identifier.issuer_id, ''), concat('issuer:cik:', entity.cik)) AS issuer_id,
 coalesce(nullIf(issuer.issuer_name, ''), entity.entity_name) AS issuer_name,
 ifNull(issuer.legal_name, '') AS legal_name,
 ifNull(issuer.branding_name, '') AS branding_name,
 entity.entity_name AS security_name,
 entity.entity_name AS display_name,
 ifNull(entity.primary_exchange, '') AS exchange_code,
 '' AS list_date,
 substring(JSONExtractString(entity.source_payload_json, 'delisted_utc'), 1, 10) AS delisted_date,
 entity.cik AS cik,
 ifNull(issuer.entity_type, '') AS entity_type,
 ifNull(issuer.domicile_country_code, '') AS domicile_country_code,
 ifNull(issuer.state_of_incorporation, '') AS state_of_incorporation,
 ifNull(issuer.sic_code, '') AS sic_code,
 ifNull(issuer.sic_description, '') AS sic_description,
 ifNull(issuer.sector, '') AS sector,
 ifNull(issuer.industry, '') AS industry,
 ifNull(issuer.website_url, '') AS website_url,
 ifNull(issuer.investor_website_url, '') AS investor_website_url,
 if(entity.active=1, 'active', 'inactive') AS status,
 'market_ticker_event_entity' AS source_authority
FROM {db}.market_ticker_event_entity_v1 AS entity FINAL
LEFT JOIN
(
 SELECT
  identifier_value_normalized AS cik,
  argMax(issuer_id, inserted_at) AS issuer_id
 FROM {db}.id_issuer_identifier_v1 FINAL
 WHERE identifier_kind='cik'
 GROUP BY identifier_value_normalized
) AS identifier ON identifier.cik=entity.cik
LEFT JOIN {db}.id_issuer_v1 AS issuer FINAL
 ON issuer.issuer_id=identifier.issuer_id
WHERE entity.current_ticker != ''
  AND entity.cik != ''
  AND lowerUTF8(ifNull(entity.currency_name, ''))='usd'
  AND lowerUTF8(JSONExtractString(entity.source_payload_json, 'locale'))='us'
FORMAT JSONEachRow
"""))
    identities = _identity_rows(canonical_rows, fallback_rows)
    if not identities:
        raise RuntimeError(
            "News issuer-resolution preflight returned no reference identities."
        )
    return NewsIssuerResolver(identities)


def _identity_rows(
    canonical_rows: Sequence[Mapping[str, object]],
    fallback_rows: Sequence[Mapping[str, object]],
) -> list[IssuerIdentity]:
    """Build one complete identity table without weakening dated canonical rows."""
    rows = list(canonical_rows)
    canonical_keys = {
        (str(row.get("ticker") or "").upper(), str(row.get("issuer_id") or ""))
        for row in canonical_rows
    }
    rows.extend(
        row
        for row in fallback_rows
        if (
            str(row.get("ticker") or "").upper(),
            str(row.get("issuer_id") or ""),
        )
        not in canonical_keys
    )
    identities: list[IssuerIdentity] = []
    seen: set[tuple[str, str, dt.date | None, dt.date | None, str]] = set()
    for row in rows:
        ticker = str(row.get("ticker") or "").upper().strip()
        issuer_id = str(row.get("issuer_id") or "").strip()
        list_date = _date_or_none(row.get("list_date"))
        delisted_date = _date_or_none(row.get("delisted_date"))
        exchange_code = str(row.get("exchange_code") or "").strip().upper()
        key = (ticker, issuer_id, list_date, delisted_date, exchange_code)
        if not ticker or not issuer_id or key in seen:
            continue
        seen.add(key)
        aliases = tuple(
            dict.fromkeys(
                str(row.get(name) or "").strip()
                for name in (
                    "issuer_name",
                    "legal_name",
                    "branding_name",
                    "security_name",
                    "display_name",
                )
                if str(row.get(name) or "").strip()
            )
        )
        if not aliases:
            continue
        identities.append(
            IssuerIdentity(
                ticker=ticker,
                issuer_id=issuer_id,
                aliases=aliases,
                list_date=list_date,
                delisted_date=delisted_date,
                exchange_code=exchange_code,
                cik=str(row.get("cik") or ""),
                entity_type=str(row.get("entity_type") or ""),
                domicile_country_code=str(
                    row.get("domicile_country_code") or ""
                ),
                state_of_incorporation=str(
                    row.get("state_of_incorporation") or ""
                ),
                sic_code=str(row.get("sic_code") or ""),
                sic_description=str(row.get("sic_description") or ""),
                sector=str(row.get("sector") or ""),
                industry=str(row.get("industry") or ""),
                website_url=str(row.get("website_url") or ""),
                investor_website_url=str(
                    row.get("investor_website_url") or ""
                ),
                status=str(row.get("status") or ""),
                source_authority=str(row.get("source_authority") or ""),
            )
        )
    return identities


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


def is_safe_article_local_alias(alias: str) -> bool:
    """Accept compact local aliases only when their structure is distinctive."""
    tokens = alias.split()
    return (
        len(tokens) >= 2
        and len(alias) >= 3
        and any(len(token) == 1 for token in tokens)
        and all(token.isalnum() for token in tokens)
    )


def _derived_article_short_aliases(alias: str) -> tuple[str, ...]:
    """Derive ampersand short names such as ``H&E`` from a full issuer name."""
    match = re.match(
        r"\s*([A-Za-z0-9]+(?:\s*&\s*[A-Za-z0-9]+)+)(?=\s|,|$)",
        alias,
    )
    if match is None:
        return ()
    short = match.group(1).strip()
    return (
        (short,)
        if is_safe_article_local_alias(normalize_issuer_alias(short))
        else ()
    )


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
