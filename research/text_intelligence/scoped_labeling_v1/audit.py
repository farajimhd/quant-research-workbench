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
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files
from research.mlops.paths import MLOpsPathConfig
from research.text_intelligence.classification_authority_v2.evaluation import (
    attach_sec_tickers,
    fetch_news_sample,
    fetch_sec_sample,
    json_rows,
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
from .news_extractor import analyze_news_scope
from .news_identity import NewsIssuerResolver, load_news_issuer_resolver
from .schema import ScopedLabel
from .schema import SCOPED_LABELING_VERSION


EXPECTED_NEWS_OUTCOMES = {
    "1d0bb440729b3e79bc5dc8d28fb83d92": {
        "AERI": {
            "trigger": True,
            "issuer_role": "target",
            "required_direction": "positive",
            "observed_direction": "up",
            "observed_move_pct": 35.6,
            "required_concepts": {
                "ma_transaction.acquisition",
                "analyst_action.downgrade",
            },
        },
        "ALC": {
            "trigger": True,
            "issuer_role": "acquirer",
            "required_direction": "mixed",
            "observed_direction": "up",
            "observed_move_pct": 0.09,
            "required_concepts": {
                "ma_transaction.acquisition",
                "profitability.margin_pressure",
            },
        },
    },
    "2a40f3fcd60c38c389bc48f79c0379a5": {
        "CNSP": {
            "trigger": True,
            "required_concepts": {"clinical.progress_update"},
        },
    },
    "038c30ed7bb7e369fc28c1bf66e58469": {
        "FDMT": {"trigger": True, "require_any_concept": True},
    },
    "2f97b47b5d642d1d414a994256f31199": {
        "VVOS": {"trigger": True, "require_any_concept": True},
        "__forbidden_tickers__": {"RDGL"},
    },
    "0c9794153b7d09e1e3bc565294987d27": {
        "__all_non_triggering__": True,
    },
}

EXPECTED_SEC_OUTCOMES = {
    "1fa16cfb1fe5ccaaa9ca6787b02cfa1b89ca1c01": {
        "required_concepts": {
            "guidance.raise",
            "earnings.revenue_growth",
            "legal.settlement",
        },
        "require_trigger": True,
    },
    "1ba1336b22aaa6146ca4b59b56412a5f537eca5f": {
        "required_concepts": {
            "management_governance.employee_share_purchase_plan_amendment",
        },
        "require_trigger": True,
    },
    "8baaca44adb4962ed806c2677d274492bb511088": {
        "required_concepts": {
            "financing.preferred_stock_private_placement",
            "financing.warrant",
        },
        "require_trigger": True,
    },
    "b6228180d95518ce5f08238e5914d617796af75c": {
        "require_all_non_triggering": True,
    },
    "056dddcf986b5ef9f51e215e5197cb26a8561972": {
        "require_no_units": True,
    },
}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    output = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "scoped_labeling_v5"
        / "certification"
    )
    parser = argparse.ArgumentParser(
        description="Create and self-review scoped News and SEC audit files."
    )
    parser.add_argument("--candidate-sample-size", type=int, default=120)
    parser.add_argument(
        "--news-audits",
        type=int,
        default=10,
        help=(
            "Total News audits. The five mandatory regressions are always "
            "included; the remaining cases are a fresh stratified set."
        ),
    )
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
        required_news = fetch_required_news(
            client,
            config,
            tuple(EXPECTED_NEWS_OUTCOMES),
        )
        news_rows = [
            *required_news,
            *(
                row for row in news_rows
                if row["source_id"] not in EXPECTED_NEWS_OUTCOMES
            ),
        ]
        sec_rows = fetch_sec_sample(
            client, config, args.candidate_sample_size
        )
        required_sec = fetch_required_sec(
            client,
            config,
            tuple(EXPECTED_SEC_OUTCOMES),
        )
        sec_rows = [
            *required_sec,
            *(
                row for row in sec_rows
                if row["source_id"] not in EXPECTED_SEC_OUTCOMES
            ),
        ]
        issuer_resolver = load_news_issuer_resolver(client, config.database)
    finally:
        client.close()

    news_cases = _build_cases(
        news_rows,
        "news",
        issuer_resolver=issuer_resolver,
    )
    sec_cases = _build_cases(sec_rows, "sec")
    selected_news = _select_cases(news_cases, args.news_audits, "news")
    selected_sec = _select_cases(sec_cases, args.sec_audits, "sec")
    selected = [*selected_news, *selected_sec]
    news_scope_coverage = _news_scope_coverage(selected_news)
    missing_news_scope_cases = sorted(
        name for name, covered in news_scope_coverage.items() if not covered
    )

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
        ) + len(missing_news_scope_cases),
        "news_scope_coverage": news_scope_coverage,
        "missing_news_scope_cases": missing_news_scope_cases,
        "expected_outcome_failures": [
            issue
            for row in review_rows
            for issue in row["issues"]
            if issue.startswith("expected:")
        ],
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


