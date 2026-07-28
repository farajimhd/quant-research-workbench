from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Iterable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.paths import MLOpsPathConfig
from research.text_intelligence.classification_authority_v2.evaluation import (
    attach_sec_tickers,
    json_rows,
)
from research.text_intelligence.candidate_inventory_v1.config import (
    CandidateInventoryConfig,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .pipeline import classify_news_document, classify_sec_document
from .news_identity import NewsIssuerResolver, load_news_issuer_resolver
from .schema import SCOPED_LABELING_VERSION, ScopedLabel


TARGET_TABLE = "scoped_text_labels_v4"
STATUS_TABLE = "scoped_text_labels_v4_build_status"
RELATION_TABLE = "scoped_content_relations_v2"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    certification_manifest = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "scoped_labeling_v4"
        / "certification"
        / "manifest.json"
    )
    parser = argparse.ArgumentParser(
        description=(
            "Build versioned scoped News/SEC labels. Defaults to a read-only "
            "plan; --execute is required to create or insert anything."
        )
    )
    parser.add_argument("--start-date", default="2010-01-01")
    parser.add_argument(
        "--end-date-exclusive",
        default=(dt.date.today() + dt.timedelta(days=1)).isoformat(),
    )
    parser.add_argument(
        "--corpus", choices=("news", "sec", "both"), default="both"
    )
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--period-days",
        type=int,
        default=7,
        help="Bounded source window owned by one worker (1-31 days).",
    )
    parser.add_argument("--database", default="q_live")
    parser.add_argument(
        "--certification-manifest",
        type=Path,
        default=certification_manifest,
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--rebuild-completed", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def run(args: argparse.Namespace) -> dict:
    _validate_dates(args.start_date, args.end_date_exclusive)
    if not 1 <= args.workers <= 32:
        raise ValueError("--workers must be between 1 and 32")
    if not 1 <= args.period_days <= 31:
        raise ValueError("--period-days must be between 1 and 31")
    load_env_files(discover_env_files(Path.cwd()), verbose=True)
    corpora = ("news", "sec") if args.corpus == "both" else (args.corpus,)
    periods = bounded_period_ranges(
        args.start_date,
        args.end_date_exclusive,
        args.period_days,
    )
    plan = [(corpus, start, end) for corpus in corpora for start, end in periods]
    client = make_client()
    try:
        counts = source_counts(client, args.database, plan)
        print(
            f"SCOPED LABELING PLAN | version={SCOPED_LABELING_VERSION} "
            f"units={len(plan):,} workers={args.workers} execute={args.execute}",
            flush=True,
        )
        for corpus in corpora:
            print(
                f"  {corpus}: source rows={sum(value for (kind, _, _), value in counts.items() if kind == corpus):,}",
                flush=True,
            )
        if not args.execute:
            print("PLAN ONLY | no tables created and no rows written", flush=True)
            return {"execute": False, "source_counts": counts}
        assert_certification(args.certification_manifest)
        create_tables(client, args.database)
        issuer_resolver = (
            load_news_issuer_resolver(client, args.database)
            if "news" in corpora
            else None
        )
        completed = (
            set()
            if args.rebuild_completed
            else completed_units(client, args.database)
        )
    finally:
        client.close()

    run_id = uuid.uuid4().hex
    active = [
        item for item in plan
        if (item[0], item[1], item[2], SCOPED_LABELING_VERSION)
        not in completed
    ]
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=args.workers,
        thread_name_prefix="scoped-label",
    ) as executor:
        future_map = {
            executor.submit(
                process_unit,
                args.database,
                corpus,
                start,
                end,
                run_id,
                issuer_resolver,
            ): (corpus, start, end)
            for corpus, start, end in active
        }
        for index, future in enumerate(
            concurrent.futures.as_completed(future_map), start=1
        ):
            corpus, start, _ = future_map[future]
            result = future.result()
            results.append(result)
            print(
                f"[{index:,}/{len(active):,}] {corpus} {start} "
                f"source={result['source_rows']:,} "
                f"labels={result['label_rows']:,} "
                f"relations={result['relation_rows']:,}",
                flush=True,
            )
    return {
        "execute": True,
        "run_id": run_id,
        "completed_units": len(results),
        "label_rows": sum(row["label_rows"] for row in results),
        "relation_rows": sum(row["relation_rows"] for row in results),
    }


