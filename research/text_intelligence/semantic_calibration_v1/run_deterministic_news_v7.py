from __future__ import annotations

import argparse
import json
from pathlib import Path

from research.text_intelligence.scoped_labeling_v1.news_identity import IssuerIdentity, NewsIssuerResolver
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .comparison import evaluate_predictions, load_collection
from .deterministic_v7 import classify_news_document_v7
from .run_deterministic_news_v6 import DEFAULT_FROZEN, DEFAULT_ROOT, _frozen_ids, _headline
from .storage import assert_runtime_root, write_json_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and evaluate deterministic News V7.")
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--frozen-sample", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--phase", choices=("development", "frozen-acceptance"), default="development")
    args = parser.parse_args()
    output = args.runtime_root / "deterministic_v7"
    assert_runtime_root(output)
    metrics_path = output / f"{args.phase}_metrics.json"
    if args.phase == "frozen-acceptance" and metrics_path.exists():
        raise RuntimeError("Frozen V7 acceptance already exists; create a new authority version.")
    items = load_collection(args.runtime_root)
    frozen_ids = _frozen_ids(args.frozen_sample)
    selected = tuple(
        item for item in items
        if (item.sample_id in frozen_ids) == (args.phase == "frozen-acceptance")
    )
    expected = 100 if args.phase == "frozen-acceptance" else 900
    if len(frozen_ids) != 100 or len(selected) != expected:
        raise RuntimeError(f"Invalid immutable split: frozen={len(frozen_ids)} selected={len(selected)} expected={expected}")
    prediction_dir = output / f"{args.phase}_predictions"
    for index, item in enumerate(selected, 1):
        result = _predict(item)
        result.update({"sample_id": item.sample_id, "split": item.split, "source_id": item.blinded["source_id"]})
        write_json_atomic(prediction_dir / f"{item.sample_id}.json", result)
        if index % 100 == 0 or index == len(selected):
            print(f"V7 {args.phase} {index:,}/{len(selected):,}", flush=True)
    report = evaluate_predictions(selected, prediction_dir=prediction_dir, canonical_concepts=True)
    write_json_atomic(metrics_path, report)
    print(json.dumps(_headline(report), indent=2), flush=True)
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
    metadata = {
        "author": publication.get("author") or "",
        "provider": publication.get("provider") or "",
        "provider_tags": publication.get("provider_tags") or (),
        "channels": publication.get("channels") or (),
        "teaser": publication.get("teaser") or "",
        "url_domain": publication.get("url_domain") or "",
        "issuer_identities": tuple({"ticker": value.ticker, "issuer_id": value.issuer_id, "aliases": value.aliases} for value in identities),
    }
    document = SemanticDocument(
        corpus="news",
        source_id=str(source["source_id"]),
        timestamp=str(source["source_timestamp"]),
        title=str(publication.get("title") or ""),
        text=str(rendered.get("text") or ""),
        tickers=provider_tickers,
        metadata=metadata,
    )
    return classify_news_document_v7(
        document,
        issuer_resolver=NewsIssuerResolver(identities, article_tickers=provider_tickers),
    ).as_dict()


if __name__ == "__main__":
    raise SystemExit(main())

