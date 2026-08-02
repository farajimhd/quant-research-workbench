from __future__ import annotations

import html
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .comparison import (
    CollectionItem,
    _human_by_ticker,
    _majority,
    _prediction_by_ticker,
    canonical_concept_family,
)
from .schema import stable_json_hash
from .storage import assert_runtime_root, read_json, write_json_atomic


AUDIT_CONTRACT = "news_fresh_acceptance_article_audit_v1"
ARTICLE_FIELDS = (
    ("Extraction decision", "extraction_decision"),
    ("Content role", "content_role"),
    ("Source origin", "source_origin"),
)
UNIT_FIELDS = (
    ("Direction", "semantic_direction"),
    ("Concept families", "event_concepts"),
    ("Forecast eligible", "forecast_trigger_eligible"),
    ("Reaction-study eligible", "reaction_evaluation_eligible"),
    ("Issuer-history eligible", "issuer_history_context_eligible"),
)


@dataclass(frozen=True, slots=True)
class ArticleAudit:
    sample_id: str
    title: str
    file_name: str
    v9_mismatches: int
    v10_mismatches: int
    comparison_cells: int
    markdown: str


def render_acceptance_audits(
    items: Sequence[CollectionItem],
    *,
    v9_prediction_dir: Path,
    v10_prediction_dir: Path,
    output_root: Path,
    evaluation_path: Path,
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
        v9 = read_json(v9_prediction_dir / f"{item.sample_id}.json")
        v10 = read_json(v10_prediction_dir / f"{item.sample_id}.json")
        articles.append(render_article_audit(item, v9=v9, v10=v10))
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
) -> ArticleAudit:
    human = item.truth
    publication = item.blinded.get("publication") or {}
    title = str(publication.get("title") or "Untitled article")
    human_units = _human_by_ticker(human)
    v9_units = _prediction_by_ticker(v9)
    v10_units = _prediction_by_ticker(v10)
    article_rows: list[tuple[str, Any, Any, Any]] = []
    v9_mismatches = 0
    v10_mismatches = 0
    for label, field in ARTICLE_FIELDS:
        truth_value = human.get(field) or "not set"
        v9_value = _article_prediction(v9, field)
        v10_value = _article_prediction(v10, field)
        article_rows.append((label, truth_value, v9_value, v10_value))
        v9_mismatches += truth_value != v9_value
        v10_mismatches += truth_value != v10_value

    human_tickers = set(human_units)
    v9_tickers = set(v9_units)
    v10_tickers = set(v10_units)
    article_rows.append(
        (
            "Issuer ticker set",
            sorted(human_tickers),
            sorted(v9_tickers),
            sorted(v10_tickers),
        )
    )
    v9_mismatches += human_tickers != v9_tickers
    v10_mismatches += human_tickers != v10_tickers

    unit_rows: list[tuple[str, str, Any, Any, bool, Any, bool]] = []
    all_tickers = sorted(human_tickers | v9_tickers | v10_tickers)
    for ticker in all_tickers:
        human_present = ticker in human_units
        v9_present = ticker in v9_units
        v10_present = ticker in v10_units
        truth = _normalized_unit(
            human_units.get(ticker), missing_direction="not an issuer unit"
        )
        v9_unit = _normalized_unit(v9_units.get(ticker))
        v10_unit = _normalized_unit(v10_units.get(ticker))
        for label, field in UNIT_FIELDS:
            truth_value = truth[field]
            v9_value = v9_unit[field]
            v10_value = v10_unit[field]
            v9_match = _unit_field_matches(
                human_present, v9_present, truth_value, v9_value
            )
            v10_match = _unit_field_matches(
                human_present, v10_present, truth_value, v10_value
            )
            unit_rows.append(
                (ticker, label, truth_value, v9_value, v9_match, v10_value, v10_match)
            )
            v9_mismatches += not v9_match
            v10_mismatches += not v10_match

    comparison_cells = len(article_rows) + len(unit_rows)
    file_name = f"{item.sample_id}_{_slug(title)}.md"
    body = [
        f"# {item.sample_id} - {title}",
        "",
        "## Audit summary",
        "",
        f"- **Source ID:** `{item.blinded['source_id']}`",
        f"- **Published:** `{item.blinded['source_timestamp']}`",
        f"- **Provider tickers:** {_display(publication.get('provider_tickers') or [])}",
        f"- **Channels:** {_display(publication.get('channels') or [])}",
        f"- **V9 mismatched comparison cells:** **{v9_mismatches} / {comparison_cells}**",
        f"- **V10 mismatched comparison cells:** **{v10_mismatches} / {comparison_cells}**",
        "",
        "`MATCH` and `DIFF` compare each prediction with the frozen human label. "
        "Concepts are compared at the same canonical family level used by the benchmark.",
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
        f"<pre>{html.escape(str((item.blinded.get('rendered_product') or {}).get('text') or ''))}</pre>",
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
        markdown="\n".join(body),
    )


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
        "A mismatch cell is stricter than the benchmark's earlier error-article "
        "summary: it includes article classification, ticker-set, direction, "
        "concept-family and all eligibility differences, including every extra "
        "or missing issuer unit.",
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
        ("Concept-family F1", "concept_family_f1"),
        ("Forecast eligibility F1", "forecast_f1"),
        ("Issuer-history eligibility F1", "history_f1"),
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
            "| Article | V9 mismatch cells | V10 mismatch cells | Compared cells |",
            "|---|---:|---:|---:|",
        ]
    )
    lines.extend(_index_rows(row for row in articles if row.v9_mismatches > 0))
    lines.extend(
        [
            "",
            "## V10 mismatches",
            "",
            "| Article | V9 mismatch cells | V10 mismatch cells | Compared cells |",
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
            "| Article | V9 mismatch cells | V10 mismatch cells | Compared cells |",
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
            f"| [{label}](articles/{row.file_name}) | {row.v9_mismatches} | "
            f"{row.v10_mismatches} | {row.comparison_cells} |"
        )
    return lines


def _article_prediction(prediction: Mapping[str, Any], field: str) -> str:
    explicit = str(prediction.get(field) or "")
    if explicit:
        return explicit
    labels = prediction.get("labels") or ()
    if field == "extraction_decision":
        return "labeled" if labels else "not labeled"
    return _majority(
        str((label.get("classification") or {}).get(field) or "") for label in labels
    )


def _normalized_unit(
    unit: Mapping[str, Any] | None, *, missing_direction: str = "not predicted"
) -> dict[str, Any]:
    if unit is None:
        return {
            "semantic_direction": missing_direction,
            "event_concepts": (),
            "forecast_trigger_eligible": False,
            "reaction_evaluation_eligible": False,
            "issuer_history_context_eligible": False,
        }
    return {
        "semantic_direction": str(unit.get("semantic_direction") or "neutral"),
        "event_concepts": tuple(
            sorted(
                {
                    projected
                    for concept in unit.get("event_concepts") or ()
                    if (projected := canonical_concept_family(str(concept)))
                }
            )
        ),
        "forecast_trigger_eligible": bool(unit.get("forecast_trigger_eligible")),
        "reaction_evaluation_eligible": bool(unit.get("reaction_evaluation_eligible")),
        "issuer_history_context_eligible": bool(
            unit.get("issuer_history_context_eligible")
        ),
    }


def _comparison_table(rows: Iterable[tuple[str, Any, Any, Any]]) -> str:
    output = [
        "| Dimension | Human gold | V9 | V9 result | V10 | V10 result |",
        "|---|---|---|---|---|---|",
    ]
    for label, truth, v9, v10 in rows:
        output.append(
            f"| {_escape_table(label)} | {_display(truth)} | {_display(v9)} | "
            f"{_outcome(truth, v9)} | {_display(v10)} | {_outcome(truth, v10)} |"
        )
    return "\n".join(output)


def _unit_comparison_table(
    rows: Iterable[tuple[str, str, Any, Any, bool, Any, bool]]
) -> str:
    output = [
        "| Ticker | Dimension | Human gold | V9 | V9 result | V10 | V10 result |",
        "|---|---|---|---|---|---|---|",
    ]
    for ticker, label, truth, v9, v9_match, v10, v10_match in rows:
        output.append(
            f"| `{_escape_table(ticker)}` | {_escape_table(label)} | {_display(truth)} | "
            f"{_display(v9)} | {_match_outcome(v9_match)} | {_display(v10)} | "
            f"{_match_outcome(v10_match)} |"
        )
    if len(output) == 2:
        output.append("| - | No issuer-level units | - | - | MATCH | - | MATCH |")
    return "\n".join(output)


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
    lines = [
        f"- **Article evidence:** {_display(prediction.get('evidence') or [])}",
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


def _display(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        return "<br>".join(_escape_table(item) for item in values) if values else "none"
    return _escape_table(value if value not in (None, "") else "none")


def _outcome(truth: Any, predicted: Any) -> str:
    return _match_outcome(truth == predicted)


def _match_outcome(matches: bool) -> str:
    return "**MATCH**" if matches else "**DIFF**"


def _unit_field_matches(
    human_present: bool,
    prediction_present: bool,
    truth_value: Any,
    predicted_value: Any,
) -> bool:
    if not human_present:
        return not prediction_present
    return prediction_present and truth_value == predicted_value


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
