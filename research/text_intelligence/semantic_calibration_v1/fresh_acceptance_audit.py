from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pipelines.news.benzinga.core.clickhouse_writer import NORMALIZED_COLUMNS
from pipelines.news.benzinga.core.content_quality import sanitize_packed_news_text
from research.mlops.clickhouse import ClickHouseHttpClient, sql_string

from .comparison import (
    CollectionItem,
    IssuerComparison,
    _human_by_ticker,
    _prediction_by_ticker,
    compare_article_fields,
    compare_issuer_units,
)
from .schema import stable_json_hash
from .storage import assert_runtime_root, read_json, write_json_atomic


AUDIT_CONTRACT = "news_fresh_acceptance_article_audit_v5"
GATEWAY_TEXT_FIELDS = (
    "body_text",
    "external_text",
    "pdf_text",
    "normalized_full_text",
)
ARTICLE_FIELD_LABELS = {
    "extraction_presence": "Issuer labels emitted",
    "extraction_decision": "Extraction decision",
    "content_role": "Content role",
    "source_origin": "Source origin",
}
UNIT_FIELD_LABELS = {
    "issuer_presence": "Issuer unit present",
    "semantic_direction": "Text sentiment",
    "forecast_direction": "Forecast direction",
    "event_concepts": "Concept families",
    "forecast_trigger_eligible": "Forecast eligible",
    "reaction_evaluation_eligible": "Reaction-study eligible",
    "issuer_history_context_eligible": "Issuer-history eligible",
}


@dataclass(frozen=True, slots=True)
class ArticleAudit:
    sample_id: str
    title: str
    file_name: str
    v9_mismatches: int
    v10_mismatches: int
    comparison_cells: int
    v9_scored_cells: int
    v10_scored_cells: int
    markdown: str


@dataclass(frozen=True, slots=True)
class GatewaySourceEvidence:
    retained_record: Mapping[str, Any]
    raw_payload: Mapping[str, Any]
    resolved_raw_artifact_path: str
    retained_payload_hash: str
    raw_artifact_byte_hash: str
    hash_verification_method: str


def load_gateway_source_evidence(
    client: ClickHouseHttpClient,
    items: Sequence[CollectionItem],
    *,
    raw_path_maps: Sequence[tuple[str, str]] = (),
) -> dict[str, GatewaySourceEvidence]:
    """Load and verify the exact News Gateway source authority for each item."""
    expected = {
        str(item.blinded["source_id"]): str(item.blinded["source_timestamp"])[:10]
        for item in items
    }
    pairs = ",".join(
        f"({sql_string(date)}, {sql_string(source_id)})"
        for source_id, date in sorted(expected.items())
    )
    sql = f"""
SELECT *
FROM q_live.benzinga_news_normalized_v1 FINAL
WHERE (published_date, canonical_news_id) IN ({pairs})
FORMAT JSONEachRow
"""
    records = {
        str(row["canonical_news_id"]): row
        for row in _json_each_rows(client.execute(sql))
    }
    missing = sorted(set(expected) - set(records))
    if missing:
        raise RuntimeError(
            f"News Gateway retained record missing for {len(missing)} audits; "
            f"first={missing[:5]}"
        )
    output: dict[str, GatewaySourceEvidence] = {}
    for source_id, record in records.items():
        missing_columns = sorted(set(NORMALIZED_COLUMNS) - set(record))
        if missing_columns:
            raise RuntimeError(
                f"News Gateway retained record is incomplete for {source_id}: "
                f"missing={missing_columns}"
            )
        if str(record.get("published_date") or "") != expected[source_id]:
            raise RuntimeError(f"News Gateway publication-date mismatch: {source_id}")
        raw_path = _resolve_raw_artifact_path(
            str(record.get("raw_artifact_path") or ""), raw_path_maps
        )
        if raw_path is None:
            raise RuntimeError(
                "News Gateway raw provider artifact is unavailable for "
                f"{source_id}: {record.get('raw_artifact_path') or '<empty>'}"
            )
        raw_bytes = raw_path.read_bytes()
        raw_payload = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(raw_payload, dict):
            raise RuntimeError(f"raw provider payload is not an object: {raw_path}")
        retained_hash = str(record.get("raw_payload_hash") or "")
        raw_byte_hash = _raw_artifact_hash(raw_bytes)
        verification_method = _payload_hash_verification_method(
            raw_payload,
            retained_hash=retained_hash,
            raw_artifact_byte_hash=raw_byte_hash,
        )
        if verification_method is None:
            raise RuntimeError(
                f"raw provider payload hash mismatch for {source_id}: "
                f"retained={retained_hash} artifact_bytes={raw_byte_hash}"
            )
        if _raw_provider_article_id(raw_payload) != str(
            record.get("provider_article_id") or ""
        ):
            raise RuntimeError(f"raw provider article identity mismatch: {source_id}")
        output[source_id] = GatewaySourceEvidence(
            retained_record=record,
            raw_payload=raw_payload,
            resolved_raw_artifact_path=str(raw_path),
            retained_payload_hash=retained_hash,
            raw_artifact_byte_hash=raw_byte_hash,
            hash_verification_method=verification_method,
        )
    return output


