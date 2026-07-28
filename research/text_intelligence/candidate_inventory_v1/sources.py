from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string

from .config import CandidateInventoryConfig
from .mining import SourceDocument


@dataclass(frozen=True, slots=True)
class WorkUnit:
    corpus: str
    key: str
    partition_value: int
    start_date: str = ""
    end_date_exclusive: str = ""


def work_units(config: CandidateInventoryConfig) -> list[WorkUnit]:
    units: list[WorkUnit] = []
    if "news" in config.sources:
        for start, end in month_windows(config.start_date, config.end_date_exclusive):
            units.append(
                WorkUnit(
                    corpus="news",
                    key=f"news-{start[:7]}",
                    partition_value=int(start[:4] + start[5:7]),
                    start_date=start,
                    end_date_exclusive=end,
                )
            )
    if "sec" in config.sources:
        units.extend(
            WorkUnit(corpus="sec", key=f"sec-lane-{lane:02d}", partition_value=lane)
            for lane in range(64)
        )
    return units


def initial_cursor(unit: WorkUnit) -> tuple[Any, ...]:
    if unit.corpus == "news":
        return (unit.start_date, "")
    return ("", "", "", "")


def fetch_page(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
    unit: WorkUnit,
    cursor: tuple[Any, ...],
) -> list[SourceDocument]:
    sql = (
        news_page_sql(config, unit, cursor)
        if unit.corpus == "news"
        else sec_page_sql(config, unit, cursor)
    )
    return [
        document_from_row(unit.corpus, json.loads(line))
        for line in client.execute(sql).splitlines()
        if line.strip()
    ]


def cursor_from_document(document: SourceDocument) -> tuple[Any, ...]:
    cursor = document.metadata.get("_cursor")
    if not isinstance(cursor, list):
        raise RuntimeError(f"source document {document.source_id} has no stable cursor")
    return tuple(cursor)


def news_page_sql(
    config: CandidateInventoryConfig,
    unit: WorkUnit,
    cursor: tuple[Any, ...],
) -> str:
    db = quote_ident(config.database)
    event = quote_ident(config.news_event_table)
    rendered = quote_ident(config.news_rendered_table)
    cursor_date, cursor_id = map(str, cursor)
    return f"""
SELECT
 e.canonical_news_id AS source_id,
 toString(e.published_at_utc) AS source_timestamp,
 e.title AS title,
 r.rendered_text AS text,
 e.tickers AS entity_terms,
 e.author AS author,
 e.url_domain AS source_domain,
 e.channels AS channels,
 e.provider_tags AS provider_tags,
 [toString(e.published_date), e.provider_article_id] AS _cursor
FROM {db}.{event} AS e FINAL
INNER JOIN {db}.{rendered} AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.published_at_utc >= toDateTime64({sql_string(unit.start_date)}, 9, 'UTC')
  AND e.published_at_utc < toDateTime64({sql_string(unit.end_date_exclusive)}, 9, 'UTC')
  AND tuple(e.published_date, e.provider_article_id)
      > tuple(toDate({sql_string(cursor_date)}), {sql_string(cursor_id)})
  AND notEmpty(r.rendered_text)
ORDER BY e.published_date, e.provider_article_id
LIMIT {int(config.news_page_size)}
FORMAT JSONEachRow
"""


def sec_page_sql(
    config: CandidateInventoryConfig,
    unit: WorkUnit,
    cursor: tuple[Any, ...],
) -> str:
    db = quote_ident(config.database)
    rendered = quote_ident(config.sec_rendered_table)
    document = quote_ident(config.sec_document_table)
    filing = quote_ident(config.sec_filing_table)
    cik, accession, document_id, text_kind = map(str, cursor)
    lane = int(unit.partition_value)
    return f"""
SELECT
 r.document_id AS source_id,
 toString(coalesce(f.accepted_at_utc, toDateTime64(r.source_archive_date, 9, 'UTC')))
   AS source_timestamp,
 concat(
   ifNull(d.document_type, ''), ' ',
   ifNull(d.description, ''), ' ',
   ifNull(d.document_name, '')
 ) AS title,
 r.text AS text,
 [r.cik, ifNull(f.company_name, '')] AS entity_terms,
 d.document_type AS document_type,
 d.document_role AS document_role,
 ifNull(d.description, '') AS description,
 r.text_kind AS text_kind,
 r.quality_flags AS quality_flags,
 [r.cik, r.accession_number, r.document_id, r.text_kind] AS _cursor
FROM
(
  SELECT *
  FROM {db}.{rendered} FINAL
  PREWHERE cityHash64(cik) % 64 = {lane}
  WHERE tuple(cik, accession_number, document_id, text_kind)
        > tuple(
          {sql_string(cik)},
          {sql_string(accession)},
          {sql_string(document_id)},
          {sql_string(text_kind)}
        )
    AND source_archive_date >= toDate({sql_string(config.start_date)})
    AND source_archive_date < toDate({sql_string(config.end_date_exclusive)})
    AND notEmpty(text)
  ORDER BY cik, accession_number, document_id, text_kind
  LIMIT {int(config.sec_page_size)}
) AS r
LEFT JOIN {db}.{document} AS d FINAL
 ON d.document_id=r.document_id
 AND d.cik=r.cik
 AND d.accession_number=r.accession_number
LEFT JOIN {db}.{filing} AS f FINAL
 ON f.filing_id=r.filing_id
 AND f.cik=r.cik
 AND f.accession_number=r.accession_number
ORDER BY r.cik, r.accession_number, r.document_id, r.text_kind
FORMAT JSONEachRow
"""


def document_from_row(corpus: str, row: dict[str, Any]) -> SourceDocument:
    known = {"source_id", "source_timestamp", "title", "text", "entity_terms"}
    return SourceDocument(
        corpus=corpus,
        source_id=str(row.get("source_id") or ""),
        timestamp=str(row.get("source_timestamp") or ""),
        title=str(row.get("title") or ""),
        text=str(row.get("text") or ""),
        entity_terms=tuple(str(value) for value in row.get("entity_terms") or [] if str(value)),
        metadata={key: value for key, value in row.items() if key not in known},
    )


def month_windows(start: str, end_exclusive: str) -> Iterable[tuple[str, str]]:
    current = date.fromisoformat(start).replace(day=1)
    end = date.fromisoformat(end_exclusive)
    while current < end:
        next_month = (
            current.replace(year=current.year + 1, month=1)
            if current.month == 12
            else current.replace(month=current.month + 1)
        )
        window_start = max(current, date.fromisoformat(start))
        window_end = min(next_month, end)
        if window_start < window_end:
            yield window_start.isoformat(), window_end.isoformat()
        current = next_month
