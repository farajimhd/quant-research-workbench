from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

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
from research.news_labeling.market_reaction_evaluation_v1 import fetch_reactions
from research.news_reaction_model.v17.prepare_targets import (
    IntervalRequest,
    calendar_sessions,
    event_rows_for_tickers,
    interval_aggregates,
)
from research.news_reaction_model.v17.config import LoaderConfig
from research.text_intelligence.candidate_inventory_v1.audit_samples import (
    NEWS_STRATA,
    SEC_STRATA,
)
from research.text_intelligence.candidate_inventory_v1.config import (
    CandidateInventoryConfig,
)
from research.text_intelligence.semantic_label_authority_v1.labeler import (
    label_document,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .authority import classify_document
from .schema import CLASSIFICATION_AUTHORITY_VERSION


UTC = dt.timezone.utc
EASTERN = ZoneInfo("America/New_York")
EVALUATION_VERSION = "text_classification_market_audit_v2"
REACTION_HORIZON = "30m"
REACTION_NOISE_PCT = 0.50


@dataclass(frozen=True, slots=True)
class Reaction:
    corpus: str
    source_id: str
    ticker: str
    published_at_utc: str
    anchor_price: float
    terminal_return: float
    high_return: float
    low_return: float
    observation_count: int
    source: str

    @property
    def direction(self) -> str:
        return reaction_direction(
            self.high_return,
            self.low_return,
            REACTION_NOISE_PCT,
        )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    runtime = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "classification_authority_v2"
        / "evaluation_1000_news_1000_sec"
    )
    parser = argparse.ArgumentParser(
        description="Build and audit the combined News/SEC classifier."
    )
    parser.add_argument("--sample-size", type=int, default=1000)
    parser.add_argument("--audit-count", type=int, default=10)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date-exclusive", default="2027-01-01")
    parser.add_argument("--output-root", type=Path, default=runtime)
    return parser.parse_args(list(argv) if argv is not None else None)


def run(args: argparse.Namespace) -> dict[str, Any]:
    assert_runtime_root(args.output_root)
    args.output_root.mkdir(parents=True, exist_ok=True)
    load_env_files(discover_env_files(Path.cwd()), verbose=True)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=600,
    )
    config = CandidateInventoryConfig(
        start_date=args.start_date,
        end_date_exclusive=args.end_date_exclusive,
    )
    try:
        news = fetch_news_sample(client, config, args.sample_size)
        sec = fetch_sec_sample(client, config, args.sample_size)
        if len(news) != args.sample_size or len(sec) != args.sample_size:
            raise RuntimeError(
                f"sample contract failed: news={len(news)} sec={len(sec)}"
            )
        rows = classify_rows(news, sec)
        news_reactions = news_reaction_rows(client, news)
        sec_reactions = sec_reaction_rows(client, sec)
        reactions = [*news_reactions, *sec_reactions]
        payload = summarize(rows, reactions)
        write_outputs(args.output_root, rows, reactions, payload)
        create_dossiers(
            client,
            args.output_root,
            rows,
            reactions,
            count=args.audit_count,
        )
        return payload
    finally:
        client.close()


def fetch_news_sample(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
    sample_size: int,
) -> list[dict[str, Any]]:
    target_per = math.ceil(sample_size / len(NEWS_STRATA))
    db = quote_ident(config.database)
    event = quote_ident(config.news_event_table)
    rendered = quote_ident(config.news_rendered_table)
    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for stratum, rationale, predicate in NEWS_STRATA:
        excluded = (
            "AND e.canonical_news_id NOT IN ("
            + ",".join(sql_string(value) for value in sorted(used))
            + ")"
            if used
            else ""
        )
        sql = f"""
SELECT
 e.canonical_news_id AS source_id,
 toString(e.published_at_utc) AS source_timestamp,
 e.published_at_utc,
 e.provider_article_id,
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
FROM {db}.{event} AS e FINAL
INNER JOIN {db}.{rendered} AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
WHERE e.published_at_utc >= toDateTime64({sql_string(config.start_date)}, 9, 'UTC')
  AND e.published_at_utc < toDateTime64({sql_string(config.end_date_exclusive)}, 9, 'UTC')
  AND lengthUTF8(r.rendered_text) BETWEEN 500 AND 50000
  AND ({predicate})
  {excluded}
ORDER BY cityHash64(concat(e.canonical_news_id, {sql_string(stratum)}))
LIMIT {target_per}
FORMAT JSONEachRow
"""
        found = json_rows(client.execute(sql))
        for row in found:
            identifier = str(row["source_id"])
            if identifier in used:
                continue
            used.add(identifier)
            row["sample_stratum"] = stratum
            row["sample_rationale"] = rationale
            rows.append(row)
        print(
            f"NEWS {stratum}: {len(found):,} total={len(rows):,}",
            flush=True,
        )
    return rows[:sample_size]


