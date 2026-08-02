from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.text_intelligence.scoped_labeling_v1.news_identity import IssuerIdentity, NewsIssuerResolver
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .comparison import evaluate_predictions, load_collection
from .deterministic_v9 import classify_news_document_v9
from .run_deterministic_news_v6 import DEFAULT_FROZEN, DEFAULT_ROOT, _frozen_ids, _headline
from .storage import assert_runtime_root, write_json_atomic


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
    for index, item in enumerate(items, 1):
        result = _predict(item)
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


def _predict(item) -> dict:
    source = item.blinded
    publication = source["publication"]
    rendered = source["rendered_product"]
    identities = []
    for candidate in source.get("point_in_time_issuer_candidates") or ():
        ticker = str(candidate.get("canonical_instrument_id") or candidate.get("ticker") or "").upper()
        aliases = tuple(
            evidence.split(":", 1)[1]
            for evidence in candidate.get("identity_evidence") or ()
            if str(evidence).startswith("issuer_alias:")
        )
        identities.append(IssuerIdentity(ticker=ticker, issuer_id=f"calibration:{ticker}", aliases=aliases))
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
            "issuer_identities": tuple({
                "ticker": identity.ticker, "issuer_id": identity.issuer_id, "aliases": identity.aliases,
            } for identity in identities),
        },
    )
    return classify_news_document_v9(
        document,
        issuer_resolver=NewsIssuerResolver(identities, article_tickers=provider_tickers),
    ).as_dict()


if __name__ == "__main__":
    raise SystemExit(main())