def process_unit(
    database: str,
    corpus: str,
    start: str,
    end: str,
    run_id: str,
    issuer_resolver: NewsIssuerResolver | None,
) -> dict:
    client = make_client()
    try:
        rows = (
            fetch_news_period(client, database, start, end)
            if corpus == "news"
            else fetch_sec_period(client, database, start, end)
        )
        label_rows: list[dict] = []
        relation_rows: list[dict] = []
        label_count = 0
        relation_count = 0
        for row in rows:
            document = row_to_document(row, corpus)
            labels = (
                classify_news_document(
                    document,
                    issuer_resolver=issuer_resolver,
                )
                if corpus == "news"
                else classify_sec_document(document)
            )
            label_rows.extend(
                persistence_row(document, label, run_id) for label in labels
            )
            for label in labels:
                relations = relationship_rows(document, label, run_id)
                relation_rows.extend(relations)
                relation_count += len(relations)
            label_count += len(labels)
            if len(label_rows) >= 1000:
                insert_rows(client, database, TARGET_TABLE, label_rows)
                label_rows.clear()
            if len(relation_rows) >= 4000:
                insert_rows(client, database, RELATION_TABLE, relation_rows)
                relation_rows.clear()
        if label_rows:
            insert_rows(client, database, TARGET_TABLE, label_rows)
        if relation_rows:
            insert_rows(client, database, RELATION_TABLE, relation_rows)
        insert_status(
            client, database, corpus, start, end, run_id,
            len(rows), label_count, relation_count, "completed", "",
        )
        return {
            "corpus": corpus,
            "start": start,
            "source_rows": len(rows),
            "label_rows": label_count,
            "relation_rows": relation_count,
        }
    except Exception as exc:
        try:
            insert_status(
                client, database, corpus, start, end, run_id,
                0, 0, 0, "failed", f"{type(exc).__name__}: {exc}"[:1000],
            )
        finally:
            raise
    finally:
        client.close()


def fetch_news_period(
    client: ClickHouseHttpClient,
    database: str,
    start: str,
    end: str,
) -> list[dict]:
    config = CandidateInventoryConfig()
    db = quote_ident(database)
    sql = f"""
SELECT
 e.canonical_news_id AS source_id,
 toString(e.published_at_utc) AS source_timestamp,
 e.title,
 r.rendered_text AS text,
 e.tickers AS entity_terms,
 e.tickers,
 e.channels,
 e.provider_tags,
 e.links,
 e.author,
 e.url_domain,
 e.article_url,
 e.content_quality_flags,
 r.renderer_version,
 r.text_contract,
 r.quality_flags,
 r.rendered_text_hash
FROM {db}.{quote_ident(config.news_event_table)} AS e FINAL
INNER JOIN {db}.{quote_ident(config.news_rendered_table)} AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.published_at_utc >= toDateTime64({sql_string(start)}, 9, 'UTC')
  AND e.published_at_utc < toDateTime64({sql_string(end)}, 9, 'UTC')
  AND notEmpty(r.rendered_text)
ORDER BY e.published_at_utc, e.canonical_news_id
FORMAT JSONEachRow
"""
    return json_rows(client.execute(sql))


def fetch_sec_period(
    client: ClickHouseHttpClient,
    database: str,
    start: str,
    end: str,
) -> list[dict]:
    config = CandidateInventoryConfig()
    db = quote_ident(database)
    sql = f"""
SELECT
 r.document_id AS source_id,
 toString(f.accepted_at_utc) AS source_timestamp,
 concat(ifNull(f.company_name, ''), ' ', ifNull(f.form_type, ''), ' ',
        ifNull(d.document_type, ''), ' ', ifNull(d.description, '')) AS title,
 r.text,
 [r.cik, ifNull(f.company_name, '')] AS entity_terms,
 [] AS tickers,
 r.cik,
 r.accession_number,
 r.filing_id,
 r.text_kind,
 r.text_char_count,
 r.text_sha256,
 r.normalizer_version AS source_normalizer_version,
 r.extraction_method,
 r.quality_flags,
 ifNull(d.document_type, '') AS document_type,
 ifNull(d.document_role, '') AS document_role,
 ifNull(d.description, '') AS description,
 ifNull(d.document_name, '') AS document_name,
 ifNull(f.company_name, '') AS company_name,
 ifNull(f.form_type, '') AS form_type,
 ifNull(f.items, '') AS filing_items,
 ifNull(toString(f.filing_date), '') AS filing_date,
 ifNull(toString(f.report_date), '') AS report_date,
 f.accepted_at_source
FROM {db}.{quote_ident(config.sec_rendered_table)} AS r FINAL
LEFT JOIN {db}.{quote_ident(config.sec_document_table)} AS d FINAL
 ON d.document_id=r.document_id
 AND d.cik=r.cik
 AND d.accession_number=r.accession_number
LEFT JOIN {db}.{quote_ident(config.sec_filing_table)} AS f FINAL
 ON f.filing_id=r.filing_id
 AND f.cik=r.cik
 AND f.accession_number=r.accession_number
WHERE f.accepted_at_utc >= toDateTime64({sql_string(start)}, 9, 'UTC')
  AND f.accepted_at_utc < toDateTime64({sql_string(end)}, 9, 'UTC')
  AND notEmpty(r.text)
ORDER BY f.accepted_at_utc, r.document_id
FORMAT JSONEachRow
"""
    rows = json_rows(client.execute(sql))
    attach_sec_tickers(client, rows)
    return rows