def fetch_sec_sample(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
    sample_size: int,
) -> list[dict[str, Any]]:
    target_per = math.ceil(sample_size / len(SEC_STRATA))
    db = quote_ident(config.database)
    rendered = quote_ident(config.sec_rendered_table)
    document = quote_ident(config.sec_document_table)
    filing = quote_ident(config.sec_filing_table)
    identities: list[dict[str, Any]] = []
    used_filings: set[tuple[str, str]] = set()
    for lane, (kind, rationale) in enumerate(SEC_STRATA, start=1):
        sql = f"""
SELECT cik, accession_number, document_id, text_kind
FROM {db}.{rendered}
PREWHERE source_archive_date >= toDate({sql_string(config.start_date)})
  AND source_archive_date < toDate({sql_string(config.end_date_exclusive)})
WHERE text_kind={sql_string(kind)}
  AND text_char_count BETWEEN 500 AND 50000
ORDER BY cityHash64(concat(cik, accession_number, document_id, toString({lane})))
LIMIT 1 BY cik, accession_number
LIMIT {target_per * 2}
FORMAT JSONEachRow
"""
        for row in json_rows(client.execute(sql)):
            key = (str(row["cik"]), str(row["accession_number"]))
            if key in used_filings:
                continue
            used_filings.add(key)
            row["sample_stratum"] = kind
            row["sample_rationale"] = rationale
            identities.append(row)
            if sum(
                value["sample_stratum"] == kind for value in identities
            ) >= target_per:
                break
        print(
            f"SEC identities {kind}: "
            f"{sum(value['sample_stratum'] == kind for value in identities):,}",
            flush=True,
        )
    selected = identities[:sample_size]
    result: list[dict[str, Any]] = []
    by_identity = {str(value["document_id"]): value for value in selected}
    for offset in range(0, len(selected), 40):
        document_ids = ",".join(
            sql_string(str(value["document_id"]))
            for value in selected[offset : offset + 40]
        )
        sql = f"""
SELECT
 r.document_id AS source_id,
 toString(f.accepted_at_utc) AS source_timestamp,
 f.accepted_at_utc,
 concat(ifNull(f.company_name, ''), ' ', ifNull(f.form_type, ''), ' ',
        ifNull(d.document_type, ''), ' ', ifNull(d.description, '')) AS title,
 r.text,
 [r.cik, ifNull(f.company_name, '')] AS entity_terms,
 r.cik AS cik,
 r.accession_number AS accession_number,
 r.filing_id AS filing_id,
 r.text_kind AS text_kind,
 r.text_char_count AS text_char_count,
 r.text_sha256 AS text_sha256,
 r.normalizer_version AS source_normalizer_version,
 r.extraction_method AS extraction_method,
 r.quality_flags AS quality_flags,
 ifNull(d.document_type, '') AS document_type,
 ifNull(d.document_role, '') AS document_role,
 ifNull(d.description, '') AS description,
 ifNull(d.document_name, '') AS document_name,
 ifNull(f.company_name, '') AS company_name,
 ifNull(f.form_type, '') AS form_type,
 ifNull(f.items, '') AS filing_items,
 ifNull(toString(f.filing_date), '') AS filing_date,
 ifNull(toString(f.report_date), '') AS report_date,
 f.accepted_at_source AS accepted_at_source
FROM
(
 SELECT *
 FROM {db}.{rendered} FINAL
 PREWHERE document_id IN ({document_ids})
) AS r
LEFT JOIN
(
 SELECT *
 FROM {db}.{document} FINAL
 PREWHERE document_id IN ({document_ids})
) AS d
 ON d.document_id=r.document_id
 AND d.cik=r.cik
 AND d.accession_number=r.accession_number
LEFT JOIN {db}.{filing} AS f FINAL
 ON f.filing_id=r.filing_id
 AND f.cik=r.cik
 AND f.accession_number=r.accession_number
WHERE isNotNull(f.accepted_at_utc)
FORMAT JSONEachRow
"""
        for row in json_rows(client.execute(sql)):
            identity = by_identity.get(str(row["source_id"]))
            if identity is None:
                continue
            row["sample_stratum"] = identity["sample_stratum"]
            row["sample_rationale"] = identity["sample_rationale"]
            result.append(row)
        print(
            f"SEC documents: {min(offset + 40, len(selected)):,}/"
            f"{len(selected):,} loaded={len(result):,}",
            flush=True,
        )
    if len(result) < sample_size:
        raise RuntimeError(
            f"SEC exact fetch returned {len(result)} of {sample_size}; "
            "accepted timestamps or source joins are incomplete."
        )
    attach_sec_tickers(client, result)
    return result[:sample_size]