def fetch_required_news(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
    source_ids: tuple[str, ...],
) -> list[dict]:
    """Fetch mandatory semantic regression cases by exact source identity."""
    if not source_ids:
        return []
    db = quote_ident(config.database)
    event = quote_ident(config.news_event_table)
    rendered = quote_ident(config.news_rendered_table)
    identifiers = ",".join(sql_string(value) for value in source_ids)
    rows = json_rows(client.execute(f"""
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
WHERE e.canonical_news_id IN ({identifiers})
  AND notEmpty(r.rendered_text)
ORDER BY indexOf([{identifiers}], e.canonical_news_id)
FORMAT JSONEachRow
"""))
    found = {str(row["source_id"]) for row in rows}
    missing = sorted(set(source_ids) - found)
    if missing:
        raise RuntimeError(
            "mandatory News semantic regression cases are missing: "
            + ", ".join(missing)
        )
    for row in rows:
        row["sample_stratum"] = "mandatory_semantic_regression"
        row["sample_rationale"] = (
            "certifies expected issuer-level semantics and eligibility"
        )
    return rows


def fetch_required_sec(
    client: ClickHouseHttpClient,
    config: CandidateInventoryConfig,
    source_ids: tuple[str, ...],
) -> list[dict]:
    """Fetch mandatory SEC semantic regression cases by document identity."""
    if not source_ids:
        return []
    db = quote_ident(config.database)
    rendered = quote_ident(config.sec_rendered_table)
    document = quote_ident(config.sec_document_table)
    filing = quote_ident(config.sec_filing_table)
    identifiers = ",".join(sql_string(value) for value in source_ids)
    rows = json_rows(client.execute(f"""
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
 r.extraction_method, r.quality_flags,
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
FROM
(
 SELECT *
 FROM {db}.{rendered} FINAL
 PREWHERE document_id IN ({identifiers})
) AS r
LEFT JOIN
(
 SELECT *
 FROM {db}.{document} FINAL
 PREWHERE document_id IN ({identifiers})
) AS d
 ON d.document_id=r.document_id
 AND d.cik=r.cik
 AND d.accession_number=r.accession_number
LEFT JOIN
(
 SELECT *
 FROM {db}.{filing} FINAL
 WHERE filing_id IN (
   SELECT filing_id
   FROM {db}.{rendered}
   PREWHERE document_id IN ({identifiers})
 )
) AS f
 ON f.filing_id=r.filing_id
 AND f.cik=r.cik
 AND f.accession_number=r.accession_number
WHERE r.document_id IN ({identifiers})
  AND isNotNull(f.accepted_at_utc)
ORDER BY indexOf([{identifiers}], r.document_id)
FORMAT JSONEachRow
"""))
    found = {str(row["source_id"]) for row in rows}
    missing = sorted(set(source_ids) - found)
    if missing:
        raise RuntimeError(
            "mandatory SEC semantic regression cases are missing: "
            + ", ".join(missing)
        )
    attach_sec_tickers(client, rows)
    for row in rows:
        row["sample_stratum"] = "mandatory_semantic_regression"
        row["sample_rationale"] = (
            "certifies expected SEC event semantics and eligibility"
        )
    return rows


