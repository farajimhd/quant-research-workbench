from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.text_intelligence.semantic_calibration_v1.candidate_contract import (
    enrich_candidate_rows,
)

from .certification import default_certification_config
from .engine import (
    IssuerIdentity,
    IssuerIdentityIndex,
    NewsSynthesisEngine,
    _normalize_ticker_identifier,
    _safe_alias,
)
from .taxonomy_audit import discover_pairs, load_json


AUDIT_VERSION = "direct_trading_sentiment_audit_v8"
IDENTITY_SNAPSHOT_VERSION = "news_synthesis_benchmark_identity_snapshot_v1"


@dataclass(frozen=True, slots=True)
class AuditPopulation:
    certified_ids: frozenset[str]
    annotations: Mapping[str, dict[str, Any]]
    articles: Mapping[str, dict[str, Any]]
    identity_articles: tuple[dict[str, Any], ...]


def article_source(article: Mapping[str, Any]) -> dict[str, Any]:
    publication = article.get("publication", {})
    rendered = article.get("rendered_product", {})
    return {
        "source_id": article["source_id"],
        "source_timestamp": article["source_timestamp"],
        "title": publication.get("title", ""),
        "author": publication.get("author", ""),
        "article_url": publication.get("article_url", ""),
        "url_domain": publication.get("url_domain", ""),
        "text": rendered.get("text", ""),
        "tickers": publication.get("provider_tickers", []),
        "channels": publication.get("channels", []),
        "provider_tags": publication.get("provider_tags", []),
        "content_quality_flags": publication.get("content_quality_flags", []),
        "quality_flags": rendered.get("quality_flags", []),
        "render_status": "title_only" if int(rendered.get("source_count") or 0) == 0 else "rendered",
        "rendered_text_hash": article.get("source_text_sha256", ""),
    }


def load_population() -> AuditPopulation:
    config = default_certification_config()
    certified_ids = frozenset(
        path.stem
        for path in (config.output_root / "certified_labels").glob("*.json")
    )
    annotations: dict[str, dict[str, Any]] = {}
    articles: dict[str, dict[str, Any]] = {}
    identity_articles: list[dict[str, Any]] = []
    for annotation_path, article_path, _collection in discover_pairs(
        config.collection_roots
    ):
        article = load_json(article_path)
        sample_id = str(article["sample_id"])
        identity_articles.append(article)
        if sample_id in certified_ids:
            annotations[sample_id] = load_json(annotation_path)
            articles[sample_id] = article
    if len(certified_ids) != 1045 or set(annotations) != set(certified_ids):
        raise RuntimeError(
            "Certified population identity mismatch: "
            f"certified={len(certified_ids)} annotations={len(annotations)}"
        )
    return AuditPopulation(
        certified_ids=certified_ids,
        annotations=annotations,
        articles=articles,
        identity_articles=tuple(identity_articles),
    )