def attach_sec_tickers(
    client: ClickHouseHttpClient,
    rows: list[dict[str, Any]],
) -> None:
    ciks = sorted({str(row["cik"]) for row in rows})
    mappings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for offset in range(0, len(ciks), 300):
        values = ",".join(sql_string(value) for value in ciks[offset : offset + 300])
        sql = f"""
SELECT cik, ifNull(ticker, '') AS ticker,
       ifNull(toString(valid_from_date), '') AS valid_from_date,
       ifNull(toString(valid_to_date_exclusive), '') AS valid_to_date_exclusive,
       mapping_status, ambiguity_status, confidence_score
FROM q_live.id_sec_market_bridge_v1 FINAL
WHERE cik IN ({values}) AND notEmpty(ifNull(ticker, ''))
FORMAT JSONEachRow
"""
        for row in json_rows(client.execute(sql)):
            mappings[str(row["cik"])].append(row)
    for row in rows:
        accepted = parse_utc(row["source_timestamp"]).date()
        eligible = [
            value
            for value in mappings.get(str(row["cik"]), ())
            if date_contains(value, accepted)
            and str(value.get("mapping_status") or "") in {"resolved", "active"}
            and str(value.get("ambiguity_status") or "") not in {
                "ambiguous",
                "unresolved",
            }
        ]
        eligible.sort(
            key=lambda value: (
                -float(value.get("confidence_score") or 0.0),
                str(value.get("ticker") or ""),
            )
        )
        ticker = str(eligible[0]["ticker"]).upper() if eligible else ""
        row["tickers"] = [ticker] if ticker else []
        row["ticker_mapping_status"] = "resolved_point_in_time" if ticker else "missing"


