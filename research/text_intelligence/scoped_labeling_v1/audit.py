from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.paths import MLOpsPathConfig
from research.text_intelligence.classification_authority_v2.evaluation import (
    fetch_news_sample,
    fetch_sec_sample,
)
from research.text_intelligence.candidate_inventory_v1.config import (
    CandidateInventoryConfig,
)
from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .pipeline import (
    classify_news_document,
    classify_sec_document,
    summarize_scoped_labels,
)
from .schema import ScopedLabel
from .schema import SCOPED_LABELING_VERSION


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    output = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "scoped_labeling_v1"
        / "certification"
    )
    parser = argparse.ArgumentParser(
        description="Create and self-review scoped News and SEC audit files."
    )
    parser.add_argument("--candidate-sample-size", type=int, default=120)
    parser.add_argument("--news-audits", type=int, default=5)
    parser.add_argument("--sec-audits", type=int, default=5)
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date-exclusive", default="2027-01-01")
    parser.add_argument("--output-root", type=Path, default=output)
    return parser.parse_args(list(argv) if argv is not None else None)


def run(args: argparse.Namespace) -> dict:
    _assert_runtime_path(args.output_root)
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
        news_rows = fetch_news_sample(
            client, config, args.candidate_sample_size
        )
        sec_rows = fetch_sec_sample(
            client, config, args.candidate_sample_size
        )
    finally:
        client.close()

    news_cases = _build_cases(news_rows, "news")
    sec_cases = _build_cases(sec_rows, "sec")
    selected_news = _select_cases(news_cases, args.news_audits, "news")
    selected_sec = _select_cases(sec_cases, args.sec_audits, "sec")
    selected = [*selected_news, *selected_sec]

    audit_dir = args.output_root / "audits"
    audit_dir.mkdir(parents=True, exist_ok=True)
    for stale in audit_dir.glob("*.md"):
        stale.unlink()
    review_rows = []
    for ordinal, case in enumerate(selected, start=1):
        review = review_case(case)
        review_rows.append(review)
        name = (
            f"{ordinal:02d}_{case['corpus']}_"
            f"{_safe_name(case['source_id'])}.md"
        )
        (audit_dir / name).write_text(
            render_case(case, review),
            encoding="utf-8",
        )

    summary = {
        "labeling_version": SCOPED_LABELING_VERSION,
        "news_candidates": len(news_cases),
        "sec_candidates": len(sec_cases),
        "news_audits": len(selected_news),
        "sec_audits": len(selected_sec),
        "review_passed": sum(row["status"] == "pass" for row in review_rows),
        "review_attention": sum(
            row["status"] == "attention" for row in review_rows
        ),
        "news": summarize_scoped_labels(tuple(
            label for case in selected_news for label in case["labels"]
        )),
        "sec": summarize_scoped_labels(tuple(
            label for case in selected_sec for label in case["labels"]
        )),
        "audit_directory": str(audit_dir),
    }
    (args.output_root / "manifest.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (args.output_root / "self_review.json").write_text(
        json.dumps(review_rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (args.output_root / "SELF_REVIEW.md").write_text(
        render_review_summary(summary, review_rows),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2), flush=True)
    return summary


def _build_cases(rows: list[dict], corpus: str) -> list[dict]:
    output = []
    for row in rows:
        document = _document(row, corpus)
        labels = (
            classify_news_document(document)
            if corpus == "news"
            else classify_sec_document(document)
        )
        output.append(
            {
                "corpus": corpus,
                "source_id": document.source_id,
                "timestamp": document.timestamp,
                "title": document.title,
                "tickers": document.tickers,
                "metadata": document.metadata,
                "text": document.text,
                "labels": labels,
                "stratum": row.get("sample_stratum", ""),
            }
        )
    return output


def _document(row: dict, corpus: str) -> SemanticDocument:
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


def _select_cases(cases: list[dict], count: int, corpus: str) -> list[dict]:
    def score(case: dict) -> tuple:
        labels: tuple[ScopedLabel, ...] = case["labels"]
        roles = {label.unit_role for label in labels}
        concepts = {
            concept
            for label in labels
            for concept in label.classification["event_concepts"]
        }
        if corpus == "news":
            priority = (
                5 if "ticker_market_observation" in roles else
                4 if len(case["tickers"]) > 1 else
                3 if concepts else
                2 if labels else 1
            )
        else:
            role = str(case["metadata"].get("document_role") or "")
            priority = (
                5 if role == "press_release_exhibit" else
                4 if role == "material_exhibit" else
                3 if concepts else
                2 if labels else 1
            )
        return (-priority, case["stratum"], case["source_id"])

    ordered = sorted(cases, key=score)
    selected: list[dict] = []
    if corpus == "news":
        predicates = (
            lambda case: _has_role(case, "ticker_market_observation"),
            lambda case: (
                len(case["tickers"]) > 1
                and _has_role(case, "ticker_scoped_editorial_context")
            ),
            lambda case: (
                len(case["tickers"]) == 1
                and any(label.forecast_trigger_eligible for label in case["labels"])
            ),
            lambda case: (
                len(case["tickers"]) == 1
                and any(label.unit_role == "primary_or_editorial_document" for label in case["labels"])
            ),
            lambda case: not case["labels"],
        )
    else:
        predicates = tuple(
            lambda case, role=role: (
                str(case["metadata"].get("document_role") or "") == role
                and bool(case["labels"])
                and bool(case["tickers"])
            )
            for role in (
                "press_release_exhibit",
                "material_exhibit",
                "primary_document",
                "prospectus",
            )
        ) + (
            lambda case: (
                not case["labels"]
                and str(case["metadata"].get("document_role") or "")
                in {"administrative", "press_release_exhibit", "material_exhibit"}
            ),
        )
    for predicate in predicates:
        match = next(
            (
                case for case in ordered
                if case not in selected and predicate(case)
            ),
            None,
        )
        if match is not None:
            selected.append(match)
        if len(selected) == count:
            return selected
    for case in sorted(cases, key=score):
        if case not in selected:
            selected.append(case)
        if len(selected) == count:
            break
    return selected


def review_case(case: dict) -> dict:
    issues: list[str] = []
    notes: list[str] = []
    labels: tuple[ScopedLabel, ...] = case["labels"]
    if not labels:
        notes.append("explicit_abstention_no_relevant_unit")
    for label in labels:
        if label.source_id != case["source_id"]:
            issues.append(f"{label.unit_id}:source_identity_mismatch")
        if not label.ticker and case["corpus"] == "news":
            issues.append(f"{label.unit_id}:missing_ticker")
        if (
            label.unit_role in {
                "ticker_market_observation",
                "editorial_reaction_explanation",
            }
            and (
                label.forecast_trigger_eligible
                or label.reaction_evaluation_eligible
            )
        ):
            issues.append(f"{label.unit_id}:context_marked_as_trigger")
        if case["corpus"] == "sec" and label.unit_role != "relevant_filing_section":
            issues.append(f"{label.unit_id}:unexpected_sec_role")
        for evidence in (
            item
            for canonical in label.semantic["labels"]
            for item in canonical["evidence"]
        ):
            if evidence["text"] not in label.semantic["normalized_semantic_text"] \
                    and evidence["text"].casefold() not in case["text"].casefold():
                issues.append(f"{label.unit_id}:evidence_not_traceable")
    return {
        "corpus": case["corpus"],
        "source_id": case["source_id"],
        "title": case["title"],
        "unit_count": len(labels),
        "status": "attention" if issues else "pass",
        "issues": sorted(set(issues)),
        "notes": sorted(set(notes)),
    }


def render_case(case: dict, review: dict) -> str:
    lines = [
        f"# {case['corpus'].upper()} scoped-label audit",
        "",
        f"- Source ID: `{case['source_id']}`",
        f"- Timestamp: `{case['timestamp']}`",
        f"- Title: {case['title']}",
        f"- Tickers: {', '.join(case['tickers']) or 'none'}",
        f"- Sample stratum: `{case['stratum']}`",
        f"- Self-review: **{review['status']}**",
        f"- Review issues: {', '.join(review['issues']) or 'none'}",
        f"- Review notes: {', '.join(review['notes']) or 'none'}",
        "",
        "## Original rendered text",
        "",
        "```text",
        case["text"],
        "```",
        "",
        "## Extracted and labeled units",
        "",
    ]
    if not case["labels"]:
        lines.append("_No relevant unit was extracted; this is an explicit abstention._")
    for index, label in enumerate(case["labels"], start=1):
        semantic = label.semantic
        classification = label.classification
        lines.extend(
            [
                f"### Unit {index}: {label.ticker or 'unmapped issuer'}",
                "",
                f"- Unit ID: `{label.unit_id}`",
                f"- Unit role: `{label.unit_role}`",
                f"- Content role: `{classification['content_role']}`",
                f"- Source origin: `{classification['source_origin']}`",
                f"- Event concepts: {', '.join(classification['event_concepts']) or 'none'}",
                f"- Semantic direction: `{classification['semantic_direction']}` "
                f"({classification['semantic_score']})",
                f"- Forecast trigger eligible: `{label.forecast_trigger_eligible}`",
                f"- Reaction evaluation eligible: `{label.reaction_evaluation_eligible}`",
                f"- Issuer history context eligible: `{label.issuer_history_context_eligible}`",
                f"- Observed reaction: `{json.dumps(asdict(label.observed_reaction), sort_keys=True)}`",
                f"- Reported catalyst: {label.reported_catalyst or 'none'}",
                f"- Quality flags: {', '.join(classification['quality_flags']) or 'none'}",
                "",
                "#### Scoped text",
                "",
                "```text",
                semantic["normalized_semantic_text"],
                "```",
                "",
                "#### Exact label evidence",
                "",
            ]
        )
        if not semantic["labels"]:
            lines.append("- No supported canonical event label.")
        for item in semantic["labels"]:
            evidence = "; ".join(value["text"] for value in item["evidence"])
            lines.append(
                f"- `{item['family']}.{item['subtype']}` "
                f"({item['direction']}): {evidence}"
            )
        lines.extend(
            [
                "",
                "#### Clean keywords and phrase candidates",
                "",
                f"- Keywords: {', '.join(semantic['keywords'][:30]) or 'none'}",
                "- Candidates:",
            ]
        )
        for candidate in semantic["candidates"][:15]:
            lines.append(
                f"  - `{candidate['phrase']}` x{candidate['count']}"
                + (
                    f" -> `{candidate['seed_concept']}`"
                    if candidate["seed_concept"] else ""
                )
            )
        lines.append("")
    return "\n".join(lines)


def render_review_summary(summary: dict, rows: list[dict]) -> str:
    lines = [
        "# Scoped labeling V1 self-review",
        "",
        "This review certifies extraction invariants, evidence traceability, "
        "and eligibility safety. It does not authorize persistence or "
        "downstream cutover.",
        "",
        f"- Passed: {summary['review_passed']}",
        f"- Needs attention: {summary['review_attention']}",
        f"- Audit directory: `{summary['audit_directory']}`",
        "",
        "## Cases",
        "",
        "| Corpus | Source | Units | Status | Issues / notes |",
        "|---|---|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['corpus']} | `{row['source_id']}` | {row['unit_count']} | "
            f"{row['status']} | "
            f"{', '.join((*row['issues'], *row['notes'])) or 'none'} |"
        )
    return "\n".join(lines) + "\n"


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)[:80]


def _has_role(case: dict, role: str) -> bool:
    return any(label.unit_role == role for label in case["labels"])


def _assert_runtime_path(path: Path) -> None:
    resolved = path.resolve()
    if "runtimes" not in {part.casefold() for part in resolved.parts}:
        raise RuntimeError(f"audit output must be under a runtime root: {resolved}")
