from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files  # noqa: E402
from research.text_intelligence.news_synthesis_v1.deepfm_serving import (  # noqa: E402
    DeepFMServingRelease,
)
from research.text_intelligence.news_synthesis_v1.engine import (  # noqa: E402
    IssuerIdentity,
    IssuerIdentityIndex,
    NewsSynthesisEngine,
)
from research.text_intelligence.news_synthesis_v1.text_contract import (  # noqa: E402
    BODY_TEXT_CONTRACT_VERSION,
    MODEL_TEXT_CONTRACT_VERSION,
    body_v3_source_row,
)


AUDIT_VERSION = "news_body_v3_downstream_drift_v1"
RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence\news_body_v3_downstream_drift")
ASSIGNMENTS = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_v59_calibrated_reaudit_v2"
    r"\EVALUATION_ASSIGNMENTS.csv"
)
RELEASE_MANIFEST = Path(
    r"D:\TradingML\runtimes\text_intelligence\serving"
    r"\news_forecast_funnel_v1\release_v2.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_scope(*, offset: int, limit: int | None) -> list[dict[str, str]]:
    if not ASSIGNMENTS.is_file():
        raise FileNotFoundError(ASSIGNMENTS)
    with ASSIGNMENTS.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 352_559:
        raise ValueError(f"assignment population changed: {len(rows):,}")
    # Source-id ordering keeps bounded runs deterministic without concentrating
    # the sample in whichever split sorts first.
    rows.sort(key=lambda row: str(row["source_id"]))
    return rows[offset:] if limit is None else rows[offset : offset + limit]


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _query_sources(
    client: ClickHouseHttpClient,
    *,
    database: str,
    source_ids: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for ids in _chunks(source_ids, 750):
        values = ",".join(sql_string(value) for value in ids)
        query = f"""
SELECT e.canonical_news_id source_id,toString(e.published_at_utc) source_timestamp,
       e.provider,e.title,e.author,e.article_url,e.url_domain,e.tickers,e.channels,
       e.provider_tags,e.content_quality_flags,e.source_revision_key,
       if(empty(v2.rendered_text),e.title,v2.rendered_text) v2_text,
       if(empty(v2.rendered_text_hash),hex(SHA256(e.title)),v2.rendered_text_hash) v2_text_hash,
       v3.canonical_body_text AS canonical_body_text,v3.body_hash AS body_hash,
       v3.body_status AS body_status,v3.text_contract AS body_text_contract,
       v3.quality_flags AS body_quality_flags
FROM `{database}`.`benzinga_news_event_v2` e FINAL
LEFT JOIN `{database}`.`benzinga_news_rendered_v2` v2 FINAL
  ON v2.published_date=e.published_date
 AND v2.provider_article_id=e.provider_article_id
 AND v2.source_revision_key=e.source_revision_key
LEFT JOIN `{database}`.`benzinga_news_rendered_v3` v3 FINAL
  ON v3.published_date=e.published_date
 AND v3.provider_article_id=e.provider_article_id
WHERE e.canonical_news_id IN ({values})
FORMAT JSONEachRow
"""
        for row in client.iter_json_each_row(query):
            source_id = str(row["source_id"])
            if source_id in result:
                raise ValueError(f"duplicate source row: {source_id}")
            result[source_id] = row
        print(
            f"LOAD status=active completed={len(result):,}/{len(source_ids):,} "
            f"queued={len(source_ids) - len(result):,} failed=0",
            flush=True,
        )
    return result


def _optional_date(value: Any) -> date | None:
    clean = str(value or "")[:10]
    return date.fromisoformat(clean) if clean and clean != "0000-00-00" else None


def _identity_index(
    client: ClickHouseHttpClient, *, database: str, tickers: set[str]
) -> IssuerIdentityIndex:
    values = ",".join(sql_string(value) for value in sorted(tickers))
    rows = client.iter_json_each_row(f"""
SELECT upperUTF8(sym.ticker_normalized) ticker,sec.issuer_id AS issuer_id,
       sec.security_id AS security_id,
       coalesce(nullIf(issuer.branding_name,''),nullIf(issuer.issuer_name,''),
                nullIf(issuer.legal_name,''),sym.display_name) display_name,
       arrayFilter(value -> notEmpty(value),[ifNull(issuer.issuer_name,''),
         ifNull(issuer.legal_name,''),ifNull(issuer.branding_name,''),
         ifNull(sec.security_name,''),ifNull(sym.display_name,'')]) aliases,
       listing.exchange_code AS exchange_code,toString(listing.list_date) list_date,
       toString(listing.delisted_date) delisted_date
FROM `{database}`.`id_symbol_v1` sym FINAL
INNER JOIN `{database}`.`id_listing_v1` listing FINAL ON listing.listing_id=sym.listing_id
INNER JOIN `{database}`.`id_security_v1` sec FINAL ON sec.security_id=listing.security_id
INNER JOIN `{database}`.`id_issuer_v1` issuer FINAL ON issuer.issuer_id=sec.issuer_id
WHERE sym.ticker_normalized!='' AND sec.issuer_id!='' AND listing.currency_code='USD'
  AND upperUTF8(sym.ticker_normalized) IN ({values})
FORMAT JSONEachRow
""")
    identities = [
        IssuerIdentity(
            ticker=str(row["ticker"]),
            issuer_id=str(row["issuer_id"]),
            display_name=str(row["display_name"]),
            aliases=tuple(str(value).strip() for value in row.get("aliases") or () if str(value).strip()),
            security_id=str(row.get("security_id") or ""),
            exchange_code=str(row.get("exchange_code") or ""),
            list_date=_optional_date(row.get("list_date")),
            delisted_date=_optional_date(row.get("delisted_date")),
        )
        for row in rows
        if row.get("aliases")
    ]
    return IssuerIdentityIndex(identities)


def _synthesis_signature(engine: NewsSynthesisEngine, source: Mapping[str, Any]) -> tuple[Any, ...]:
    if not str(source.get("text") or source.get("title") or "").strip():
        return ("insufficient_information", "missing", "missing", "missing")
    document = engine.synthesize(source)
    forecast = [row for row in document["eligibility"] if row["product"] == "forecast_trigger"]
    envelope = document["envelope"]
    return (
        "eligible" if any(bool(row["eligible"]) for row in forecast) else "ineligible",
        envelope["document_structure"]["value"],
        envelope["communication_purpose"]["value"],
        envelope["information_origin"]["value"],
    )


def _cosine(left: Any, right: Any) -> float:
    numerator = float(left.multiply(right).sum())
    denominator = math.sqrt(float(left.multiply(left).sum()) * float(right.multiply(right).sum()))
    return numerator / denominator if denominator else 1.0 if left.nnz == right.nnz == 0 else 0.0


def _source(row: Mapping[str, Any], text: str, text_hash: str, *, body_status: str = "") -> dict[str, Any]:
    return {
        "source_id": str(row["source_id"]),
        "source_timestamp": str(row["source_timestamp"]),
        "provider": row.get("provider"),
        "title": row.get("title"),
        "author": row.get("author"),
        "article_url": row.get("article_url"),
        "url_domain": row.get("url_domain"),
        "text": text,
        "tickers": row.get("tickers") or [],
        "channels": row.get("channels") or [],
        "provider_tags": row.get("provider_tags") or [],
        "content_quality_flags": row.get("content_quality_flags") or [],
        "quality_flags": row.get("body_quality_flags") or [],
        "source_revision_key": row.get("source_revision_key") or "",
        "rendered_text_hash": text_hash,
        "body_status": body_status,
    }


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    parser = argparse.ArgumentParser(
        description="Measure Body V2 to V3 drift without mutating labels, models, or live authorities."
    )
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--offset", type=int, default=0, help="Deterministic source-id offset for restartable tranches.")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--full", action="store_true", help="Audit all 352,559 assigned articles.")
    parser.add_argument("--output-root", type=Path, default=RUNTIME_ROOT)
    parser.add_argument("--release-manifest", type=Path, default=RELEASE_MANIFEST)
    args = parser.parse_args()
    if args.offset < 0:
        raise ValueError("--offset must be non-negative")
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    command = [
        sys.executable,
        "scripts/audit_news_body_v3_downstream_drift.py",
        "--database", args.database,
        "--offset", str(args.offset),
        "--limit", str(args.limit),
        "--output-root", str(args.output_root),
        "--release-manifest", str(args.release_manifest),
    ]
    if args.full:
        command.append("--full")
    print("COMMAND " + " ".join(command), flush=True)
    limit = None if args.full else args.limit
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S_%f")
    output = args.output_root / run_id
    building = output.with_name(output.name + ".building")
    if output.exists() or building.exists():
        raise FileExistsError(output)
    building.mkdir(parents=True)
    started = time.monotonic()
    scope = _load_scope(offset=args.offset, limit=limit)
    if not scope:
        raise ValueError("selected audit tranche is empty")
    source_ids = [str(row["source_id"]) for row in scope]
    split_by_id = {str(row["source_id"]): str(row["population_split"]) for row in scope}
    print(
        f"PREFLIGHT status=passed audit={AUDIT_VERSION} population={len(scope):,} "
        f"offset={args.offset:,} mode={'full' if args.full else 'bounded'} "
        "labels_mutated=0 live_mutated=0",
        flush=True,
    )

    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=300
    )
    failures: list[dict[str, str]] = []
    try:
        sources = _query_sources(client, database=args.database, source_ids=source_ids)
        missing = set(source_ids) - set(sources)
        if missing:
            raise RuntimeError(f"source coverage mismatch: missing={len(missing):,}")
        tickers = {
            str(ticker).strip().upper()
            for row in sources.values()
            for ticker in row.get("tickers") or ()
            if str(ticker).strip()
        }
        engine = NewsSynthesisEngine(_identity_index(client, database=args.database, tickers=tickers))
    finally:
        client.close()

    release = DeepFMServingRelease(args.release_manifest)
    counters: Counter[str] = Counter()
    split_counters: dict[str, Counter[str]] = {}
    cosine_values: list[float] = []
    probability_deltas: list[float] = []
    details_path = building / "PAIRED_DRIFT.jsonl"
    with details_path.open("x", encoding="utf-8", newline="\n") as handle:
        for index, source_id in enumerate(source_ids, start=1):
            row = sources[source_id]
            split = split_by_id[source_id]
            split_counter = split_counters.setdefault(split, Counter())
            stage = "derive"
            try:
                derived = body_v3_source_row(row)
                old_text = str(row.get("v2_text") or row.get("title") or "")
                new_text = str(derived["text"])
                old_model_text_hash = hashlib.sha256(old_text.encode("utf-8")).hexdigest()
                model_text_hash_changed = old_model_text_hash != str(derived["model_text_hash"])
                old = _source(row, old_text, str(row.get("v2_text_hash") or ""))
                new = _source(
                    row, new_text, str(derived["model_text_hash"]), body_status=str(derived["body_status"])
                )
                stage = "v2_synthesis"
                old_synthesis = _synthesis_signature(engine, old)
                stage = "v3_synthesis"
                new_synthesis = _synthesis_signature(engine, new)
                stage = "tfidf"
                old_vector = release.vectorizer.transform([old_text])
                new_vector = release.vectorizer.transform([new_text])
                cosine = _cosine(old_vector, new_vector)
                stage = "v2_deepfm"
                old_score = release.score(old, threshold=0.5)
                stage = "v3_deepfm"
                new_score = release.score(new, threshold=0.5)
                delta = float(new_score["eligible_probability"]) - float(old_score["eligible_probability"])
                label_flip = old_score["forecast_eligibility"] != new_score["forecast_eligibility"]
                synthesis_flip = old_synthesis != new_synthesis
                for counter in (counters, split_counter):
                    counter["articles"] += 1
                    counter[f"body_status:{derived['body_status']}"] += 1
                    counter["model_text_hash_changes"] += int(model_text_hash_changed)
                    counter["tfidf_changed"] += int(cosine < 1.0 - 1e-12)
                    counter["deepfm_label_flips_at_0.5"] += int(label_flip)
                    counter["synthesis_signature_changes"] += int(synthesis_flip)
                cosine_values.append(cosine)
                probability_deltas.append(delta)
                handle.write(json.dumps({
                    "source_id": source_id,
                    "population_split": split,
                    "body_status": derived["body_status"],
                    "v2_chars": len(old_text),
                    "v3_model_chars": len(new_text),
                    "model_text_hash_changed": model_text_hash_changed,
                    "tfidf_cosine": cosine,
                    "v2_synthesis": old_synthesis,
                    "v3_synthesis": new_synthesis,
                    "v2_deepfm_probability": old_score["eligible_probability"],
                    "v3_deepfm_probability": new_score["eligible_probability"],
                    "deepfm_probability_delta": delta,
                    "deepfm_label_flip_at_0.5": label_flip,
                }, sort_keys=True) + "\n")
            except Exception as exc:
                counters["failed"] += 1
                split_counter["failed"] += 1
                failures.append({
                    "source_id": source_id,
                    "stage": stage,
                    "error": f"{type(exc).__name__}: {exc}",
                    "has_timestamp": bool(str(row.get("source_timestamp") or "").strip()),
                    "has_title": bool(str(row.get("title") or "").strip()),
                    "has_v2_text": bool(str(row.get("v2_text") or "").strip()),
                    "has_v3_body": bool(str(row.get("canonical_body_text") or "").strip()),
                })
            if index % 100 == 0 or index == len(source_ids):
                print(
                    f"AUDIT status=active completed={index:,}/{len(source_ids):,} "
                    f"queued={len(source_ids) - index:,} failed={len(failures):,}",
                    flush=True,
                )

    def distribution(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"min": None, "mean": None, "max": None}
        return {"min": min(values), "mean": sum(values) / len(values), "max": max(values)}

    report = {
        "status": "passed" if not failures else "failed",
        "audit_version": AUDIT_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "mode": "full" if args.full else "bounded",
        "population_offset": args.offset,
        "population_articles": len(scope),
        "contracts": {
            "body": BODY_TEXT_CONTRACT_VERSION,
            "model_text": MODEL_TEXT_CONTRACT_VERSION,
            "legacy_release": release.release_id,
            "legacy_release_hash": release.release_hash,
            "diagnostic_threshold": 0.5,
        },
        "mutation_guards": {"labels_mutated": False, "models_mutated": False, "live_authority_mutated": False},
        "overall": dict(counters),
        "splits": {name: dict(value) for name, value in sorted(split_counters.items())},
        "tfidf_cosine": distribution(cosine_values),
        "deepfm_probability_delta": distribution(probability_deltas),
        "failures": failures,
        "interpretation": {
            "deepfm": "Diagnostic replay through the frozen V2 model; not a V3 promotion result.",
            "embeddings": "A model-text hash change invalidates the V2 embedding input; vector drift requires a separately versioned paid rebuild.",
            "holdout": "Any holdout rows are measurement-only and must not tune text composition or thresholds.",
            "next_gate": "Freeze operator labels, train a versioned V3 successor, then evaluate once on a later sealed time-forward holdout.",
        },
        "authority": {
            "assignments": str(ASSIGNMENTS),
            "assignments_sha256": _sha256(ASSIGNMENTS),
            "release_manifest": str(args.release_manifest),
            "release_manifest_sha256": _sha256(args.release_manifest),
        },
    }
    _json_dump(building / "REPORT.json", report)
    _json_dump(building / "HASH_MANIFEST.json", {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(building.iterdir())
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    })
    building.replace(output)
    print(
        f"COMPLETE status={report['status']} output={output} completed={counters['articles']:,} "
        f"failed={len(failures):,} queued=0",
        flush=True,
    )
    if failures:
        raise RuntimeError(f"audit failed for {len(failures):,} articles; see {output / 'REPORT.json'}")


if __name__ == "__main__":
    main()