def build_benchmark_identity_snapshot(
    articles: Iterable[Mapping[str, Any]],
) -> tuple[IssuerIdentityIndex, dict[str, Any]]:
    """Build prediction-blind offline identity evidence for reproducible audits.

    This is not a replacement for the production ClickHouse identity authority.
    It repairs the frozen candidate contract, consolidates aliases across the
    source corpus, and preserves shared-issuer lineage when securities share an
    unambiguous non-generic alias.
    """
    articles = tuple(articles)
    aliases_by_ticker: dict[str, set[str]] = defaultdict(set)
    tickers: set[str] = set()
    for article in articles:
        publication = article.get("publication", {})
        rendered = article.get("rendered_product", {})
        rows = enrich_candidate_rows(
            article.get("point_in_time_issuer_candidates", []),
            title=str(publication.get("title") or ""),
            teaser=str(publication.get("teaser") or ""),
            rendered_text=str(rendered.get("text") or ""),
            authoritative_identifiers=publication.get("provider_tickers") or (),
        )
        for row in rows:
            ticker = _normalize_ticker_identifier(
                row.get("display_symbol") or row.get("canonical_instrument_id")
            )
            if not ticker or not re.fullmatch(
                r"(?:(?:TSX|TSXV|CSE):)?[A-Z][A-Z0-9.\-]{0,9}", ticker
            ):
                continue
            tickers.add(ticker)
            for evidence in row.get("identity_evidence", []):
                if not str(evidence).startswith("issuer_alias:"):
                    continue
                alias = str(evidence).split(":", 1)[1].strip()
                if _benchmark_alias_safe(alias):
                    aliases_by_ticker[ticker].add(alias)

    parent = {ticker: ticker for ticker in tickers}

    def find(ticker: str) -> str:
        while parent[ticker] != ticker:
            parent[ticker] = parent[parent[ticker]]
            ticker = parent[ticker]
        return ticker

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    tickers_by_alias: dict[str, set[str]] = defaultdict(set)
    for ticker, aliases in aliases_by_ticker.items():
        for alias in aliases:
            tickers_by_alias[_normalize_alias(alias)].add(ticker)
    for alias, related in tickers_by_alias.items():
        if len(alias.split()) < 2:
            continue
        ordered = sorted(related)
        for ticker in ordered[1:]:
            union(ordered[0], ticker)

    groups: dict[str, list[str]] = defaultdict(list)
    for ticker in sorted(tickers):
        groups[find(ticker)].append(ticker)
    identities: list[IssuerIdentity] = []
    snapshot_rows: list[dict[str, Any]] = []
    for ticker in sorted(tickers):
        group = groups[find(ticker)]
        issuer_key = hashlib.sha256("|".join(group).encode()).hexdigest()[:20]
        aliases = sorted(
            alias
            for alias in aliases_by_ticker.get(ticker, ())
            if len({find(value) for value in tickers_by_alias[_normalize_alias(alias)]}) == 1
        )
        display_name = max(aliases, key=len).title() if aliases else ticker
        identity = IssuerIdentity(
            ticker=ticker,
            issuer_id=f"benchmark-issuer:{issuer_key}",
            display_name=display_name,
            aliases=tuple(aliases or (ticker,)),
            security_id=f"benchmark-security:{ticker}",
        )
        identities.append(identity)
        snapshot_rows.append(
            {
                "ticker": ticker,
                "issuer_id": identity.issuer_id,
                "security_id": identity.security_id,
                "display_name": display_name,
                "aliases": aliases,
            }
        )
    canonical = json.dumps(
        snapshot_rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot = {
        "version": IDENTITY_SNAPSHOT_VERSION,
        "source": "prediction_blind_candidate_contract_and_provider_identifiers",
        "production_authority": False,
        "article_count": len(articles),
        "identity_count": len(snapshot_rows),
        "sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "identities": snapshot_rows,
    }
    return IssuerIdentityIndex(identities), snapshot


def prediction_sentiments(document: Mapping[str, Any]) -> dict[str, str]:
    tickers = {
        str(row["entity_id"]): str(row.get("ticker") or "").upper()
        for row in document.get("entities", [])
        if row.get("entity_id") and row.get("ticker")
    }
    return {
        tickers[str(row["entity_id"])]: str(row["composite_sentiment"])
        for row in document.get("issuer_views", [])
        if str(row.get("entity_id")) in tickers
    }


def generate_audit(
    output_root: Path,
    *,
    previous_manifest: Path | None = None,
) -> dict[str, Any]:
    population = load_population()
    identity_index, snapshot = build_benchmark_identity_snapshot(
        population.identity_articles
    )
    engine = NewsSynthesisEngine(identity_index)
    if output_root.exists():
        shutil.rmtree(output_root)
    (output_root / "audit_files").mkdir(parents=True)

    eligible_news: set[str] = set()
    eligible_units = 0
    exact_matches = 0
    failures: list[dict[str, str]] = []
    records: list[dict[str, Any]] = []
    missing_dispositions = Counter()
    for sample_id in sorted(population.certified_ids):
        annotation = population.annotations[sample_id]
        article = population.articles[sample_id]
        units = [
            unit
            for unit in annotation.get("issuer_units", [])
            if unit.get("forecast_trigger_eligible")
            or unit.get("analyst_evaluation_eligible")
        ]
        if not units:
            continue
        eligible_news.add(sample_id)
        eligible_units += len(units)
        try:
            prediction = engine.synthesize(article_source(article))
        except Exception as exc:
            prediction = {}
            failures.append(
                {"sample_id": sample_id, "error": f"{type(exc).__name__}: {exc}"}
            )
        sentiments = prediction_sentiments(prediction)
        entity_by_ticker = {
            str(row.get("ticker") or "").upper(): str(row.get("entity_id") or "")
            for row in prediction.get("entities", [])
        }
        participation_ids = {
            str(row.get("entity_id")) for row in prediction.get("participations", [])
        }
        for unit in units:
            ticker = str(unit.get("ticker") or "").upper()
            manual = str(unit.get("semantic_direction") or "")
            predicted = sentiments.get(ticker, "missing")
            if manual == predicted:
                exact_matches += 1
                continue
            disposition = "direction_mismatch"
            if predicted == "missing":
                entity_id = entity_by_ticker.get(ticker)
                if not entity_id:
                    disposition = "identity_unresolved"
                elif not prediction.get("statements"):
                    disposition = "no_supported_statement"
                elif entity_id not in participation_ids:
                    disposition = "statement_unbound"
                else:
                    disposition = "issuer_view_missing"
                missing_dispositions[disposition] += 1
            eligibility_class = (
                "analyst_evaluation"
                if unit.get("analyst_evaluation_eligible")
                else "forecast_reaction"
            )
            error_type = f"{manual}_to_{predicted}"
            relative = Path("audit_files") / error_type / f"{sample_id}__{_safe(ticker)}.md"
            target = output_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                _render_packet(
                    article=article,
                    annotation=annotation,
                    unit=unit,
                    prediction=prediction,
                    predicted=predicted,
                    eligibility_class=eligibility_class,
                    disposition=disposition,
                ),
                encoding="utf-8",
            )
            records.append(
                {
                    "sample_id": sample_id,
                    "ticker": ticker,
                    "eligibility_class": eligibility_class,
                    "manual_sentiment": manual,
                    "predicted_sentiment": predicted,
                    "error_type": error_type,
                    "failure_stage": disposition,
                    "file": relative.as_posix(),
                }
            )

    error_counts = Counter(row["error_type"] for row in records)
    manifest: dict[str, Any] = {
        "version": AUDIT_VERSION,
        "identity_authority": {key: value for key, value in snapshot.items() if key != "identities"},
        "population": {
            "certified_news": len(population.certified_ids),
            "distinct_direct_trading_news": len(eligible_news),
            "direct_trading_issuer_units": eligible_units,
            "exact_sentiment_matches": exact_matches,
            "sentiment_mismatches": len(records),
            "missing_sentiments": sum(row["predicted_sentiment"] == "missing" for row in records),
        },
        "selection": "Corrected manually reviewed issuer units where forecast_trigger_eligible or analyst_evaluation_eligible is true.",
        "error_definition": "Current News Synthesis issuer-view sentiment for the same ticker differs from corrected manual semantic_direction; absent views are missing errors with explicit failure stages.",
        "error_counts": dict(sorted(error_counts.items())),
        "missing_dispositions": dict(sorted(missing_dispositions.items())),
        "engine_failures": failures,
        "records": records,
    }
    (output_root / "identity_snapshot.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if previous_manifest:
        previous = load_json(previous_manifest)
        comparison = compare_manifests(previous, manifest)
        manifest["comparison_to_previous"] = comparison
        (output_root / "comparison_to_previous.json").write_text(
            json.dumps(comparison, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_root / "README.md").write_text(_render_readme(manifest), encoding="utf-8")
    return manifest


def compare_manifests(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    before = previous["population"]
    after = current["population"]
    keys = (
        "exact_sentiment_matches",
        "sentiment_mismatches",
        "missing_sentiments",
    )
    before_missing = int(before.get("missing_sentiments") or sum(
        row.get("predicted_sentiment") in {"missing", "<missing>"}
        for row in previous.get("records", [])
    ))
    before_values = {**before, "missing_sentiments": before_missing}
    previous_errors = {
        (str(row.get("sample_id")), str(row.get("ticker"))): row
        for row in previous.get("records", [])
    }
    current_errors = {
        (str(row.get("sample_id")), str(row.get("ticker"))): row
        for row in current.get("records", [])
    }
    previous_error_keys = set(previous_errors)
    current_error_keys = set(current_errors)

    def is_missing(row: Mapping[str, Any]) -> bool:
        return row.get("predicted_sentiment") in {None, "", "missing", "<missing>"}

    previous_missing_keys = {
        key for key, row in previous_errors.items() if is_missing(row)
    }
    current_missing_keys = {
        key for key, row in current_errors.items() if is_missing(row)
    }
    recovered_missing = previous_missing_keys - current_missing_keys
    return {
        "previous_version": previous.get("version"),
        "current_version": current.get("version"),
        "population_identity_equal": all(
            int(before.get(key) or 0) == int(after.get(key) or 0)
            for key in ("certified_news", "distinct_direct_trading_news", "direct_trading_issuer_units")
        ),
        "metrics": {
            key: {
                "before": int(before_values.get(key) or 0),
                "after": int(after.get(key) or 0),
                "delta": int(after.get(key) or 0) - int(before_values.get(key) or 0),
            }
            for key in keys
        },
        "identity_transitions": {
            "fixed_errors": len(previous_error_keys - current_error_keys),
            "new_errors": len(current_error_keys - previous_error_keys),
            "previous_missing": len(previous_missing_keys),
            "current_missing": len(current_missing_keys),
            "missing_recovered_correct": len(recovered_missing - current_error_keys),
            "missing_recovered_wrong_direction": len(recovered_missing & current_error_keys),
            "newly_missing": len(current_missing_keys - previous_missing_keys),
        },
    }


def _render_packet(
    *,
    article: Mapping[str, Any],
    annotation: Mapping[str, Any],
    unit: Mapping[str, Any],
    prediction: Mapping[str, Any],
    predicted: str,
    eligibility_class: str,
    disposition: str,
) -> str:
    publication = article.get("publication", {})
    rendered = article.get("rendered_product", {})
    sample_id = str(article["sample_id"])
    ticker = str(unit["ticker"]).upper()
    manual = str(unit["semantic_direction"])
    source_text = str(rendered.get("text") or publication.get("title") or "")
    focused_annotation = dict(annotation)
    focused_annotation["issuer_units"] = [dict(unit)]
    return (
        f"# Direct-trading sentiment audit — {sample_id} / {ticker}\n\n"
        "## Error summary\n\n"
        f"- **Manual eligibility class:** `{eligibility_class}`\n"
        f"- **Manual sentiment:** `{manual}`\n"
        f"- **News Synthesis sentiment:** `{predicted}`\n"
        f"- **Failure stage:** `{disposition}`\n"
        f"- **Error type:** `{manual}_to_{predicted}`\n\n"
        "## Source metadata\n\n"
        f"- **Sample ID:** {sample_id}\n"
        f"- **Audited ticker:** {ticker}\n"
        f"- **Source ID:** {article.get('source_id', '')}\n"
        f"- **Published:** {article.get('source_timestamp', '')}\n"
        f"- **Provider:** {publication.get('provider', '')}\n"
        f"- **URL:** {publication.get('article_url', '')}\n\n"
        "## Original news\n\n"
        f"### {publication.get('title') or 'Untitled'}\n\n{source_text}\n\n"
        "## Corrected manually reviewed ground truth\n\n"
        f"```json\n{json.dumps(focused_annotation, indent=2, ensure_ascii=False)}\n```\n\n"
        "## News Synthesis labels\n\n"
        f"```json\n{json.dumps(prediction, indent=2, ensure_ascii=False)}\n```\n"
    )


def _render_readme(manifest: Mapping[str, Any]) -> str:
    population = manifest["population"]
    return (
        "# Direct-trading sentiment audit\n\n"
        "Mismatch-only issuer-level audit over the certified direct-trading population.\n\n"
        f"- Distinct eligible news: {population['distinct_direct_trading_news']}\n"
        f"- Eligible issuer units: {population['direct_trading_issuer_units']}\n"
        f"- Exact matches: {population['exact_sentiment_matches']}\n"
        f"- Audit packets: {population['sentiment_mismatches']}\n"
        f"- Missing sentiments: {population['missing_sentiments']}\n"
        f"- Engine failures: {len(manifest['engine_failures'])}\n\n"
        "`identity_snapshot.json` is a prediction-blind, versioned offline benchmark snapshot. "
        "Production continues to use the canonical ClickHouse point-in-time identity tables.\n"
    )


def _normalize_alias(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _benchmark_alias_safe(value: str) -> bool:
    normalized = _normalize_alias(value)
    if not _safe_alias(normalized):
        return False
    return not re.match(
        r"^(?:about|competitor|corporate overview|for|reuters|source|the company)\b",
        normalized,
    ) and normalized not in {"nasdaq stock market", "new york stock exchange"}


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "unknown"
