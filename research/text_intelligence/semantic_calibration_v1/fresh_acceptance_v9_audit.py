from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .comparison import (
    CollectionItem,
    IssuerComparison,
    _human_by_ticker,
    _prediction_by_ticker,
    compare_article_fields,
    compare_issuer_units,
)
from .fresh_acceptance_audit import (
    ARTICLE_FIELD_LABELS,
    UNIT_FIELD_LABELS,
    GatewaySourceEvidence,
    _display,
    _gateway_provenance_section,
    _gateway_retained_record_section,
    _human_evidence,
    _metadata_section,
    _raw_provider_payload_section,
    _rendered_review_text,
    _slug,
    _source_text_sections,
    _v9_trace,
    _write_text_atomic,
)
from .schema import stable_json_hash
from .storage import assert_runtime_root, read_json, write_json_atomic


AUDIT_CONTRACT = "news_fresh_acceptance_v2_v9_article_audit_v1"


@dataclass(frozen=True, slots=True)
class V9ArticleAudit:
    sample_id: str
    title: str
    file_name: str
    mismatches: int
    scored_cells: int
    markdown: str


def render_v9_acceptance_audits(
    items: Sequence[CollectionItem],
    *,
    prediction_dir: Path,
    output_root: Path,
    evaluation_path: Path,
    gateway_evidence: Mapping[str, GatewaySourceEvidence],
) -> dict[str, Any]:
    assert_runtime_root(output_root)
    evaluation = read_json(evaluation_path)
    expected_count = len(items)
    if expected_count <= 0:
        raise RuntimeError("V9 acceptance audit requires at least one article")
    if int(evaluation.get("articles") or 0) != expected_count:
        raise RuntimeError(
            "V9 acceptance audit item/evaluation count mismatch: "
            f"items={expected_count} evaluation={evaluation.get('articles')}"
        )
    articles: list[V9ArticleAudit] = []
    for item in items:
        source_id = str(item.blinded["source_id"])
        evidence = gateway_evidence.get(source_id)
        if evidence is None:
            raise RuntimeError(f"missing News Gateway source evidence: {source_id}")
        prediction = read_json(prediction_dir / f"{item.sample_id}.json")
        articles.append(
            render_v9_article_audit(
                item,
                prediction=prediction,
                gateway_evidence=evidence,
            )
        )
    if len({row.sample_id for row in articles}) != expected_count:
        raise RuntimeError("V9 acceptance audit contains duplicate sample identities")

    article_root = output_root / "articles"
    expected = {row.file_name for row in articles}
    stale = (
        {path.name for path in article_root.glob("*.md")} - expected
        if article_root.exists()
        else set()
    )
    if stale:
        raise RuntimeError("stale article audits: " + ", ".join(sorted(stale)[:5]))
    for row in articles:
        _write_text_atomic(article_root / row.file_name, row.markdown)

    ordered = sorted(articles, key=lambda row: (-row.mismatches, row.sample_id))
    _write_text_atomic(output_root / "INDEX.md", _render_index(ordered, evaluation))
    manifest = {
        "contract": AUDIT_CONTRACT,
        "article_count": len(articles),
        "articles_with_any_v9_mismatch": sum(row.mismatches > 0 for row in articles),
        "v9_mismatch_cells": sum(row.mismatches for row in articles),
        "v9_scored_cells": sum(row.scored_cells for row in articles),
        "gateway_source_available": sum(
            value.source_authority_available for value in gateway_evidence.values()
        ),
        "gateway_source_unavailable": sum(
            not value.source_authority_available for value in gateway_evidence.values()
        ),
        "evaluation_report_sha256": str(
            (evaluation.get("v9") or {}).get("report_sha256") or ""
        ),
        "items": [
            {
                "sample_id": row.sample_id,
                "file_name": row.file_name,
                "v9_mismatches": row.mismatches,
                "v9_scored_cells": row.scored_cells,
                "markdown_sha256": stable_json_hash(row.markdown),
            }
            for row in sorted(articles, key=lambda value: value.sample_id)
        ],
    }
    manifest["manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(output_root / "audit_manifest.json", manifest)
    return manifest


def render_v9_article_audit(
    item: CollectionItem,
    *,
    prediction: Mapping[str, Any],
    gateway_evidence: GatewaySourceEvidence,
) -> V9ArticleAudit:
    truth = item.truth
    publication = item.blinded.get("publication") or {}
    title = str(publication.get("title") or "Untitled article")
    human_units = _human_by_ticker(truth)
    predicted_units = _prediction_by_ticker(prediction)
    article_comparisons = compare_article_fields(truth, prediction)
    tickers = sorted(set(human_units) | set(predicted_units))
    issuer_comparisons = compare_issuer_units(
        human_units,
        predicted_units,
        canonical_concepts=True,
        ticker_universe=tickers,
    )
    mismatches = sum(row.status == "diff" for row in article_comparisons)
    mismatches += sum(row.status == "diff" for row in issuer_comparisons)
    scored = sum(row.scored for row in article_comparisons)
    scored += sum(row.scored for row in issuer_comparisons)
    article_rows = [
        (ARTICLE_FIELD_LABELS[row.dimension], row) for row in article_comparisons
    ]
    issuer_by_key = {(row.ticker, row.dimension): row for row in issuer_comparisons}
    issuer_rows = [
        (ticker, UNIT_FIELD_LABELS[dimension], issuer_by_key[(ticker, dimension)])
        for ticker in tickers
        for dimension in UNIT_FIELD_LABELS
    ]
    body = [
        f"# {item.sample_id} - {title}",
        "",
        "## Original provider payload downloaded by News Gateway",
        "",
        _raw_provider_payload_section(gateway_evidence),
        "",
        "## News Gateway download provenance",
        "",
        _gateway_provenance_section(gateway_evidence),
        "",
        "## Complete News Gateway retained record",
        "",
        _gateway_retained_record_section(gateway_evidence.retained_record),
        "",
        "## Derived frozen calibration metadata",
        "",
        _metadata_section(item.blinded),
        "",
        "## Original news texts",
        "",
        _source_text_sections(item.blinded),
        "",
        "## Audit summary",
        "",
        f"- **Source ID:** `{item.blinded['source_id']}`",
        f"- **Published:** `{item.blinded['source_timestamp']}`",
        f"- **Provider tickers:** {_display(publication.get('provider_tickers') or [])}",
        f"- **Channels:** {_display(publication.get('channels') or [])}",
        f"- **V9 evaluator mismatches:** **{mismatches} / {scored} scored fields**",
        "",
        "The result column below is emitted by the same comparison authority as "
        "the aggregate evaluation. `NOT SCORED` is preserved with its dependency "
        "reason; it is never rewritten as a presentation-only match or difference.",
        "",
        "## Article-level labels",
        "",
        _article_table(article_rows),
        "",
        "## Issuer-level labels",
        "",
        _issuer_table(issuer_rows),
        "",
        "## Human evidence and rationale",
        "",
        _human_evidence(truth),
        "",
        "## V9 deterministic rule trace",
        "",
        "V9 has no hidden reasoning chain. This is its persisted rule evidence, "
        "identity trace, precedence trace, and numeric score trace.",
        "",
        _v9_trace(prediction),
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
    return V9ArticleAudit(
        sample_id=item.sample_id,
        title=title,
        file_name=f"{item.sample_id}_{_slug(title)}.md",
        mismatches=mismatches,
        scored_cells=scored,
        markdown="\n".join(body),
    )


def _article_table(rows: Sequence[tuple[str, IssuerComparison]]) -> str:
    lines = ["| Dimension | Human gold | V9 | V9 evaluator result |", "|---|---|---|---|"]
    lines.extend(
        f"| {_cell(label)} | {_cell(row.actual)} | {_cell(row.predicted)} | {_result(row)} |"
        for label, row in rows
    )
    return "\n".join(lines)


def _issuer_table(rows: Sequence[tuple[str, str, IssuerComparison]]) -> str:
    lines = [
        "| Ticker | Dimension | Human gold | V9 | V9 evaluator result |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {_cell(ticker)} | {_cell(label)} | {_cell(row.actual)} | "
        f"{_cell(row.predicted)} | {_result(row)} |"
        for ticker, label, row in rows
    )
    return "\n".join(lines)


def _result(row: IssuerComparison) -> str:
    if row.status == "not_scored":
        return f"**NOT SCORED**<br>{_cell(row.reason)}"
    label = "MATCH" if row.status == "match" else "DIFF"
    category = f" ({row.category})" if row.category else ""
    return f"**{label}{_cell(category)}**"


def _cell(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set, frozenset)):
        return "<br>".join(html.escape(str(item)) for item in value) or "none"
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")


def _render_index(
    articles: Sequence[V9ArticleAudit], evaluation: Mapping[str, Any]
) -> str:
    headline = (evaluation.get("headline") or {}).get("v9") or {}
    lines = [
        f"# Fresh {len(articles)} News V9 audit index",
        "",
        "All human labels were frozen before V9 predictions were generated. "
        "Articles are ordered by evaluator mismatch count.",
        "",
        f"- Articles: **{len(articles)}**",
        f"- V9 headline: `{html.escape(str(headline))}`",
        "",
        "| Sample | Mismatches | Scored fields | Article |",
        "|---|---:|---:|---|",
    ]
    lines.extend(
        f"| {row.sample_id} | {row.mismatches} | {row.scored_cells} | "
        f"[{html.escape(row.title)}](articles/{row.file_name}) |"
        for row in articles
    )
    return "\n".join(lines) + "\n"