def _build_cases(
    rows: list[dict],
    corpus: str,
    *,
    issuer_resolver: NewsIssuerResolver | None = None,
) -> list[dict]:
    output = []
    for row in rows:
        document = _document(row, corpus)
        labels = (
            classify_news_document(
                document,
                issuer_resolver=issuer_resolver,
            )
            if corpus == "news"
            else classify_sec_document(document)
        )
        scope_analysis = (
            analyze_news_scope(
                source_id=document.source_id,
                title=document.title,
                text=document.text,
                tickers=document.tickers,
                timestamp=document.timestamp,
                issuer_resolver=issuer_resolver,
                metadata=document.metadata,
            )
            if corpus == "news"
            else None
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
                "scope_analysis": scope_analysis,
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
    if corpus == "news":
        by_id = {case["source_id"]: case for case in cases}
        required = [
            by_id[source_id]
            for source_id in EXPECTED_NEWS_OUTCOMES
            if source_id in by_id
        ]
        if count <= len(required):
            return required[:count]
    elif corpus == "sec":
        by_id = {case["source_id"]: case for case in cases}
        required = [
            by_id[source_id]
            for source_id in EXPECTED_SEC_OUTCOMES
            if source_id in by_id
        ]
        missing = sorted(set(EXPECTED_SEC_OUTCOMES) - set(by_id))
        if missing:
            raise RuntimeError(
                "mandatory SEC semantic regression cases are missing: "
                + ", ".join(missing)
            )
        if count <= len(required):
            return required[:count]

    def score(case: dict) -> tuple:
        labels: tuple[ScopedLabel, ...] = case["labels"]
        roles = {label.unit_role for label in labels}
        concepts = {
            concept
            for label in labels
            for concept in label.classification["event_concepts"]
        }
        if corpus == "news":
            decision = (
                case["scope_analysis"].document_decision
                if case["scope_analysis"] is not None else ""
            )
            priority = (
                7 if decision == "mixed_issuer_passage_scoping"
                    and len(case["tickers"]) == 1 else
                6 if decision == "unresolved_issuer_passage_abstention" else
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
    selected: list[dict] = required if corpus in {"news", "sec"} else []
    if corpus == "news":
        predicates = (
            lambda case: (
                len(case["tickers"]) == 1
                and case["scope_analysis"].document_decision
                == "mixed_issuer_passage_scoping"
            ),
            lambda case: (
                case["scope_analysis"].document_decision
                == "unresolved_issuer_passage_abstention"
            ),
            lambda case: _has_role(case, "ticker_market_observation"),
            lambda case: (
                _has_role(case, "ticker_scoped_analyst_context")
                or (
                    len(case["tickers"]) > 1
                    and _has_role(case, "ticker_scoped_editorial_context")
                )
            ),
            lambda case: (
                len(case["tickers"]) == 1
                and any(label.forecast_trigger_eligible for label in case["labels"])
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


def _news_scope_coverage(cases: list[dict]) -> dict[str, bool]:
    present = {case["source_id"] for case in cases}
    return {
        f"mandatory:{source_id}": source_id in present
        for source_id in EXPECTED_NEWS_OUTCOMES
    }


def review_case(case: dict) -> dict:
    issues: list[str] = []
    notes: list[str] = []
    labels: tuple[ScopedLabel, ...] = case["labels"]
    scope_analysis = case.get("scope_analysis")
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
                "ticker_scoped_editorial_context",
                "ticker_scoped_analyst_context",
            }
            and (
                label.forecast_trigger_eligible
                or label.reaction_evaluation_eligible
            )
        ):
            issues.append(f"{label.unit_id}:context_marked_as_trigger")
        if case["corpus"] == "sec" and label.unit_role != "relevant_filing_section":
            issues.append(f"{label.unit_id}:unexpected_sec_role")
        direction = label.classification["semantic_direction"]
        score = float(label.classification["semantic_score"])
        if direction not in {"positive", "negative", "neutral", "mixed"}:
            issues.append(f"{label.unit_id}:invalid_semantic_direction")
        if direction == "positive" and score < 0.5:
            issues.append(f"{label.unit_id}:positive_direction_score_mismatch")
        if direction == "negative" and score > -0.5:
            issues.append(f"{label.unit_id}:negative_direction_score_mismatch")
        if direction == "neutral" and abs(score) >= 0.5:
            issues.append(f"{label.unit_id}:neutral_direction_score_mismatch")
        if case["corpus"] == "news":
            concepts = set(label.classification["event_concepts"])
            if (
                label.issuer_role == "target"
                and concepts & {
                    "ma_transaction.acquisition",
                    "ma_transaction.merger_agreement",
                }
                and direction not in {"positive", "mixed"}
            ):
                issues.append(
                    f"{label.unit_id}:target_transaction_direction_mismatch"
                )
            if (
                label.unit_role == "analyst_opinion"
                and label.classification["content_role"]
                != "automated_summary"
                and (
                    label.classification["content_role"] != "analyst_event"
                    or label.classification["issuer_relationship"]
                    != "analyst_opinion"
                )
            ):
                issues.append(
                    f"{label.unit_id}:analyst_opinion_classification_mismatch"
                )
        for evidence in (
            item
            for canonical in label.semantic["labels"]
            for item in canonical["evidence"]
        ):
            evidence_text = _collapsed(evidence["text"])
            if evidence_text not in _collapsed(
                label.semantic["normalized_semantic_text"]
            ) and evidence_text.casefold() not in _collapsed(
                case["text"]
            ).casefold():
                issues.append(f"{label.unit_id}:evidence_not_traceable")
    if case["corpus"] == "news":
        issues.extend(_expected_news_issues(case))
    else:
        issues.extend(_expected_sec_issues(case))
    return {
        "corpus": case["corpus"],
        "source_id": case["source_id"],
        "title": case["title"],
        "unit_count": len(labels),
        "status": "attention" if issues else "pass",
        "issues": sorted(set(issues)),
        "notes": sorted(set(notes)),
    }


def _expected_news_issues(case: dict) -> list[str]:
    expected = EXPECTED_NEWS_OUTCOMES.get(case["source_id"])
    if expected is None:
        return []
    labels: tuple[ScopedLabel, ...] = case["labels"]
    issues: list[str] = []
    if expected.get("__all_non_triggering__") and any(
        label.forecast_trigger_eligible for label in labels
    ):
        issues.append("expected:aggregation_must_be_non_triggering")
    forbidden = set(expected.get("__forbidden_tickers__", ()))
    actual_tickers = {label.ticker for label in labels}
    for ticker in sorted(forbidden & actual_tickers):
        issues.append(f"expected:forbidden_ticker:{ticker}")
    for ticker, contract in expected.items():
        if ticker.startswith("__"):
            continue
        ticker_labels = [label for label in labels if label.ticker == ticker]
        if not ticker_labels:
            issues.append(f"expected:missing_ticker:{ticker}")
            continue
        if bool(contract.get("trigger")) != any(
            label.forecast_trigger_eligible for label in ticker_labels
        ):
            issues.append(f"expected:trigger_mismatch:{ticker}")
        role = contract.get("issuer_role")
        if role and role not in {label.issuer_role for label in ticker_labels}:
            issues.append(f"expected:issuer_role:{ticker}:{role}")
        required_direction = contract.get("required_direction")
        actual_directions = {
            label.classification["semantic_direction"]
            for label in ticker_labels
        }
        if required_direction and required_direction not in actual_directions:
            issues.append(
                f"expected:semantic_direction:{ticker}:"
                f"{required_direction}:actual={','.join(sorted(actual_directions))}"
            )
        observed_direction = contract.get("observed_direction")
        actual_observed = {
            label.observed_reaction.direction
            for label in ticker_labels
            if label.observed_reaction.direction
        }
        if observed_direction and observed_direction not in actual_observed:
            issues.append(
                f"expected:observed_direction:{ticker}:"
                f"{observed_direction}:actual={','.join(sorted(actual_observed))}"
            )
        observed_move = contract.get("observed_move_pct")
        if observed_move is not None and not any(
            label.observed_reaction.move_pct is not None
            and abs(label.observed_reaction.move_pct - observed_move) < 1e-9
            for label in ticker_labels
        ):
            issues.append(
                f"expected:observed_move_pct:{ticker}:{observed_move}"
            )
        concepts = {
            concept
            for label in ticker_labels
            for concept in label.classification["event_concepts"]
        }
        for concept in sorted(
            set(contract.get("required_concepts", ())) - concepts
        ):
            issues.append(f"expected:missing_concept:{ticker}:{concept}")
        if contract.get("require_any_concept") and not concepts:
            issues.append(f"expected:no_event_concept:{ticker}")
    return issues


def _expected_sec_issues(case: dict) -> list[str]:
    expected = EXPECTED_SEC_OUTCOMES.get(case["source_id"])
    if expected is None:
        return []
    labels: tuple[ScopedLabel, ...] = case["labels"]
    issues: list[str] = []
    if expected.get("require_no_units") and labels:
        issues.append("expected:sec_administrative_document_must_abstain")
    if expected.get("require_all_non_triggering") and any(
        label.forecast_trigger_eligible for label in labels
    ):
        issues.append("expected:sec_historical_context_must_be_non_triggering")
    if expected.get("require_trigger") and not any(
        label.forecast_trigger_eligible for label in labels
    ):
        issues.append("expected:sec_event_must_trigger")
    concepts = {
        concept
        for label in labels
        for concept in label.classification["event_concepts"]
    }
    for concept in sorted(set(expected.get("required_concepts", ())) - concepts):
        issues.append(f"expected:missing_sec_concept:{concept}")
    return issues


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
    ]
    expected = (
        EXPECTED_NEWS_OUTCOMES.get(case["source_id"])
        if case["corpus"] == "news"
        else EXPECTED_SEC_OUTCOMES.get(case["source_id"])
    )
    if expected is not None:
        lines.extend([
            "## Expected semantic outcome",
            "",
            "```json",
            json.dumps(
                {
                    key: (
                        {
                            field: sorted(value)
                            if isinstance(value, set) else value
                            for field, value in contract.items()
                        }
                        if isinstance(contract, dict) else sorted(contract)
                        if isinstance(contract, set) else contract
                    )
                    for key, contract in expected.items()
                },
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
        ])
    scope_analysis = case.get("scope_analysis")
    if scope_analysis is not None:
        lines.extend(
            [
                "## Issuer scope resolution",
                "",
                f"- Resolver: `{scope_analysis.resolver_version}`",
                f"- Provider-linked tickers: "
                f"{', '.join(scope_analysis.linked_tickers) or 'none'}",
                f"- Text-resolved subjects: "
                f"{', '.join(scope_analysis.resolved_subjects) or 'none'}",
                f"- Document decision: `{scope_analysis.document_decision}`",
                f"- Aggregation structure: `{scope_analysis.aggregation}`",
                "",
                "| # | Assigned | Directly resolved | Decision | Evidence | Passage |",
                "|---:|---|---|---|---|---|",
            ]
        )
        for passage in scope_analysis.passages:
            passage_text = passage.text.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {passage.ordinal} | {passage.assigned_ticker or 'none'} | "
                f"{', '.join(passage.resolved_tickers) or 'none'} | "
                f"`{passage.decision}` | "
                f"{'; '.join(passage.evidence) or 'none'} | {passage_text} |"
            )
        lines.extend(["", "## Extracted and labeled units", ""])
    else:
        lines.extend(
            [
                "## Extracted and labeled units",
                "",
            ]
        )
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
                f"- Event ID: `{label.event_id or 'none'}`",
                f"- Event tickers: {', '.join(label.event_tickers) or 'none'}",
                f"- Issuer role: `{label.issuer_role or 'none'}`",
                f"- Evidence scope: `{label.evidence_scope}`",
                f"- Publication text hash: `{label.publication_text_hash}`",
                f"- Content role: `{classification['content_role']}`",
                f"- Source origin: `{classification['source_origin']}`",
                f"- Event concepts: {', '.join(classification['event_concepts']) or 'none'}",
                f"- Semantic direction: `{classification['semantic_direction']}` "
                f"({classification['semantic_score']})",
                "- Direction base / adjustment: "
                f"`{classification.get('semantic_score_base', classification['semantic_score'])}` / "
                f"`{classification.get('semantic_score_adjustment', 0.0)}`",
                "- Direction basis: "
                + (
                    ", ".join(
                        classification.get("semantic_direction_basis", ())
                    )
                    or "none"
                ),
                f"- Forecast trigger eligible: `{label.forecast_trigger_eligible}`",
                f"- Reaction evaluation eligible: `{label.reaction_evaluation_eligible}`",
                f"- Issuer history context eligible: `{label.issuer_history_context_eligible}`",
                f"- Observed reaction: `{json.dumps(asdict(label.observed_reaction), sort_keys=True)}`",
                f"- Reported catalyst: {label.reported_catalyst or 'none'}",
                f"- Quality flags: {', '.join(classification['quality_flags']) or 'none'}",
                "",
                "#### Issuer-scoped semantic evidence",
                "",
                "```text",
                label.semantic_evidence_text,
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
        "# Scoped labeling V4 certification review",
        "",
        "This review checks extraction invariants, evidence traceability, and "
        "mandatory issuer-level semantic outcomes. It does not authorize "
        "persistence or downstream cutover until a human reviews every file.",
        "",
        f"- Passed: {summary['review_passed']}",
        f"- Needs attention: {summary['review_attention']}",
        f"- Audit directory: `{summary['audit_directory']}`",
        "",
        "## Mandatory News semantic regression cases",
        "",
    ]
    for name, covered in summary["news_scope_coverage"].items():
        lines.append(f"- {name}: **{'covered' if covered else 'missing'}**")
    lines.extend([
        "",
        "## Cases",
        "",
        "| Corpus | Source | Units | Status | Issues / notes |",
        "|---|---|---:|---|---|",
    ])
    for row in rows:
        lines.append(
            f"| {row['corpus']} | `{row['source_id']}` | {row['unit_count']} | "
            f"{row['status']} | "
            f"{', '.join((*row['issues'], *row['notes'])) or 'none'} |"
        )
    return "\n".join(lines) + "\n"


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)[:80]


def _collapsed(value: str) -> str:
    return " ".join(str(value).split())


def _has_role(case: dict, role: str) -> bool:
    return any(label.unit_role == role for label in case["labels"])


def _assert_runtime_path(path: Path) -> None:
    resolved = path.resolve()
    if "runtimes" not in {part.casefold() for part in resolved.parts}:
        raise RuntimeError(f"audit output must be under a runtime root: {resolved}")