def row_to_document(row: dict, corpus: str) -> SemanticDocument:
    excluded = {
        "source_id", "source_timestamp", "title", "text", "entity_terms"
    }
    return SemanticDocument(
        corpus=corpus,
        source_id=str(row["source_id"]),
        timestamp=str(row["source_timestamp"]),
        title=str(row.get("title") or ""),
        text=str(row.get("text") or ""),
        entity_terms=tuple(str(value) for value in row.get("entity_terms") or []),
        tickers=tuple(
            str(value).upper() for value in row.get("tickers") or [] if value
        ),
        metadata={key: value for key, value in row.items() if key not in excluded},
    )


def persistence_row(
    document: SemanticDocument,
    label: ScopedLabel,
    run_id: str,
) -> dict:
    semantic = label.semantic
    classification = label.classification
    return {
        "corpus": document.corpus,
        "source_id": document.source_id,
        "source_timestamp": _clickhouse_time(document.timestamp),
        "unit_id": label.unit_id,
        "ticker": label.ticker,
        "unit_role": label.unit_role,
        # The canonical rendered-news table remains the one non-redundant
        # publication-text authority. Labels retain its hash plus only the
        # ticker-specific evidence used for deterministic semantics.
        "publication_text_hash": label.publication_text_hash,
        "event_id": label.event_id,
        "event_tickers": label.event_tickers,
        "issuer_role": label.issuer_role,
        "evidence_scope": label.evidence_scope,
        "semantic_evidence_text": label.semantic_evidence_text,
        "relevant_text": semantic["normalized_semantic_text"],
        "observed_direction": label.observed_reaction.direction,
        "observed_move_pct": label.observed_reaction.move_pct,
        "observed_resulting_price": label.observed_reaction.resulting_price,
        "observed_market_session": label.observed_reaction.market_session,
        "observed_evidence": label.observed_reaction.evidence,
        "reported_catalyst": label.reported_catalyst,
        "content_role": classification["content_role"],
        "source_origin": classification["source_origin"],
        "event_concepts": classification["event_concepts"],
        "semantic_direction": classification["semantic_direction"],
        "semantic_score": classification["semantic_score"],
        "forecast_trigger_eligible": int(label.forecast_trigger_eligible),
        "reaction_evaluation_eligible": int(
            label.reaction_evaluation_eligible
        ),
        "issuer_history_context_eligible": int(
            label.issuer_history_context_eligible
        ),
        "semantic_json": json.dumps(semantic, separators=(",", ":")),
        "classification_json": json.dumps(
            classification, separators=(",", ":")
        ),
        "labeling_version": label.version,
        "run_id": run_id,
        "updated_at_utc": _clickhouse_time(
            dt.datetime.now(dt.timezone.utc).isoformat()
        ),
    }