def classify_rows(
    news: Sequence[dict[str, Any]],
    sec: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, row in enumerate((*news, *sec), start=1):
        corpus = "news" if index <= len(news) else "sec"
        excluded = {
            "source_id",
            "source_timestamp",
            "title",
            "text",
            "entity_terms",
        }
        metadata = {
            key: value for key, value in row.items() if key not in excluded
        }
        metadata["source_timestamp"] = row["source_timestamp"]
        document = SemanticDocument(
            corpus=corpus,
            source_id=str(row["source_id"]),
            timestamp=str(row["source_timestamp"]),
            title=str(row.get("title") or ""),
            text=str(row.get("text") or ""),
            entity_terms=tuple(
                str(value)
                for value in row.get("entity_terms") or []
                if str(value)
            ),
            tickers=tuple(
                str(value).upper()
                for value in row.get("tickers") or []
                if str(value)
            ),
            metadata=metadata,
        )
        semantic = label_document(
            document,
            include_discovery_evidence=False,
        )
        classification = classify_document(
            document,
            semantic_result=semantic,
        )
        output.append(
            {
                "corpus": corpus,
                "source_id": document.source_id,
                "source_timestamp": document.timestamp,
                "title": document.title,
                "text": document.text,
                "tickers": list(document.tickers),
                "sample_stratum": row["sample_stratum"],
                "sample_rationale": row["sample_rationale"],
                "classification": classification.as_dict(),
                "labels": [
                    {
                        **asdict(label),
                        "evidence": [asdict(value) for value in label.evidence],
                    }
                    for label in semantic.labels
                ],
                "normalized_semantic_text": semantic.normalized_semantic_text,
                "metadata": metadata,
            }
        )
        if index % 100 == 0 or index == len(news) + len(sec):
            print(
                f"CLASSIFY {index:,}/{len(news) + len(sec):,}",
                flush=True,
            )
    return output


def news_reaction_rows(
    client: ClickHouseHttpClient,
    news: Sequence[dict[str, Any]],
) -> list[Reaction]:
    sample = {
        str(row["source_id"]): {
            "published_at_utc": row["source_timestamp"],
            "tickers": row.get("tickers") or [],
        }
        for row in news
    }
    raw = fetch_reactions(
        client,
        "q_live.news_reaction_labels_v2",
        list(sample),
        sample,
    )
    result = [
        Reaction(
            corpus="news",
            source_id=str(row["canonical_news_id"]),
            ticker=str(row["ticker"]),
            published_at_utc=str(row["published_at_utc"]),
            anchor_price=float(row["anchor_price"]),
            terminal_return=float(row["target_return"]),
            high_return=float(row["high_return"]),
            low_return=float(row["low_return"]),
            observation_count=int(row["observation_count"]),
            source="news_reaction_labels_v2+quality_overlay_v1",
        )
        for row in raw
        if str(row["horizon_code"]) == REACTION_HORIZON
    ]
    print(f"NEWS reactions: {len(result):,}", flush=True)
    return result


def sec_reaction_rows(
    client: ClickHouseHttpClient,
    sec: Sequence[dict[str, Any]],
) -> list[Reaction]:
    config = LoaderConfig()
    grouped: dict[dt.date, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(sec):
        tickers = row.get("tickers") or []
        if not tickers:
            continue
        published = parse_utc(row["source_timestamp"])
        grouped[published.astimezone(EASTERN).date()].append((index, row))
    result: list[Reaction] = []
    for ordinal, (day, day_rows) in enumerate(sorted(grouped.items()), start=1):
        for offset in range(0, len(day_rows), 64):
            batch = day_rows[offset : offset + 64]
            requests: list[IntervalRequest] = []
            for row_index, row in batch:
                published = parse_utc(row["source_timestamp"])
                requests.append(
                    IntervalRequest(
                        row_index=row_index,
                        ticker=str(row["tickers"][0]),
                        anchor_start_us=int(
                            (published - dt.timedelta(days=7)).timestamp()
                            * 1_000_000
                        ),
                        start_us=int(published.timestamp() * 1_000_000),
                        end_us=int(
                            (published + dt.timedelta(minutes=30)).timestamp()
                            * 1_000_000
                        ),
                    )
                )
            aggregates = interval_aggregates(client, config, requests)
            for row_index, row in batch:
                value = aggregates.get(row_index)
                if (
                    value is None
                    or not math.isfinite(value.anchor_price)
                    or value.anchor_price <= 0
                    or not math.isfinite(value.terminal_price)
                    or not math.isfinite(value.high_price)
                    or not math.isfinite(value.low_price)
                ):
                    continue
                result.append(
                    Reaction(
                        corpus="sec",
                        source_id=str(row["source_id"]),
                        ticker=str(row["tickers"][0]),
                        published_at_utc=str(row["source_timestamp"]),
                        anchor_price=value.anchor_price,
                        terminal_return=value.terminal_price
                        / value.anchor_price
                        - 1.0,
                        high_return=value.high_price / value.anchor_price - 1.0,
                        low_return=value.low_price / value.anchor_price - 1.0,
                        observation_count=value.observation_count,
                        source="canonical_sip_exact_event_30_wall_minutes_v1",
                    )
                )
        if ordinal % 50 == 0 or ordinal == len(grouped):
            print(
                f"SEC reaction days {ordinal:,}/{len(grouped):,} "
                f"reactions={len(result):,}",
                flush=True,
            )
    return result


def summarize(
    rows: Sequence[dict[str, Any]],
    reactions: Sequence[Reaction],
) -> dict[str, Any]:
    by_key: dict[tuple[str, str], list[Reaction]] = defaultdict(list)
    for value in reactions:
        by_key[(value.corpus, value.source_id)].append(value)
    classifications = [row["classification"] for row in rows]
    payload: dict[str, Any] = {
        "evaluation_version": EVALUATION_VERSION,
        "authority_version": CLASSIFICATION_AUTHORITY_VERSION,
        "sample": {
            "news": sum(row["corpus"] == "news" for row in rows),
            "sec": sum(row["corpus"] == "sec" for row in rows),
        },
        "classification_distributions": {},
        "reaction": {},
    }
    for corpus in ("news", "sec"):
        subset = [
            row["classification"]
            for row in rows
            if row["corpus"] == corpus
        ]
        payload["classification_distributions"][corpus] = {
            field: dict(sorted(Counter(row[field] for row in subset).items()))
            for field in (
                "source_origin",
                "content_role",
                "issuer_relationship",
                "semantic_direction",
                "source_type",
            )
        }
        payload["classification_distributions"][corpus][
            "forecast_trigger_eligible"
        ] = dict(
            sorted(
                Counter(
                    str(row["forecast_trigger_eligible"]).lower()
                    for row in subset
                ).items()
            )
        )
    for corpus in ("news", "sec"):
        corpus_rows = [row for row in rows if row["corpus"] == corpus]
        paired: list[tuple[str, str]] = []
        mixed = 0
        for row in corpus_rows:
            predicted = row["classification"]["semantic_direction"]
            values = by_key.get((corpus, row["source_id"]), ())
            for reaction in values:
                if predicted == "mixed":
                    mixed += 1
                    continue
                if predicted not in {"positive", "negative", "neutral"}:
                    continue
                paired.append((predicted, reaction.direction))
        correct = sum(left == right for left, right in paired)
        confusion = Counter(f"{left}->{right}" for left, right in paired)
        payload["reaction"][corpus] = {
            "documents": len(corpus_rows),
            "reaction_links": sum(
                len(by_key.get((corpus, row["source_id"]), ()))
                for row in corpus_rows
            ),
            "scored_links": len(paired),
            "mixed_direction_links_excluded": mixed,
            "exact_direction_agreement": (
                correct / len(paired) if paired else None
            ),
            "confusion": dict(sorted(confusion.items())),
            "interpretation": (
                "Descriptive language-direction versus realized 30-minute "
                "excursion-dominance agreement; not taxonomy ground-truth accuracy."
            ),
        }
    payload["quality"] = {
        "documents_with_no_event_concept": sum(
            not row["event_concepts"] for row in classifications
        ),
        "documents_with_quality_flags": sum(
            bool(row["quality_flags"]) for row in classifications
        ),
    }
    return payload


def write_outputs(
    root: Path,
    rows: Sequence[dict[str, Any]],
    reactions: Sequence[Reaction],
    payload: dict[str, Any],
) -> None:
    write_json_atomic(root / "summary.json", payload)
    write_jsonl_atomic(root / "classifications.jsonl", rows)
    write_jsonl_atomic(
        root / "reactions.jsonl",
        [asdict(value) | {"direction": value.direction} for value in reactions],
    )
    (root / "REPORT.md").write_text(
        render_report(payload),
        encoding="utf-8",
    )


def create_dossiers(
    client: ClickHouseHttpClient,
    root: Path,
    rows: Sequence[dict[str, Any]],
    reactions: Sequence[Reaction],
    *,
    count: int,
) -> None:
    requested_per = max(1, count // 2)
    reaction_by_key = {
        (value.corpus, value.source_id, value.ticker): value
        for value in reactions
    }
    selected: list[tuple[dict[str, Any], Reaction]] = []
    for corpus in ("news", "sec"):
        candidates: list[tuple[dict[str, Any], Reaction]] = []
        for row in rows:
            if row["corpus"] != corpus:
                continue
            for ticker in row["tickers"]:
                reaction = reaction_by_key.get(
                    (corpus, row["source_id"], ticker)
                )
                if reaction is not None:
                    candidates.append((row, reaction))
        candidates.sort(
            key=lambda item: (
                item[0]["classification"]["semantic_direction"]
                == item[1].direction,
                -abs(item[0]["classification"]["semantic_score"]),
                item[0]["source_id"],
            )
        )
        roles: set[str] = set()
        corpus_selected: set[str] = set()
        for item in candidates:
            role = item[0]["classification"]["content_role"]
            if role in roles and len(roles) < requested_per:
                continue
            selected.append(item)
            corpus_selected.add(item[0]["source_id"])
            roles.add(role)
            if len(corpus_selected) >= requested_per:
                break
        if len(corpus_selected) < requested_per:
            for item in candidates:
                if item[0]["source_id"] in corpus_selected:
                    continue
                selected.append(item)
                corpus_selected.add(item[0]["source_id"])
                if len(corpus_selected) >= requested_per:
                    break
    sessions = calendar_sessions(client, LoaderConfig())
    audit_root = root / "audits"
    plot_root = audit_root / "plots"
    audit_root.mkdir(parents=True, exist_ok=True)
    plot_root.mkdir(parents=True, exist_ok=True)
    index_lines = [
        "# Combined classification and market-reaction audits",
        "",
        "| # | Corpus | Source | Ticker | Language | Reaction | Agreement | File |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    event_cache: dict[tuple[str, dt.date], np.ndarray] = {}
    for ordinal, (row, reaction) in enumerate(selected, start=1):
        days = audit_days(sessions, row["source_timestamp"])
        bars = fetch_bars(
            client,
            LoaderConfig(),
            reaction.ticker,
            days,
            event_cache,
        )
        stem = (
            f"{ordinal:02d}_{row['corpus']}_{slug(reaction.ticker)}_"
            f"{slug(row['source_id'])[:24]}"
        )
        chart = plot_root / f"{stem}.png"
        render_chart(
            chart,
            ticker=reaction.ticker,
            bars=bars,
            published_at=parse_utc(row["source_timestamp"]),
        )
        dossier = audit_root / f"{stem}.md"
        dossier.write_text(
            render_dossier(
                row,
                reaction,
                chart_relative=f"plots/{chart.name}",
            ),
            encoding="utf-8",
        )
        agreement = (
            row["classification"]["semantic_direction"] == reaction.direction
        )
        index_lines.append(
            f"| {ordinal} | {row['corpus']} | `{row['source_id']}` | "
            f"`{reaction.ticker}` | {row['classification']['semantic_direction']} | "
            f"{reaction.direction} | {'yes' if agreement else 'no'} | "
            f"[open]({dossier.name}) |"
        )
    (audit_root / "INDEX.md").write_text(
        "\n".join(index_lines) + "\n",
        encoding="utf-8",
    )


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Combined News and SEC classification evaluation",
        "",
        f"- Authority: `{payload['authority_version']}`",
        f"- News documents: **{payload['sample']['news']:,}**",
        f"- SEC documents: **{payload['sample']['sec']:,}**",
        "",
        "## Interpretation boundary",
        "",
        "The collection is deliberately stratified across five News event/format "
        "families and five SEC rendered-document kinds. Its distributions are "
        "audit coverage, not estimates of corpus prevalence.",
        "",
        "Taxonomy labels and market response answer different questions. The tables "
        "below report classification distributions and a descriptive agreement "
        "between semantic language direction and realized 30-minute "
        "excursion-dominance. That agreement is not precision or recall for "
        "company/editorial/SEC type because no reviewed taxonomy answer key exists.",
        "",
    ]
    for corpus in ("news", "sec"):
        lines.extend([f"## {corpus.upper()} classification", ""])
        for field, counts in payload["classification_distributions"][corpus].items():
            lines.extend(
                [
                    f"### {field}",
                    "",
                    "| Value | Documents |",
                    "|---|---:|",
                    *[
                        f"| `{value}` | {count:,} |"
                        for value, count in counts.items()
                    ],
                    "",
                ]
            )
        reaction = payload["reaction"][corpus]
        agreement = reaction["exact_direction_agreement"]
        lines.extend(
            [
                f"## {corpus.upper()} language/reaction comparison",
                "",
                f"- Reaction links: **{reaction['reaction_links']:,}**.",
                f"- Scored three-class links: **{reaction['scored_links']:,}**.",
                f"- Mixed-language links excluded: **{reaction['mixed_direction_links_excluded']:,}**.",
                "- Exact direction agreement: "
                + (
                    f"**{agreement:.2%}**."
                    if agreement is not None
                    else "**not available**."
                ),
                "",
                "| Language -> reaction | Links |",
                "|---|---:|",
                *[
                    f"| `{value}` | {count:,} |"
                    for value, count in reaction["confusion"].items()
                ],
                "",
            ]
        )
    return "\n".join(lines)


def render_dossier(
    row: dict[str, Any],
    reaction: Reaction,
    *,
    chart_relative: str,
) -> str:
    classification = row["classification"]
    lines = [
        f"# {row['corpus'].upper()} classification audit — {reaction.ticker}",
        "",
        "## Verdict",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Source ID | `{row['source_id']}` |",
        f"| Published/accepted UTC | `{row['source_timestamp']}` |",
        f"| Sampling stratum | `{row['sample_stratum']}` |",
        f"| Source origin | `{classification['source_origin']}` |",
        f"| Content role | `{classification['content_role']}` |",
        f"| Issuer relationship | `{classification['issuer_relationship']}` |",
        f"| Source type | `{classification['source_type']}` |",
        f"| Source subtype | `{classification['source_subtype']}` |",
        f"| Semantic direction | **{classification['semantic_direction']}** ({classification['semantic_score']:+.2f}) |",
        f"| Market reaction direction | **{reaction.direction}** |",
        f"| Exact direction agreement | **{'yes' if classification['semantic_direction'] == reaction.direction else 'no'}** |",
        f"| Forecast trigger eligible | `{classification['forecast_trigger_eligible']}` |",
        f"| Prior primary context eligible | `{classification['prior_primary_context_eligible']}` |",
        f"| Episode follow-up eligible | `{classification['episode_followup_eligible']}` |",
        "",
        "## Exact-event price action",
        "",
        f"![{reaction.ticker} price action]({chart_relative})",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Anchor price | {reaction.anchor_price:.6f} |",
        f"| 30-minute terminal return | {reaction.terminal_return:+.3%} |",
        f"| 30-minute high return | {reaction.high_return:+.3%} |",
        f"| 30-minute low return | {reaction.low_return:+.3%} |",
        f"| Eligible observations | {reaction.observation_count:,} |",
        f"| Reaction authority | `{reaction.source}` |",
        "",
        "## Canonical event labels",
        "",
        "| Family | Subtype | Direction | Modality | Time | Evidence |",
        "|---|---|---|---|---|---|",
    ]
    if row["labels"]:
        for label in row["labels"]:
            evidence = "; ".join(value["text"] for value in label["evidence"])
            lines.append(
                f"| `{label['family']}` | `{label['subtype']}` | "
                f"{label['direction']} | {label['modality']} | "
                f"{label['time_orientation']} | {escape(evidence)} |"
            )
    else:
        lines.append("| — | — | neutral | — | — | No supported event concept |")
    lines.extend(
        [
            "",
            "## Classification evidence",
            "",
            *[f"- {escape(value)}" for value in classification["evidence"]],
            "",
            "## Source metadata",
            "",
            "| Field | Value |",
            "|---|---|",
            *[
                f"| `{escape(key)}` | {escape(json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value)} |"
                for key, value in sorted(row["metadata"].items())
                if key not in {"text"}
            ],
            "",
            "## Normalized semantic text",
            "",
            fence(row["normalized_semantic_text"]),
            "",
            "## Original rendered text",
            "",
            fence(row["text"]),
            "",
            "## Audit questions",
            "",
            "- [ ] Is source origin supported by provider/source metadata?",
            "- [ ] Is content role distinct from the event meaning?",
            "- [ ] Does issuer relationship describe who announced versus who reported?",
            "- [ ] Are canonical concepts and direction supported by exact text?",
            "- [ ] Is the reaction comparison interpreted as response, not taxonomy truth?",
            "",
        ]
    )
    return "\n".join(lines)


def reaction_direction(
    high_return: float,
    low_return: float,
    minimum_span_pct: float,
) -> str:
    upside = max(0.0, float(high_return) * 100.0)
    downside = max(0.0, -float(low_return) * 100.0)
    if upside + downside < minimum_span_pct:
        return "neutral"
    if math.isclose(upside, downside, rel_tol=0.0, abs_tol=1e-12):
        return "neutral"
    return "positive" if upside > downside else "negative"


def audit_days(
    sessions: Sequence[dt.date],
    timestamp: str,
) -> tuple[dt.date, ...]:
    day = parse_utc(timestamp).astimezone(EASTERN).date()
    position = bisect.bisect_left(sessions, day)
    return tuple(sessions[position : position + 3])


def fetch_bars(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    ticker: str,
    days: Sequence[dt.date],
    cache: dict[tuple[str, dt.date], np.ndarray],
) -> list[tuple[dt.datetime, float, float, float, float, float]]:
    output: list[tuple[dt.datetime, float, float, float, float, float]] = []
    for day in days:
        key = (ticker, day)
        if key not in cache:
            cache[key] = event_rows_for_tickers(
                client, config, [ticker], day
            )[ticker]
        output.extend(minute_bars(cache[key]))
    return output


def minute_bars(
    events: np.ndarray,
) -> list[tuple[dt.datetime, float, float, float, float, float]]:
    if not events.size:
        return []
    minute_ids = events[:, 0].astype(np.int64) // 60_000_000
    last_mask = events[:, 6] > 0.5
    extrema_mask = events[:, 7] > 0.5
    output = []
    for minute in np.unique(minute_ids[last_mask | extrema_mask]):
        last = events[(minute_ids == minute) & last_mask]
        extrema = events[(minute_ids == minute) & extrema_mask]
        reference = last if last.size else extrema
        if not reference.size:
            continue
        high_values = extrema[:, 2] if extrema.size else reference[:, 2]
        timestamp = dt.datetime.fromtimestamp(
            int(minute) * 60, UTC
        ).astimezone(EASTERN)
        output.append(
            (
                timestamp,
                float(reference[0, 2]),
                float(np.max(high_values)),
                float(np.min(high_values)),
                float(reference[-1, 2]),
                float(np.sum(last[:, 3])) if last.size else 0.0,
            )
        )
    return output


def render_chart(
    path: Path,
    *,
    ticker: str,
    bars: Sequence[tuple[dt.datetime, float, float, float, float, float]],
    published_at: dt.datetime,
) -> None:
    figure, (price_axis, volume_axis) = plt.subplots(
        2,
        1,
        figsize=(15, 8),
        sharex=True,
        constrained_layout=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
    )
    if bars:
        times = np.asarray([mdates.date2num(value[0]) for value in bars])
        width = 0.00045
        for x, (_, open_, high, low, close, volume) in zip(
            times, bars, strict=True
        ):
            color = "#00a884" if close >= open_ else "#ff3b5c"
            price_axis.vlines(x, low, high, color=color, linewidth=0.55)
            price_axis.add_patch(
                plt.Rectangle(
                    (x - width / 2, min(open_, close)),
                    width,
                    max(abs(close - open_), 1e-8),
                    facecolor=color,
                    edgecolor=color,
                )
            )
            volume_axis.bar(x, volume, width=width, color=color, alpha=0.65)
        marker = mdates.date2num(published_at.astimezone(EASTERN))
        price_axis.axvline(marker, color="#7c3aed", linewidth=1.2)
        volume_axis.axvline(marker, color="#7c3aed", linewidth=1.2)
    price_axis.set_title(f"{ticker} exact-event one-minute price path")
    price_axis.set_ylabel("Price")
    volume_axis.set_ylabel("Volume")
    volume_axis.xaxis.set_major_formatter(
        mdates.DateFormatter("%b %d\n%H:%M", tz=EASTERN)
    )
    for axis in (price_axis, volume_axis):
        axis.grid(True, linewidth=0.35, alpha=0.4)
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def parse_utc(value: Any) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def date_contains(mapping: dict[str, Any], day: dt.date) -> bool:
    start = str(mapping.get("valid_from_date") or "")
    end = str(mapping.get("valid_to_date_exclusive") or "")
    return (
        (not start or dt.date.fromisoformat(start) <= day)
        and (not end or day < dt.date.fromisoformat(end))
    )


def json_rows(value: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in value.splitlines() if line.strip()]


def assert_runtime_root(path: Path) -> None:
    resolved = path.resolve()
    required = MLOpsPathConfig.from_env().runtimes_root.resolve()
    if required not in (resolved, *resolved.parents):
        raise RuntimeError(
            f"generated output must be under {required}, received {resolved}"
        )


def write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_jsonl_atomic(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
            )
    os.replace(temporary, path)


def slug(value: Any) -> str:
    return "".join(
        character.lower() if character.isalnum() else "_"
        for character in str(value)
    ).strip("_")


def escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def fence(value: str) -> str:
    marker = "````" if "```" in value else "```"
    return f"{marker}text\n{value}\n{marker}"
