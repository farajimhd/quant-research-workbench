from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    ISSUER_IDENTITY_AUTHORITY_VERSION,
    NewsIssuerResolver,
    load_news_issuer_resolver,
)
from research.text_intelligence.scoped_labeling_v1.news_extractor import (
    NEWS_EXTRACTOR_VERSION,
)
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .comparison import evaluate_predictions, load_collection
from .deterministic_v9 import classify_news_document_v9
from .deterministic_v9_config import CALIBRATION_VERSION, DETERMINISTIC_V9_VERSION
from .run_deterministic_news_v6 import DEFAULT_FROZEN, DEFAULT_ROOT, _frozen_ids, _headline
from .storage import assert_runtime_root, read_json, write_json_atomic


_WORKER_ISSUER_AUTHORITY: NewsIssuerResolver | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic News V9 against the human collection.")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--frozen-sample", type=Path, default=DEFAULT_FROZEN)
    args = parser.parse_args()
    output = args.runtime_root / "deterministic_v9"
    assert_runtime_root(output)
    items = load_collection(args.runtime_root)
    frozen_ids = _frozen_ids(args.frozen_sample)
    if len(items) != 1_000 or len(frozen_ids) != 100:
        raise RuntimeError(f"Invalid human authority: items={len(items)} frozen={len(frozen_ids)}")
    prediction_dir = output / "human_predictions"
    issuer_resolver = load_v9_issuer_authority()
    for index, item in enumerate(items, 1):
        result = _predict(item, issuer_resolver=issuer_resolver)
        result.update({"sample_id": item.sample_id, "split": item.split, "source_id": item.blinded["source_id"]})
        write_json_atomic(prediction_dir / f"{item.sample_id}.json", result)
        if index % 100 == 0 or index == len(items):
            print(f"V9 HUMAN {index:,}/{len(items):,}", flush=True)
    reports = {
        "all": evaluate_predictions(items, prediction_dir=prediction_dir, canonical_concepts=True),
        "development_900": evaluate_predictions(
            (item for item in items if item.sample_id not in frozen_ids),
            prediction_dir=prediction_dir,
            canonical_concepts=True,
        ),
        "historical_frozen_100": evaluate_predictions(
            (item for item in items if item.sample_id in frozen_ids),
            prediction_dir=prediction_dir,
            canonical_concepts=True,
        ),
    }
    write_json_atomic(output / "human_metrics.json", reports)
    print(json.dumps({name: _headline(report) for name, report in reports.items()}, indent=2), flush=True)
    return 0


def load_v9_issuer_authority(*, database: str = "q_live") -> NewsIssuerResolver:
    """Load the complete point-in-time issuer reference once per V9 process."""
    repo_root = Path(__file__).resolve().parents[3]
    load_env_files(discover_env_files(repo_root), verbose=True)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    resolver = load_news_issuer_resolver(client, database=database)
    print(
        "V9 ISSUER AUTHORITY "
        f"version={ISSUER_IDENTITY_AUTHORITY_VERSION} "
        f"identities={resolver.identity_count:,} tickers={resolver.ticker_count:,}",
        flush=True,
    )
    return resolver


def _predict(item, *, issuer_resolver: NewsIssuerResolver) -> dict:
    source = item.blinded
    publication = source["publication"]
    rendered = source["rendered_product"]
    provider_tickers = tuple(str(value).upper() for value in publication.get("provider_tickers") or ())
    document = SemanticDocument(
        corpus="news",
        source_id=str(source["source_id"]),
        timestamp=str(source["source_timestamp"]),
        title=str(publication.get("title") or ""),
        text=str(rendered.get("text") or ""),
        tickers=provider_tickers,
        metadata={
            "author": publication.get("author") or "",
            "provider": publication.get("provider") or "",
            "provider_tags": publication.get("provider_tags") or (),
            "channels": publication.get("channels") or (),
            "teaser": publication.get("teaser") or "",
            "url_domain": publication.get("url_domain") or "",
        },
    )
    result = classify_news_document_v9(
        document,
        issuer_resolver=issuer_resolver,
    ).as_dict()
    combined_text = "\n".join(
        value
        for value in (
            str(publication.get("title") or ""),
            str(publication.get("teaser") or ""),
            str(rendered.get("text") or ""),
        )
        if value
    )
    resolved = issuer_resolver.with_article_identities(combined_text).resolve(
        combined_text,
        timestamp=str(source["source_timestamp"]),
        linked_tickers=provider_tickers,
    )
    title_resolved = issuer_resolver.resolve_title_lead_subjects(
        str(publication.get("title") or ""),
        timestamp=str(source["source_timestamp"]),
        linked_tickers=provider_tickers,
    )
    resolved_by_ticker = {
        match.ticker: match for match in (*resolved, *title_resolved)
    }
    result["identity_resolution"] = {
        "authority_version": ISSUER_IDENTITY_AUTHORITY_VERSION,
        "authority_identity_count": issuer_resolver.identity_count,
        "authority_ticker_count": issuer_resolver.ticker_count,
        "point_in_time_candidates": issuer_resolver.reference_snapshot(
            provider_tickers,
            timestamp=str(source["source_timestamp"]),
        ),
        "resolved_subjects": tuple(
            {
                "ticker": match.ticker,
                "evidence": match.evidence,
            }
            for match in resolved_by_ticker.values()
        ),
    }
    return result


def predict_with_loaded_authority(item) -> dict:
    """Process-worker entry point that loads the reference table once per worker."""
    global _WORKER_ISSUER_AUTHORITY
    if _WORKER_ISSUER_AUTHORITY is None:
        _WORKER_ISSUER_AUTHORITY = load_v9_issuer_authority()
    return _predict(item, issuer_resolver=_WORKER_ISSUER_AUTHORITY)


def prediction_is_current(existing: dict) -> bool:
    """Return whether a cached prediction matches every semantic authority."""
    return (
        str(existing.get("version") or "") == DETERMINISTIC_V9_VERSION
        and str(existing.get("calibration_version") or "") == CALIBRATION_VERSION
        and str(existing.get("scope_extractor_version") or "") == NEWS_EXTRACTOR_VERSION
        and str((existing.get("identity_resolution") or {}).get("authority_version"))
        == ISSUER_IDENTITY_AUTHORITY_VERSION
    )


def generate_v9_predictions(
    items,
    output_dir: Path,
    *,
    issuer_resolver: NewsIssuerResolver,
) -> None:
    """Generate resumable V9 predictions without importing optional V10 ML deps."""
    assert_runtime_root(output_dir)
    for index, item in enumerate(items, 1):
        target = output_dir / f"{item.sample_id}.json"
        if target.exists():
            existing = read_json(target)
            if prediction_is_current(existing):
                continue
        result = _predict(item, issuer_resolver=issuer_resolver)
        result.update({
            "sample_id": item.sample_id,
            "split": item.split,
            "source_id": item.blinded["source_id"],
        })
        write_json_atomic(target, result)
        if index % 100 == 0 or index == len(items):
            print(f"V9 HUMAN {index:,}/{len(items):,}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
