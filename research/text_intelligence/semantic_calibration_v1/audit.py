from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .schema import SAMPLE_VERSION, stable_json_hash
from .storage import assert_runtime_root, read_json, write_json_atomic


FORBIDDEN_BLINDED_KEYS = {
    "v5",
    "locked_split",
    "semantic_score",
    "semantic_direction",
    "event_concepts",
    "forecast_trigger_eligible",
    "reaction_evaluation_eligible",
    "issuer_history_context_eligible",
    "observed_direction",
    "observed_move_pct",
    "subsequent_price_reaction",
}


def audit_sample(root: Path, *, write_report: bool = True) -> dict[str, Any]:
    assert_runtime_root(root)
    manifest = read_json(root / "sample_manifest.json")
    errors: list[str] = []
    if manifest.get("sample_version") != SAMPLE_VERSION:
        errors.append("sample_version_mismatch")
    expected_manifest_hash = manifest.get("sample_manifest_sha256")
    unhashed_manifest = dict(manifest)
    unhashed_manifest.pop("sample_manifest_sha256", None)
    if stable_json_hash(unhashed_manifest) != expected_manifest_hash:
        errors.append("sample_manifest_hash_mismatch")

    items = list(manifest.get("items") or ())
    sample_ids = [str(item.get("sample_id") or "") for item in items]
    source_ids = [str(item.get("source_id") or "") for item in items]
    if len(sample_ids) != int(manifest.get("sample_count") or -1):
        errors.append("manifest_count_mismatch")
    if len(sample_ids) != len(set(sample_ids)):
        errors.append("duplicate_sample_id")
    if len(source_ids) != len(set(source_ids)):
        errors.append("duplicate_source_id")

    era_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    rendered_quality: Counter[str] = Counter()
    source_lane_counts: Counter[str] = Counter()
    identity_match_counts: Counter[str] = Counter()
    for summary in items:
        sample_id = str(summary["sample_id"])
        path = root / "blinded_articles" / f"{sample_id}.json"
        if not path.exists():
            errors.append(f"missing_blinded_item:{sample_id}")
            continue
        article = read_json(path)
        article_hash = article.get("blinded_item_sha256")
        unhashed_article = dict(article)
        unhashed_article.pop("blinded_item_sha256", None)
        if stable_json_hash(unhashed_article) != article_hash:
            errors.append(f"blinded_hash_mismatch:{sample_id}")
        if article_hash != summary.get("blinded_item_sha256"):
            errors.append(f"manifest_blinded_hash_mismatch:{sample_id}")
        for field in ("source_id", "source_timestamp", "source_text_sha256"):
            if str(article.get(field) or "") != str(summary.get(field) or ""):
                errors.append(f"{field}_mismatch:{sample_id}")
        forbidden = sorted(FORBIDDEN_BLINDED_KEYS & recursive_keys(article))
        if forbidden:
            errors.append(f"blinding_violation:{sample_id}:{','.join(forbidden)}")
        rendered = article.get("rendered_product") or {}
        if not str(rendered.get("text") or "").strip():
            errors.append(f"empty_rendered_text:{sample_id}")
        timestamp = str(article.get("source_timestamp") or "")
        era_counts[era(timestamp)] += 1
        ticker_count = len((article.get("publication") or {}).get("provider_tickers") or ())
        scope_counts["zero" if ticker_count == 0 else "single" if ticker_count == 1 else "multi"] += 1
        for flag in rendered.get("quality_flags") or ():
            rendered_quality[str(flag)] += 1
        for lane in article.get("source_lanes") or ():
            source_lane_counts[str(lane.get("source_kind") or "unknown")] += 1
        matches = len(article.get("point_in_time_issuer_candidates") or ())
        identity_match_counts["zero" if matches == 0 else "one" if matches == 1 else "multiple"] += 1

    sealed_path = root / "sealed" / "v5_comparison_and_splits.json"
    if not sealed_path.exists():
        errors.append("sealed_comparison_missing")
    else:
        sealed = read_json(sealed_path)
        expected = sealed.get("sealed_comparison_sha256")
        unhashed = dict(sealed)
        unhashed.pop("sealed_comparison_sha256", None)
        if stable_json_hash(unhashed) != expected:
            errors.append("sealed_comparison_hash_mismatch")
        sealed_ids = [str(value.get("sample_id") or "") for value in sealed.get("items") or ()]
        if sealed_ids != sample_ids:
            errors.append("sealed_identity_order_mismatch")

    report = {
        "sample_version": SAMPLE_VERSION,
        "sample_manifest_sha256": expected_manifest_hash,
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "sample_count": len(items),
        "pilot_count": sum(bool(item.get("pilot")) for item in items),
        "era_counts": dict(sorted(era_counts.items())),
        "provider_ticker_scope_counts": dict(sorted(scope_counts.items())),
        "identity_match_counts": dict(sorted(identity_match_counts.items())),
        "source_lane_counts": dict(sorted(source_lane_counts.items())),
        "top_rendered_quality_flags": rendered_quality.most_common(20),
    }
    if write_report:
        write_json_atomic(root / "sample_audit.json", report)
    return report


def recursive_keys(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        result = {str(key) for key in value}
        for child in value.values():
            result.update(recursive_keys(child))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for child in value:
            result.update(recursive_keys(child))
        return result
    return set()


def era(timestamp: str) -> str:
    year = int(timestamp[:4])
    if year < 2015:
        return "2010_2014"
    if year < 2020:
        return "2015_2019"
    if year < 2024:
        return "2020_2023"
    return "2024_2026"