def relationship_rows(
    document: SemanticDocument,
    label: ScopedLabel,
    run_id: str,
) -> list[dict]:
    """Create normalized graph edges without copying publication text."""
    source_node = f"{document.corpus}:source:{document.source_id}"
    unit_node = f"{document.corpus}:unit:{label.unit_id}"
    event_node = f"event:{label.event_id}" if label.event_id else ""
    ticker_node = f"issuer:ticker:{label.ticker}" if label.ticker else ""
    edges = [(source_node, unit_node, "contains_unit", "")]
    if ticker_node:
        edges.append(
            (unit_node, ticker_node, "about_issuer", label.issuer_role)
        )
    if event_node:
        edges.append(
            (
                unit_node,
                event_node,
                "evidence_for_event",
                label.evidence_scope,
            )
        )
        if ticker_node:
            edges.append(
                (
                    event_node,
                    ticker_node,
                    "affects_issuer",
                    label.issuer_role,
                )
            )
    for concept in label.classification["event_concepts"]:
        edges.append(
            (unit_node, f"concept:{concept}", "expresses_concept", "")
        )
    updated = _clickhouse_time(dt.datetime.now(dt.timezone.utc).isoformat())
    return [
        {
            "corpus": document.corpus,
            "source_id": document.source_id,
            "source_timestamp": _clickhouse_time(document.timestamp),
            "from_node": left,
            "to_node": right,
            "relation_type": relation,
            "relation_role": role,
            "labeling_version": label.version,
            "run_id": run_id,
            "updated_at_utc": updated,
        }
        for left, right, relation, role in edges
    ]


def create_tables(client: ClickHouseHttpClient, database: str) -> None:
    db = quote_ident(database)
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(TARGET_TABLE)}
(
 corpus LowCardinality(String),
 source_id String,
 source_timestamp DateTime64(9, 'UTC'),
 unit_id String,
 ticker LowCardinality(String),
 unit_role LowCardinality(String),
 publication_text_hash FixedString(64),
 event_id String,
 event_tickers Array(LowCardinality(String)),
 issuer_role LowCardinality(String),
 evidence_scope LowCardinality(String),
 semantic_evidence_text String,
 relevant_text String,
 observed_direction LowCardinality(String),
 observed_move_pct Nullable(Float64),
 observed_resulting_price Nullable(Float64),
 observed_market_session LowCardinality(String),
 observed_evidence String,
 reported_catalyst String,
 content_role LowCardinality(String),
 source_origin LowCardinality(String),
 event_concepts Array(String),
 semantic_direction LowCardinality(String),
 semantic_score Float64,
 forecast_trigger_eligible UInt8,
 reaction_evaluation_eligible UInt8,
 issuer_history_context_eligible UInt8,
 semantic_json String,
 classification_json String,
 labeling_version LowCardinality(String),
 run_id String,
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY (corpus, toYYYYMM(source_timestamp))
ORDER BY (corpus, ticker, source_timestamp, source_id, unit_id, labeling_version)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(STATUS_TABLE)}
(
 corpus LowCardinality(String),
 period_start Date,
 period_end_exclusive Date,
 labeling_version LowCardinality(String),
 run_id String,
 source_rows UInt64,
 label_rows UInt64,
 relation_rows UInt64,
 status LowCardinality(String),
 error String,
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (
 corpus, period_start, period_end_exclusive, labeling_version
)
""")
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {db}.{quote_ident(RELATION_TABLE)}
(
 corpus LowCardinality(String),
 source_id String,
 source_timestamp DateTime64(9, 'UTC'),
 from_node String,
 to_node String,
 relation_type LowCardinality(String),
 relation_role LowCardinality(String),
 labeling_version LowCardinality(String),
 run_id String,
 updated_at_utc DateTime64(6, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY (corpus, toYYYYMM(source_timestamp))
ORDER BY (
 corpus, source_id, from_node, to_node, relation_type, labeling_version
)
""")
    client.execute(
        f"ALTER TABLE {db}.{quote_ident(STATUS_TABLE)} "
        "ADD COLUMN IF NOT EXISTS relation_rows UInt64 AFTER label_rows"
    )


def insert_rows(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    rows: list[dict],
) -> None:
    if not rows:
        return
    body = "\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        for row in rows
    )
    client.execute(
        f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} "
        f"FORMAT JSONEachRow\n{body}"
    )


def insert_status(
    client: ClickHouseHttpClient,
    database: str,
    corpus: str,
    start: str,
    end: str,
    run_id: str,
    source_rows: int,
    label_rows: int,
    relation_rows: int,
    status: str,
    error: str,
) -> None:
    insert_rows(
        client,
        database,
        STATUS_TABLE,
        [{
            "corpus": corpus,
            "period_start": start,
            "period_end_exclusive": end,
            "labeling_version": SCOPED_LABELING_VERSION,
            "run_id": run_id,
            "source_rows": source_rows,
            "label_rows": label_rows,
            "relation_rows": relation_rows,
            "status": status,
            "error": error,
            "updated_at_utc": _clickhouse_time(
                dt.datetime.now(dt.timezone.utc).isoformat()
            ),
        }],
    )