def render_acceptance_audits(
    items: Sequence[CollectionItem],
    *,
    v9_prediction_dir: Path,
    v10_prediction_dir: Path,
    output_root: Path,
    evaluation_path: Path,
    gateway_evidence: Mapping[str, GatewaySourceEvidence],
) -> dict[str, Any]:
    assert_runtime_root(output_root)
    evaluation = read_json(evaluation_path)
    if len(items) != 100 or int(evaluation.get("articles") or 0) != 100:
        raise RuntimeError("fresh acceptance audit requires exactly 100 articles")
    articles: list[ArticleAudit] = []
    source_ids: set[str] = set()
    for item in items:
        if item.blinded["source_id"] in source_ids:
            raise RuntimeError(f"duplicate source identity: {item.blinded['source_id']}")
        source_ids.add(str(item.blinded["source_id"]))
        source_id = str(item.blinded["source_id"])
        evidence = gateway_evidence.get(source_id)
        if evidence is None:
            raise RuntimeError(f"missing News Gateway source evidence: {source_id}")
        v9 = read_json(v9_prediction_dir / f"{item.sample_id}.json")
        v10 = read_json(v10_prediction_dir / f"{item.sample_id}.json")
        articles.append(
            render_article_audit(item, v9=v9, v10=v10, gateway_evidence=evidence)
        )
    if len(articles) != 100:
        raise RuntimeError("fresh acceptance audit did not render 100 unique articles")

    article_root = output_root / "articles"
    expected_files = {article.file_name for article in articles}
    if article_root.exists():
        unexpected = {
            path.name for path in article_root.glob("*.md") if path.name not in expected_files
        }
        if unexpected:
            raise RuntimeError(
                "existing audit output contains stale article files: "
                + ", ".join(sorted(unexpected)[:5])
            )
    for article in articles:
        _write_text_atomic(article_root / article.file_name, article.markdown)

    ordered = sorted(
        articles,
        key=lambda row: (-max(row.v9_mismatches, row.v10_mismatches), row.sample_id),
    )
    index = render_index(ordered, evaluation=evaluation)
    _write_text_atomic(output_root / "INDEX.md", index)
    manifest = {
        "contract": AUDIT_CONTRACT,
        "article_count": len(articles),
        "source_count": len(source_ids),
        "gateway_source_count": len(gateway_evidence),
        "raw_provider_payload_count": len(gateway_evidence),
        "raw_provider_payloads_verified": True,
        "gateway_hash_verification_methods": dict(
            sorted(
                Counter(
                    value.hash_verification_method
                    for value in gateway_evidence.values()
                ).items()
            )
        ),
        "gateway_retained_column_count_min": min(
            len(value.retained_record) for value in gateway_evidence.values()
        ),
        "gateway_retained_column_count_max": max(
            len(value.retained_record) for value in gateway_evidence.values()
        ),
        "evaluation_report_sha256": str(
            evaluation.get("v9", {}).get("report_sha256") or ""
        )
        + ":"
        + str(evaluation.get("v10", {}).get("report_sha256") or ""),
        "articles_with_any_v9_mismatch": sum(row.v9_mismatches > 0 for row in articles),
        "articles_with_any_v10_mismatch": sum(row.v10_mismatches > 0 for row in articles),
        "v9_mismatch_cells": sum(row.v9_mismatches for row in articles),
        "v10_mismatch_cells": sum(row.v10_mismatches for row in articles),
        "items": [
            {
                "sample_id": row.sample_id,
                "file_name": row.file_name,
                "v9_mismatches": row.v9_mismatches,
                "v10_mismatches": row.v10_mismatches,
                "comparison_cells": row.comparison_cells,
                "v9_scored_cells": row.v9_scored_cells,
                "v10_scored_cells": row.v10_scored_cells,
                "markdown_sha256": stable_json_hash(row.markdown),
            }
            for row in sorted(articles, key=lambda row: row.sample_id)
        ],
    }
    manifest["manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(output_root / "audit_manifest.json", manifest)
    return manifest


def render_article_audit(
    item: CollectionItem,
    *,
    v9: Mapping[str, Any],
    v10: Mapping[str, Any],
    gateway_evidence: GatewaySourceEvidence,
) -> ArticleAudit:
    human = item.truth
    publication = item.blinded.get("publication") or {}
    title = str(publication.get("title") or "Untitled article")
    human_units = _human_by_ticker(human)
    v9_units = _prediction_by_ticker(v9)
    v10_units = _prediction_by_ticker(v10)
    v9_article = {
        comparison.dimension: comparison
        for comparison in compare_article_fields(human, v9)
    }
    v10_article = {
        comparison.dimension: comparison
        for comparison in compare_article_fields(human, v10)
    }
    article_rows = [
        (label, v9_article[dimension], v10_article[dimension])
        for dimension, label in ARTICLE_FIELD_LABELS.items()
    ]
    v9_mismatches = sum(row.status == "diff" for row in v9_article.values())
    v10_mismatches = sum(row.status == "diff" for row in v10_article.values())

    human_tickers = set(human_units)
    v9_tickers = set(v9_units)
    v10_tickers = set(v10_units)
    all_tickers = sorted(human_tickers | v9_tickers | v10_tickers)
    v9_comparisons = {
        (comparison.ticker, comparison.dimension): comparison
        for comparison in compare_issuer_units(
            human_units,
            v9_units,
            canonical_concepts=True,
            ticker_universe=all_tickers,
        )
    }
    v10_comparisons = {
        (comparison.ticker, comparison.dimension): comparison
        for comparison in compare_issuer_units(
            human_units,
            v10_units,
            canonical_concepts=True,
            ticker_universe=all_tickers,
        )
    }
    unit_rows: list[tuple[str, str, IssuerComparison, IssuerComparison]] = []
    for ticker in all_tickers:
        for dimension, label in UNIT_FIELD_LABELS.items():
            key = (ticker, dimension)
            unit_rows.append((ticker, label, v9_comparisons[key], v10_comparisons[key]))

    v9_mismatches += sum(
        comparison.status == "diff" for comparison in v9_comparisons.values()
    )
    v10_mismatches += sum(
        comparison.status == "diff" for comparison in v10_comparisons.values()
    )
    v9_scored_cells = len(article_rows) + sum(
        comparison.scored for comparison in v9_comparisons.values()
    )
    v10_scored_cells = len(article_rows) + sum(
        comparison.scored for comparison in v10_comparisons.values()
    )
    comparison_cells = len(article_rows) + len(unit_rows)
    file_name = f"{item.sample_id}_{_slug(title)}.md"
    body = [
        f"# {item.sample_id} - {title}",
        "",
        "## Original provider payload downloaded by News Gateway",
        "",
        "This is the exact provider JSON loaded from the gateway's retained raw "
        "artifact. It is shown before every normalized or derived product. The "
        "stored payload hash was verified against its recorded historical "
        "serialization contract before this audit was written.",
        "",
        _raw_provider_payload_section(gateway_evidence),
        "",
        "## News Gateway download provenance",
        "",
        _gateway_provenance_section(gateway_evidence),
        "",
        "## Complete News Gateway retained record",
        "",
        "This is the complete 42-column `benzinga_news_normalized_v1` row. Large "
        "retained text fields are removed only from the metadata JSON and reproduced "
        "verbatim immediately afterward; no retained column is omitted.",
        "",
        _gateway_retained_record_section(gateway_evidence.retained_record),
        "",
        "## Derived frozen calibration metadata",
        "",
        "This downstream record was created for blinded calibration review; it is "
        "not original provider metadata. Only its large text "
        "payloads are removed from this JSON and reproduced verbatim in the next "
        "section so metadata and source text are not duplicated or truncated.",
        "",
        _metadata_section(item.blinded),
        "",
        "## Original news texts",
        "",
        "These are all source-lane texts preserved in the frozen acceptance item, "
        "in source ordinal order. They are shown before human or model labels so "
        "the article can be reviewed from source evidence first.",
        "",
        _source_text_sections(item.blinded),
        "",
        "## Audit summary",
        "",
        f"- **Source ID:** `{item.blinded['source_id']}`",
        f"- **Published:** `{item.blinded['source_timestamp']}`",
        f"- **Provider tickers:** {_display(publication.get('provider_tickers') or [])}",
        f"- **Channels:** {_display(publication.get('channels') or [])}",
        f"- **V9 evaluator mismatches:** **{v9_mismatches} / {v9_scored_cells} scored fields**",
        f"- **V10 evaluator mismatches:** **{v10_mismatches} / {v10_scored_cells} scored fields**",
        "",
        "Every issuer result below is emitted by the same comparison authority used "
        "by aggregate evaluation. Categorical fields report `MATCH` or `DIFF`; "
        "binary eligibility reports `TP`, `TN`, `FP`, or `FN`; concepts report set "
        "`TP/FP/FN`; and `NOT SCORED` includes the evaluator's dependency reason. "
        "In particular, forecast direction is not scored when the human forecast "
        "eligibility label is off. It is not treated as a third direction class.",
        "",
        "## Article-level labels",
        "",
        _comparison_table(article_rows),
        "",
        "## Issuer-level labels",
        "",
        _unit_comparison_table(unit_rows),
        "",
        "## Human evidence and rationale",
        "",
        _human_evidence(human),
        "",
        "## V9 deterministic rule trace",
        "",
        "V9 does not have a private model reasoning chain. The following is its "
        "persisted, inspectable rule evidence and numeric score trace.",
        "",
        _v9_trace(v9),
        "",
        "## V10 recorded model output",
        "",
        "V10 is a TF-IDF/SVD random-forest model. Its persisted output contains "
        "predictions and direction confidence, but no faithful natural-language "
        "reasoning chain; none is invented here.",
        "",
        _v10_trace(v10),
        "",
        "## Rendered article used for review",
        "",
        "<details open><summary>Full rendered text</summary>",
        "",
        _rendered_review_text(item.blinded),
        "",
        "</details>",
        "",
    ]
    return ArticleAudit(
        sample_id=item.sample_id,
        title=title,
        file_name=file_name,
        v9_mismatches=v9_mismatches,
        v10_mismatches=v10_mismatches,
        comparison_cells=comparison_cells,
        v9_scored_cells=v9_scored_cells,
        v10_scored_cells=v10_scored_cells,
        markdown="\n".join(body),
    )


def _rendered_review_text(blinded: Mapping[str, Any]) -> str:
    original = str((blinded.get("rendered_product") or {}).get("text") or "")
    sanitized, rejected = sanitize_packed_news_text(original)
    note = ""
    if rejected:
        note = (
            "<p><strong>Current renderer:</strong> rejected an external transport "
            f"artifact ({html.escape(', '.join(rejected))}). The exact retained "
            "legacy field remains visible in the provenance section above.</p>\n\n"
        )
    return f"{note}<pre>{html.escape(sanitized)}</pre>"


def render_index(
    articles: Sequence[ArticleAudit], *, evaluation: Mapping[str, Any]
) -> str:
    headline = evaluation["headline"]
    lines = [
        "# Fresh 100 News audit index",
        "",
        "Every article was manually labeled before V9 or V10 predictions were "
        "generated. Files are ordered by the larger model mismatch count so the "
        "most useful error audits appear first.",
        "",
            "Audit mismatches come from the same issuer-comparison authority as the "
            "aggregate benchmark. The denominator is model-specific because "
            "dependency-gated fields can be explicitly NOT SCORED.",
        "",
        "## Benchmark headline",
        "",
        "| Metric | V9 | V10 | V10 - V9 |",
        "|---|---:|---:|---:|",
    ]
    metric_names = (
        ("Extraction F1", "extraction_f1"),
        ("Ticker-scope F1", "ticker_scope_f1"),
        ("Content-role macro F1", "content_role_macro_f1"),
        ("Source-origin macro F1", "source_origin_macro_f1"),
        ("Direction macro F1", "direction_macro_f1"),
        ("Forecast-direction macro F1", "forecast_direction_macro_f1"),
        ("Concept-family F1", "concept_family_f1"),
        ("Forecast eligibility F1 (human issuers)", "forecast_f1"),
        ("Forecast eligibility F1 (end to end)", "forecast_end_to_end_f1"),
        ("Reaction eligibility F1 (end to end)", "reaction_end_to_end_f1"),
        ("Issuer-history eligibility F1 (human issuers)", "history_f1"),
        ("Issuer-history eligibility F1 (end to end)", "history_end_to_end_f1"),
    )
    for label, key in metric_names:
        lines.append(
            f"| {label} | {headline['v9'][key]:.3f} | "
            f"{headline['v10'][key]:.3f} | "
            f"{headline['delta_v10_minus_v9'][key]:+.3f} |"
        )
    lines.extend(
        [
            "",
            "## V9 mismatches",
            "",
            "| Article | V9 errors / scored | V10 errors / scored | Displayed fields |",
            "|---|---:|---:|---:|",
        ]
    )
    lines.extend(_index_rows(row for row in articles if row.v9_mismatches > 0))
    lines.extend(
        [
            "",
            "## V10 mismatches",
            "",
            "| Article | V9 errors / scored | V10 errors / scored | Displayed fields |",
            "|---|---:|---:|---:|",
        ]
    )
    lines.extend(
        _index_rows(
            sorted(
                (row for row in articles if row.v10_mismatches > 0),
                key=lambda row: (-row.v10_mismatches, row.sample_id),
            )
        )
    )
    lines.extend(
        [
            "",
            "## All 100 articles",
            "",
            "| Article | V9 errors / scored | V10 errors / scored | Displayed fields |",
            "|---|---:|---:|---:|",
        ]
    )
    lines.extend(_index_rows(articles))
    lines.append("")
    return "\n".join(lines)


def _index_rows(articles: Iterable[ArticleAudit]) -> list[str]:
    lines: list[str] = []
    for row in articles:
        label = _escape_table(f"{row.sample_id} - {row.title}")
        lines.append(
            f"| [{label}](articles/{row.file_name}) | "
            f"{row.v9_mismatches} / {row.v9_scored_cells} | "
            f"{row.v10_mismatches} / {row.v10_scored_cells} | "
            f"{row.comparison_cells} |"
        )
    return lines


def _comparison_table(
    rows: Iterable[tuple[str, IssuerComparison, IssuerComparison]]
) -> str:
    output = [
        "| Dimension | Human gold | V9 | V9 result | V10 | V10 result |",
        "|---|---|---|---|---|---|",
    ]
    for label, v9, v10 in rows:
        truth = v9.actual
        output.append(
            f"| {_escape_table(label)} | {_display(truth)} | "
            f"{_display(v9.predicted)} | {_evaluator_outcome(v9)} | "
            f"{_display(v10.predicted)} | {_evaluator_outcome(v10)} |"
        )
    return "\n".join(output)


def _unit_comparison_table(
    rows: Iterable[tuple[str, str, IssuerComparison, IssuerComparison]]
) -> str:
    output = [
        "| Ticker | Dimension | Human gold | V9 | V9 result | V10 | V10 result |",
        "|---|---|---|---|---|---|---|",
    ]
    row_count = 0
    for ticker, label, v9, v10 in rows:
        row_count += 1
        truth = v9.actual if v9.actual is not None else v10.actual
        output.append(
            f"| `{_escape_table(ticker)}` | {_escape_table(label)} | "
            f"{_display(truth) if truth is not None else 'not an issuer unit'} | "
            f"{_display(v9.predicted)} | {_evaluator_outcome(v9)} | "
            f"{_display(v10.predicted)} | {_evaluator_outcome(v10)} |"
        )
    if row_count == 0:
        output.append(
            "| - | No issuer-level units | none | none | NOT SCORED | none | NOT SCORED |"
        )
    return "\n".join(output)


def _evaluator_outcome(comparison: IssuerComparison) -> str:
    if comparison.status == "not_scored":
        return (
            "**NOT SCORED**<br>"
            f"<small>{_escape_table(comparison.reason)}</small>"
        )
    status = "MATCH" if comparison.matches else "DIFF"
    metric = ", ".join(comparison.metrics) or "no aggregate metric"
    return (
        f"**{_escape_table(comparison.category)} - {status}**<br>"
        f"<small>{_escape_table(comparison.reason)}; "
        f"metric: {_escape_table(metric)}</small>"
    )


def _human_evidence(human: Mapping[str, Any]) -> str:
    units = human.get("issuer_units") or ()
    if not units:
        return (
            f"- **Review decision:** `{human.get('extraction_decision')}`\n"
            f"- **Review notes:** {_escape_text(human.get('review_notes') or '')}"
        )
    lines: list[str] = []
    for unit in units:
        lines.extend(
            [
                f"### {unit['ticker']}",
                "",
                f"- **Issuer role:** `{unit.get('issuer_role')}`",
                f"- **Direction rationale:** {_escape_text(unit.get('semantic_rationale') or '')}",
                f"- **Eligibility rationale:** {_escape_text(unit.get('eligibility_reason') or '')}",
                f"- **Positive / negative evidence:** {unit.get('positive_evidence_level', 0)} / {unit.get('negative_evidence_level', 0)}",
                "- **Evidence quotes:**",
            ]
        )
        quotes = unit.get("evidence_quotes") or ()
        lines.extend(f"  - {_escape_text(quote)}" for quote in quotes)
        lines.append("")
    return "\n".join(lines).rstrip()


def _v9_trace(prediction: Mapping[str, Any]) -> str:
    identity = prediction.get("identity_resolution") or {}
    lines = [
        f"- **Article evidence:** {_display(prediction.get('evidence') or [])}",
        f"- **Issuer authority:** `{identity.get('authority_version') or 'not recorded'}`",
        f"- **Resolved text subjects:** {_display(identity.get('resolved_subjects') or [])}",
        f"- **Point-in-time issuer facts:** {_display(identity.get('point_in_time_candidates') or [])}",
        "",
    ]
    labels = prediction.get("labels") or ()
    if not labels:
        lines.append("No V9 issuer label was emitted.")
        return "\n".join(lines)
    for index, label in enumerate(labels, 1):
        classification = label.get("classification") or {}
        lines.extend(
            [
                f"### {label.get('ticker') or 'No ticker'} - rule unit {index}",
                "",
                f"- **Evidence scope / issuer role:** `{label.get('evidence_scope')}` / `{label.get('issuer_role')}`",
                f"- **Scoped evidence excerpt:** {_escape_text(_excerpt(label.get('semantic_evidence_text') or ''))}",
                f"- **Semantic score:** `{classification.get('semantic_score')}`",
                f"- **Raw / base / adjustment:** `{classification.get('semantic_score_raw')}` / `{classification.get('semantic_score_base')}` / `{classification.get('semantic_score_adjustment')}`",
                f"- **Direction confidence:** `{classification.get('direction_confidence')}`",
                f"- **Direction basis:** {_display(classification.get('semantic_direction_basis') or [])}",
                f"- **Weighted direction evidence:** {_display(classification.get('deterministic_direction_evidence') or [])}",
                f"- **Classification evidence:** {_display(classification.get('evidence') or [])}",
                f"- **Quality flags:** {_display(classification.get('quality_flags') or [])}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _v10_trace(prediction: Mapping[str, Any]) -> str:
    labels = prediction.get("labels") or ()
    if not labels:
        return "No V10 issuer label was emitted."
    lines: list[str] = []
    for label in labels:
        classification = label.get("classification") or {}
        lines.extend(
            [
                f"### {label.get('ticker') or 'No ticker'}",
                "",
                f"- **Direction / confidence:** `{classification.get('semantic_direction')}` / `{classification.get('direction_confidence')}`",
                f"- **Semantic score:** `{classification.get('semantic_score')}`",
                f"- **Concepts:** {_display(classification.get('event_concepts') or [])}",
                f"- **Forecast / reaction / history:** `{bool(label.get('forecast_trigger_eligible'))}` / `{bool(label.get('reaction_evaluation_eligible'))}` / `{bool(label.get('issuer_history_context_eligible'))}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _metadata_section(article: Mapping[str, Any]) -> str:
    payload = _metadata_payload(article)
    return "\n".join(
        [
            "<details open><summary>All metadata fields</summary>",
            "",
            f"<pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))}</pre>",
            "",
            "</details>",
        ]
    )


def _raw_provider_payload_section(evidence: GatewaySourceEvidence) -> str:
    return "\n".join(
        [
            "<details open><summary>Exact downloaded provider JSON</summary>",
            "",
            "<pre>"
            + html.escape(
                json.dumps(
                    evidence.raw_payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            + "</pre>",
            "",
            "</details>",
        ]
    )


def _gateway_provenance_section(evidence: GatewaySourceEvidence) -> str:
    record = evidence.retained_record
    return "\n".join(
        [
            f"- **Provider:** `{_escape_text(record.get('provider') or '')}`",
            f"- **Provider article ID:** `{_escape_text(record.get('provider_article_id') or '')}`",
            f"- **Canonical news ID:** `{_escape_text(record.get('canonical_news_id') or '')}`",
            f"- **Provider published value:** `{_escape_text(record.get('published_raw') or '')}`",
            f"- **Provider last-updated value:** `{_escape_text(record.get('last_updated_raw') or '')}`",
            f"- **Gateway downloaded at:** `{_escape_text(record.get('downloaded_at_utc') or '')}`",
            f"- **Provider delay (ns):** `{_escape_text(record.get('provider_delay_ns'))}`",
            f"- **Stored raw artifact path:** `{_escape_text(record.get('raw_artifact_path') or '')}`",
            f"- **Resolved audit read path:** `{_escape_text(evidence.resolved_raw_artifact_path)}`",
            f"- **Retained payload hash:** `{_escape_text(evidence.retained_payload_hash)}`",
            f"- **Hash verification method:** `{_escape_text(evidence.hash_verification_method)}`",
            f"- **Exact artifact-byte hash:** `{_escape_text(evidence.raw_artifact_byte_hash)}`",
        ]
    )


def _gateway_retained_record_section(record: Mapping[str, Any]) -> str:
    metadata = dict(record)
    text_fields = {name: metadata.pop(name, "") for name in GATEWAY_TEXT_FIELDS}
    lines = [
        "<details open><summary>All retained non-text fields</summary>",
        "",
        "<pre>"
        + html.escape(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True)
        )
        + "</pre>",
        "",
        "</details>",
    ]
    for field, value in text_fields.items():
        lines.extend(
            [
                "",
                f"<details open><summary>Retained field: {field}</summary>",
                "",
                f"<pre>{html.escape(str(value or ''))}</pre>",
                "",
                "</details>",
            ]
        )
    return "\n".join(lines)


def _resolve_raw_artifact_path(
    stored_path: str, raw_path_maps: Sequence[tuple[str, str]]
) -> Path | None:
    if not stored_path:
        return None
    direct = Path(stored_path)
    if direct.is_file():
        return direct
    comparable = stored_path.replace("/", "\\")
    for source, target in raw_path_maps:
        source_prefix = source.replace("/", "\\").rstrip("\\")
        if comparable.casefold() == source_prefix.casefold():
            candidate = Path(target)
        elif comparable.casefold().startswith(source_prefix.casefold() + "\\"):
            suffix = comparable[len(source_prefix) + 1 :]
            candidate = Path(target) / Path(suffix)
        else:
            continue
        if candidate.is_file():
            return candidate
    return None


def _provider_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return _raw_artifact_hash(canonical)


def _payload_hash_verification_method(
    payload: Mapping[str, Any],
    *,
    retained_hash: str,
    raw_artifact_byte_hash: str,
) -> str | None:
    candidates = (
        ("exact_utf8_artifact_bytes", raw_artifact_byte_hash),
        (
            "canonical_json_ascii_escaped",
            _raw_artifact_hash(
                json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
            ),
        ),
        ("canonical_json_utf8", _provider_payload_hash(payload)),
    )
    return next((method for method, value in candidates if value == retained_hash), None)


def _raw_artifact_hash(raw_bytes: bytes) -> str:
    return hashlib.blake2b(raw_bytes, digest_size=16).hexdigest()


def _raw_provider_article_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("benzinga_id", payload.get("id", ""))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def _json_each_rows(value: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in value.splitlines() if line.strip()]


def _metadata_payload(article: Mapping[str, Any]) -> dict[str, Any]:
    """Return every frozen field except separately rendered large text payloads."""
    payload = dict(article)
    source_lanes: list[dict[str, Any]] = []
    for lane in article.get("source_lanes") or ():
        lane_metadata = dict(lane)
        lane_metadata.pop("text", None)
        source_lanes.append(lane_metadata)
    payload["source_lanes"] = source_lanes
    rendered_product = dict(article.get("rendered_product") or {})
    rendered_product.pop("text", None)
    payload["rendered_product"] = rendered_product
    return payload


def _source_text_sections(article: Mapping[str, Any]) -> str:
    publication = article.get("publication") or {}
    sections: list[str] = [
        "### Publication text fields",
        "",
        "#### Title",
        "",
        f"<pre>{html.escape(str(publication.get('title') or ''))}</pre>",
        "",
        "#### Teaser",
        "",
        f"<pre>{html.escape(str(publication.get('teaser') or ''))}</pre>",
        "",
    ]
    lanes = sorted(
        article.get("source_lanes") or (),
        key=lambda lane: (
            int(lane.get("source_ordinal") or 0),
            str(lane.get("source_kind") or ""),
        ),
    )
    if not lanes:
        sections.extend(
            [
                "### Source body availability",
                "",
                "No separate original source-body lane is present in this frozen "
                "legacy article record. The title and teaser above are all original "
                "publication text fields available for this item; no body is invented.",
            ]
        )
        return "\n".join(sections).rstrip()
    for lane in lanes:
        source_kind = str(lane.get("source_kind") or "unknown")
        ordinal = int(lane.get("source_ordinal") or 0)
        source_url = str(lane.get("source_url") or "")
        sections.extend(
            [
                f"### Source `{source_kind}:{ordinal}`",
                "",
                f"- **URL:** {_escape_text(source_url) if source_url else 'none'}",
                f"- **Format:** `{_escape_text(lane.get('content_format') or 'unknown')}`",
                f"- **Source hash:** `{_escape_text(lane.get('source_hash') or '')}`",
                f"- **Stored characters:** `{int(lane.get('source_chars') or 0):,}`",
                "",
                "<details open><summary>Full original source text</summary>",
                "",
                f"<pre>{html.escape(str(lane.get('text') or ''))}</pre>",
                "",
                "</details>",
                "",
            ]
        )
    return "\n".join(sections).rstrip()


def _display(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        return "<br>".join(_escape_table(item) for item in values) if values else "none"
    return _escape_table(value if value not in (None, "") else "none")


def _escape_table(value: Any) -> str:
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")


def _escape_text(value: Any) -> str:
    return html.escape(str(value)).replace("\n", " ")


def _excerpt(value: Any, *, limit: int = 900) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (slug[:72].rstrip("-") or "untitled") + ".audit"


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.replace(temporary, path)
