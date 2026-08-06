from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping

from research.mlops.clickhouse import ClickHouseHttpClient, insert_json_each_row

from .contracts import canonical_json
from .engine import ENGINE_VERSION, IssuerIdentity, IssuerIdentityIndex


SYNTHESIS_TABLE = "news_synthesis_v1"
STATUS_TABLE = "news_synthesis_build_status_v1"
LIVE_SEMANTIC_TABLE = "news_live_semantic_v3"


def create_tables(client: ClickHouseHttpClient, database: str) -> None:
    client.execute(f"""CREATE TABLE IF NOT EXISTS `{database}`.`{SYNTHESIS_TABLE}` (
canonical_news_id String,
published_at_utc DateTime64(9,'UTC'),
source_text_sha256 FixedString(64),
source_revision String,
contract_version LowCardinality(String),
engine_version LowCardinality(String),
document_structure LowCardinality(String),
communication_purpose LowCardinality(String),
information_origin LowCardinality(String),
production_method LowCardinality(String),
text_availability LowCardinality(String),
tickers Array(LowCardinality(String)),
sentiments Array(LowCardinality(String)),
concepts Array(LowCardinality(String)),
forecast_tickers Array(LowCardinality(String)),
reaction_tickers Array(LowCardinality(String)),
history_tickers Array(LowCardinality(String)),
quality_flags Array(LowCardinality(String)),
synthesis_json String,
updated_at_utc DateTime64(6,'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_at_utc,canonical_news_id,engine_version)""")
    client.execute(f"""CREATE TABLE IF NOT EXISTS `{database}`.`{STATUS_TABLE}` (
published_date Date, source_rows UInt64, completed_rows UInt64, failed_rows UInt64,
source_revision FixedString(64), engine_version LowCardinality(String), status LowCardinality(String), error String,
updated_at_utc DateTime64(6,'UTC')
) ENGINE=ReplacingMergeTree(updated_at_utc)
ORDER BY (published_date,engine_version)""")
    client.execute(f"ALTER TABLE `{database}`.`{STATUS_TABLE}` ADD COLUMN IF NOT EXISTS source_revision FixedString(64) AFTER failed_rows")


def persistence_row(document: Mapping[str, Any]) -> dict[str, Any]:
    entity_by_id = {str(row["entity_id"]): row for row in document["entities"]}
    eligibility = {(str(row["entity_id"]), str(row["product"])): bool(row["eligible"]) for row in document["eligibility"]}
    views = list(document["issuer_views"])
    tickers = [str(entity_by_id[str(view["entity_id"])].get("ticker") or "") for view in views]
    concepts = sorted({str(row["concept_leaf"]) for row in document["statements"]})
    def eligible(product: str) -> list[str]:
        return [ticker for ticker, view in zip(tickers, views) if ticker and eligibility.get((str(view["entity_id"]), product), False)]
    return {
        "canonical_news_id": document["source_id"], "published_at_utc": document["source_timestamp"],
        "source_text_sha256": document["source_text_sha256"], "source_revision": document["production"]["source_revision"],
        "contract_version": document["contract_version"], "engine_version": document["production"]["engine_version"],
        "document_structure": document["envelope"]["document_structure"]["value"],
        "communication_purpose": document["envelope"]["communication_purpose"]["value"],
        "information_origin": document["envelope"]["information_origin"]["value"],
        "production_method": document["envelope"]["production_method"]["value"],
        "text_availability": document["envelope"]["text_availability"]["value"],
        "tickers": tickers, "sentiments": [str(row["composite_sentiment"]) for row in views], "concepts": concepts,
        "forecast_tickers": eligible("forecast_trigger"), "reaction_tickers": eligible("reaction_study"), "history_tickers": eligible("issuer_history"),
        "quality_flags": list(document["quality_flags"]), "synthesis_json": canonical_json(document),
        "updated_at_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"),
    }


def persist_documents(client: ClickHouseHttpClient, database: str, documents: Iterable[Mapping[str, Any]]) -> int:
    rows = [persistence_row(document) for document in documents]
    if rows:
        insert_json_each_row(client, database, SYNTHESIS_TABLE, list(rows[0]), rows)
    return len(rows)


def load_identity_index(client: ClickHouseHttpClient, database: str) -> IssuerIdentityIndex:
    rows = list(client.iter_json_each_row(f"""
SELECT upperUTF8(sym.ticker_normalized) ticker, sec.issuer_id,sec.security_id,
coalesce(nullIf(issuer.branding_name,''),nullIf(issuer.issuer_name,''),nullIf(issuer.legal_name,''),sym.display_name) display_name,
arrayFilter(value -> notEmpty(value),[
  ifNull(issuer.issuer_name,''),ifNull(issuer.legal_name,''),
  ifNull(issuer.branding_name,''),ifNull(sec.security_name,''),ifNull(sym.display_name,'')
]) aliases,
listing.exchange_code,toString(listing.list_date) list_date,toString(listing.delisted_date) delisted_date
FROM `{database}`.`id_symbol_v1` sym FINAL
INNER JOIN `{database}`.`id_listing_v1` listing FINAL ON listing.listing_id=sym.listing_id
INNER JOIN `{database}`.`id_security_v1` sec FINAL ON sec.security_id=listing.security_id
INNER JOIN `{database}`.`id_issuer_v1` issuer FINAL ON issuer.issuer_id=sec.issuer_id
WHERE sym.ticker_normalized!='' AND sec.issuer_id!='' AND listing.currency_code='USD'
FORMAT JSONEachRow"""))
    identities = []
    for row in rows:
        aliases = tuple(str(value).strip() for value in row.get("aliases") or () if str(value).strip())
        if aliases:
            identities.append(IssuerIdentity(ticker=str(row["ticker"]), issuer_id=str(row["issuer_id"]), display_name=str(row["display_name"]), aliases=aliases, security_id=str(row.get("security_id") or ""), exchange_code=str(row.get("exchange_code") or ""), list_date=_date(row.get("list_date")), delisted_date=_date(row.get("delisted_date"))))
    if not identities:
        raise RuntimeError("News Synthesis identity preflight returned no canonical identities")
    return IssuerIdentityIndex(identities)


def write_status(client: ClickHouseHttpClient, database: str, *, published_date: str, source_rows: int, completed_rows: int, failed_rows: int, source_revision: str, status: str, error: str = "") -> None:
    row = {"published_date": published_date, "source_rows": source_rows, "completed_rows": completed_rows, "failed_rows": failed_rows, "source_revision": source_revision, "engine_version": ENGINE_VERSION, "status": status, "error": error[:1000], "updated_at_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")}
    insert_json_each_row(client, database, STATUS_TABLE, list(row), [row])


def _date(value: Any) -> date | None:
    clean = str(value or "")[:10]
    try: return date.fromisoformat(clean) if clean and clean != "0000-00-00" else None
    except ValueError: return None