def completed_units(
    client: ClickHouseHttpClient,
    database: str,
) -> set[tuple[str, str, str, str]]:
    sql = f"""
SELECT corpus, toString(period_start) AS period_start,
       toString(period_end_exclusive) AS period_end_exclusive,
       labeling_version
FROM {quote_ident(database)}.{quote_ident(STATUS_TABLE)} FINAL
WHERE status='completed'
  AND labeling_version={sql_string(SCOPED_LABELING_VERSION)}
  AND (label_rows=0 OR relation_rows>0)
FORMAT JSONEachRow
"""
    return {
        (
            str(row["corpus"]),
            str(row["period_start"]),
            str(row["period_end_exclusive"]),
            str(row["labeling_version"]),
        )
        for row in json_rows(client.execute(sql))
    }


def source_counts(
    client: ClickHouseHttpClient,
    database: str,
    plan: list[tuple[str, str, str]],
) -> dict:
    config = CandidateInventoryConfig()
    tables = {
        "news": config.news_event_table,
        "sec": config.sec_filing_table,
    }
    columns = {
        "news": "published_at_utc",
        "sec": "accepted_at_utc",
    }
    result = {(corpus, start, end): 0 for corpus, start, end in plan}
    by_corpus = {
        corpus: [(start, end) for kind, start, end in plan if kind == corpus]
        for corpus in {kind for kind, _, _ in plan}
    }
    for corpus, periods in by_corpus.items():
        overall_start = min(start for start, _ in periods)
        overall_end = max(end for _, end in periods)
        sql = f"""
SELECT toDate({columns[corpus]}) AS day, count() AS rows
FROM {quote_ident(database)}.{quote_ident(tables[corpus])}
WHERE {columns[corpus]} >= toDateTime64({sql_string(overall_start)}, 9, 'UTC')
  AND {columns[corpus]} < toDateTime64({sql_string(overall_end)}, 9, 'UTC')
GROUP BY day
FORMAT JSONEachRow
"""
        daily = {
            str(row["day"]): int(row["rows"])
            for row in json_rows(client.execute(sql))
        }
        for start, end in periods:
            cursor = dt.date.fromisoformat(start)
            finish = dt.date.fromisoformat(end)
            total = 0
            while cursor < finish:
                total += daily.get(cursor.isoformat(), 0)
                cursor += dt.timedelta(days=1)
            result[(corpus, start, end)] = total
    return result


def bounded_period_ranges(
    start: str,
    end: str,
    period_days: int,
) -> list[tuple[str, str]]:
    cursor = dt.date.fromisoformat(start)
    finish = dt.date.fromisoformat(end)
    output = []
    while cursor < finish:
        right = min(cursor + dt.timedelta(days=period_days), finish)
        output.append((cursor.isoformat(), right.isoformat()))
        cursor = right
    return output


def make_client() -> ClickHouseHttpClient:
    return ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=1800,
    )


def _clickhouse_time(value: str) -> str:
    clean = str(value).replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(clean)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")


def _validate_dates(start: str, end: str) -> None:
    if dt.date.fromisoformat(start) >= dt.date.fromisoformat(end):
        raise ValueError("start date must precede end date")


def assert_certification(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(
            f"certification manifest is missing: {path}. "
            "Run and review run_certification before persistence."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("labeling_version") != SCOPED_LABELING_VERSION:
        raise RuntimeError(
            "certification version does not match the persistence version"
        )
    if int(payload.get("news_audits") or 0) < 5 \
            or int(payload.get("sec_audits") or 0) < 5:
        raise RuntimeError("certification does not contain five News and five SEC audits")
    if int(payload.get("review_attention") or 0) != 0:
        raise RuntimeError("certification self-review still has attention items")
    if payload.get("missing_news_scope_cases") != []:
        raise RuntimeError(
            "certification is missing one or more required News issuer-scope cases"
        )
    if payload.get("expected_outcome_failures") != []:
        raise RuntimeError(
            "certification failed mandatory issuer-level semantic outcomes"
        )
