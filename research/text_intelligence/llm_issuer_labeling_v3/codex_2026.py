from __future__ import annotations

import hashlib
import json
import math
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from pipelines.news.benzinga.core.clickhouse_writer_v2 import (
    NewsV2TargetConfig,
    write_news_pipeline_result_v2,
)
from pipelines.news.benzinga.core.item_pipeline import process_benzinga_news_item
from pipelines.news.benzinga.core.url_policy import load_policy
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files

from .pipeline import EXAMPLE_PATH, REPO_ROOT, normalize_source, sha256_bytes, sha256_file, utc_now
from .prompt import build_system_prompt, example_source_ids, load_example_bank
from .schema import OUTPUT_SCHEMA, SCHEMA_VERSION, canonicalize_output, normalize_ticker, validate_output


DATASET_VERSION = "llm_issuer_labeling_codex_2026_v1"
PACKET_VERSION = "llm_issuer_labeling_codex_packet_v1"
INVENTORY_VERSION = "llm_issuer_labeling_codex_inventory_v1"
AGREEMENT_VERSION = "llm_issuer_labeling_codex_agreement_v1"
AUTHORITY_VERSION = "llm_issuer_labeling_codex_authority_v1"
SOURCE_QUERY_VERSION = "q_live_benzinga_event_rendered_2026_v1"
NORMALIZER_VERSION = "llm_issuer_labeling_v3_sentence_normalizer_v1"
DEFAULT_RUNTIME_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v3\codex_2026_v1"
)
DEFAULT_GOLD_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1\gold_certified_news_labels_consolidated_v1"
)
DEFAULT_EXAMPLE_SOURCE_CATALOG = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\consolidated_gold_audit_v1_v48_baseline_20260810\audit_source_catalog.jsonl"
)
START_UTC = "2026-01-01 00:00:00"
END_UTC_EXCLUSIVE = "2027-01-01 00:00:00"
TARGET_PACKET_TOKENS = 25_000
QC_FRACTION = 0.15
FORECAST_THRESHOLD = 0.5
NEAR_THRESHOLD = 0.1
LOW_IDENTITY_CONFIDENCE = 0.75
RISK_TAGS = {
    "analyst_action", "market_observation", "listing", "legal", "acquisition", "other_material"
}
ARTICLE_CLASS_TERMS = {
    "roundup": ("roundup", "stocks moving", "why are", "top stocks", "biggest movers"),
    "recap": ("recap", "closing bell", "market today", "session recap"),
    "analyst": ("analyst", "price target", "upgrade", "downgrade", "initiates coverage"),
    "preview": ("preview", "what to expect", "ahead of earnings", "earnings preview"),
}


class ControllerError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path

    @property
    def inventory(self) -> Path:
        return self.root / "inventory"

    @property
    def frozen(self) -> Path:
        return self.root / "frozen"

    @property
    def packets(self) -> Path:
        return self.root / "packets"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_jsonl(path: Path, values: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for value in values:
            handle.write(canonical_json(value) + "\n")
    temporary.replace(path)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(value) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ControllerError(f"expected JSON object: {path}")
    return value


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ControllerError(f"{path}:{line_number} is not an object")
            yield value


def require_file(path: Path, description: str) -> Path:
    if not path.is_file():
        raise ControllerError(f"{description} is unavailable: {path}")
    return path


def clickhouse_client() -> ClickHouseHttpClient:
    load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    return ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=300,
    )


def source_query(*, include_text: bool) -> str:
    text_projection = ", r.rendered_text AS rendered_text" if include_text else ""
    return f"""
SELECT
 e.canonical_news_id AS source_id,
 e.provider AS provider,
 e.provider_article_id AS provider_article_id,
 toString(e.published_at_utc) AS published_at_utc,
 toString(e.published_date) AS published_date,
 e.title AS title,
 e.raw_artifact_path AS raw_artifact_path,
 e.raw_payload_hash AS original_source_hash,
 e.source_revision_key AS source_revision_key,
 e.renderer_version AS event_renderer_version,
 r.renderer_version AS renderer_version,
 r.text_contract AS text_contract,
 r.rendered_text_hash AS rendered_text_hash,
 lengthUTF8(r.rendered_text) AS rendered_chars,
 r.source_count AS source_count,
 r.block_count AS block_count,
 r.quality_flags AS quality_flags
 {text_projection}
FROM q_live.benzinga_news_event_v2 AS e FINAL
LEFT JOIN q_live.benzinga_news_rendered_v2 AS r FINAL
 ON r.published_date=e.published_date
 AND r.provider_article_id=e.provider_article_id
 AND r.source_revision_key=e.source_revision_key
PREWHERE e.published_at_utc >= toDateTime64('{START_UTC}', 9, 'UTC')
 AND e.published_at_utc < toDateTime64('{END_UTC_EXCLUSIVE}', 9, 'UTC')
ORDER BY e.published_at_utc, e.canonical_news_id
FORMAT JSONEachRow
"""


def source_diagnostics(client: ClickHouseHttpClient) -> dict[str, Any]:
    rows = _one_json(
        client,
        f"""
SELECT
 count() AS total_rows,
 uniqExact(canonical_news_id) AS unique_source_ids,
 uniqExact(provider_article_id) AS unique_provider_article_ids,
 min(published_at_utc) AS min_published_at_utc,
 max(published_at_utc) AS max_published_at_utc,
 groupArrayDistinct(provider) AS providers,
 groupArrayDistinct(renderer_version) AS renderer_versions
FROM q_live.benzinga_news_event_v2 FINAL
PREWHERE published_at_utc >= toDateTime64('{START_UTC}', 9, 'UTC')
 AND published_at_utc < toDateTime64('{END_UTC_EXCLUSIVE}', 9, 'UTC')
FORMAT JSONEachRow
""",
    )
    conflicts = _one_json(
        client,
        f"""
SELECT
 countIf(row_count > 1) AS duplicate_source_identity_groups,
 countIf(provider_ids > 1 OR timestamps > 1 OR source_revisions > 1) AS conflicting_source_identity_groups,
 sum(greatest(row_count - 1, 0)) AS duplicate_rows
FROM
(
 SELECT canonical_news_id, count() AS row_count,
  uniqExact(provider_article_id) AS provider_ids,
  uniqExact(published_at_utc) AS timestamps,
  uniqExact(source_revision_key) AS source_revisions
 FROM q_live.benzinga_news_event_v2 FINAL
 PREWHERE published_at_utc >= toDateTime64('{START_UTC}', 9, 'UTC')
  AND published_at_utc < toDateTime64('{END_UTC_EXCLUSIVE}', 9, 'UTC')
 GROUP BY canonical_news_id
)
FORMAT JSONEachRow
""",
    )
    authority = [
        json.loads(line)
        for line in client.execute(
            "SELECT * FROM q_live.benzinga_news_render_authority_v2 FINAL "
            "ORDER BY updated_at_utc DESC LIMIT 5 FORMAT JSONEachRow"
        ).splitlines()
        if line.strip()
    ]
    return {**rows, **conflicts, "render_authority_rows": authority}


def _one_json(client: ClickHouseHttpClient, sql: str) -> dict[str, Any]:
    lines = [line for line in client.execute(sql).splitlines() if line.strip()]
    if len(lines) != 1:
        raise ControllerError(f"expected one ClickHouse row, received {len(lines)}")
    return json.loads(lines[0])


def load_gold_identity(gold_root: Path) -> tuple[dict[str, Any], set[str], str]:
    manifest_path = require_file(gold_root / "manifest.json", "consolidated gold manifest")
    labels_path = require_file(gold_root / "gold_labels.jsonl", "consolidated gold article authority")
    manifest = read_json(manifest_path)
    declared = manifest.get("files", {}).get("gold_labels.jsonl", {})
    actual_labels_hash = sha256_file(labels_path)
    if declared.get("sha256") != actual_labels_hash:
        raise ControllerError("consolidated gold article hash does not match its manifest")
    source_ids: set[str] = set()
    for row in iter_jsonl(labels_path):
        source_id = str(row.get("source_id") or "")
        if not source_id or source_id in source_ids:
            raise ControllerError(f"invalid or duplicate consolidated gold source_id: {source_id!r}")
        source_ids.add(source_id)
    if len(source_ids) != int(manifest.get("population", {}).get("articles", -1)):
        raise ControllerError("consolidated gold article count does not reconcile")
    ids_hash = sha256_json(sorted(source_ids))
    if ids_hash != manifest.get("article_source_ids_sha256"):
        raise ControllerError("consolidated gold source-ID hash does not reconcile")
    return manifest, source_ids, sha256_file(manifest_path)


def inventory(runtime_root: Path, gold_root: Path = DEFAULT_GOLD_ROOT) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    gold_manifest, gold_ids, gold_manifest_hash = load_gold_identity(gold_root)
    bank = load_example_bank(EXAMPLE_PATH)
    examples = example_source_ids(bank)
    client = clickhouse_client()
    diagnostics = source_diagnostics(client)
    selected: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: dict[str, tuple[str, str, str]] = {}
    renderer_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    missing_rendered = 0
    valid_rendered = 0
    example_population_overlap = 0
    for row in client.iter_json_each_row(source_query(include_text=False)):
        source_id = str(row.get("source_id") or "")
        timestamp = str(row.get("published_at_utc") or "")
        identity = (
            str(row.get("provider_article_id") or ""),
            timestamp,
            str(row.get("source_revision_key") or ""),
        )
        reasons: list[str] = []
        if not source_id:
            reasons.append("missing_source_id")
        if not timestamp:
            reasons.append("missing_or_invalid_timestamp")
        if source_id in seen and seen[source_id] != identity:
            reasons.append("conflicting_source_identity")
        seen[source_id] = identity
        rendered_valid = bool(row.get("rendered_text_hash")) and int(row.get("rendered_chars") or 0) > 0
        if not rendered_valid:
            missing_rendered += 1
        else:
            valid_rendered += 1
        if reasons:
            rejected.append({"source_id": source_id, "reasons": reasons, "identity": identity})
            continue
        base = {
            "source_id": source_id,
            "provider": str(row.get("provider") or ""),
            "source_schema": "q_live.benzinga_news_event_v2",
            "provider_article_id": str(row.get("provider_article_id") or ""),
            "published_at_utc": timestamp,
            "published_date": str(row.get("published_date") or ""),
            "title": str(row.get("title") or ""),
            "raw_artifact_path": str(row.get("raw_artifact_path") or ""),
            "original_source_hash": str(row.get("original_source_hash") or ""),
            "source_revision_key": str(row.get("source_revision_key") or ""),
            "renderer_version": str(row.get("renderer_version") or ""),
            "event_renderer_version": str(row.get("event_renderer_version") or ""),
            "text_contract": str(row.get("text_contract") or ""),
            "rendered_text_hash": str(row.get("rendered_text_hash") or ""),
            "rendered_chars": int(row.get("rendered_chars") or 0),
            "source_count": int(row.get("source_count") or 0),
            "block_count": int(row.get("block_count") or 0),
            "quality_flags": sorted(str(value) for value in row.get("quality_flags") or []),
            "render_status": "valid" if rendered_valid else "missing_or_invalid",
        }
        provider_counts[base["provider"]] += 1
        renderer_counts[base["renderer_version"]] += 1
        if source_id in examples:
            example_population_overlap += 1
        if source_id in gold_ids:
            exclusions.append(
                {
                    "source_id": source_id,
                    "reason": "consolidated_certified_gold_source_id",
                    "gold_manifest_sha256": gold_manifest_hash,
                }
            )
        elif source_id in examples:
            exclusions.append(
                {
                    "source_id": source_id,
                    "reason": "v3_prompt_example_source_id",
                    "example_bank_sha256": sha256_file(EXAMPLE_PATH),
                }
            )
        else:
            selected.append(base)
    selected.sort(key=lambda row: (row["published_at_utc"], row["source_id"]))
    exclusions.sort(key=lambda row: row["source_id"])
    rejected.sort(key=lambda row: row["source_id"])
    write_jsonl(paths.inventory / "inventory.jsonl", selected)
    write_jsonl(paths.inventory / "gold_exclusions.jsonl", exclusions)
    write_jsonl(paths.inventory / "rejected.jsonl", rejected)
    report = {
        "inventory_version": INVENTORY_VERSION,
        "created_at_utc": utc_now(),
        "time_boundary": {"start_inclusive": "2026-01-01T00:00:00Z", "end_exclusive": "2027-01-01T00:00:00Z"},
        "source_query_version": SOURCE_QUERY_VERSION,
        "source_authority": "q_live.benzinga_news_event_v2 FINAL",
        "stable_source_identifier": "canonical_news_id",
        "publication_timestamp_authority": "published_at_utc",
        "original_text_authority": "raw_artifact_path plus raw_payload_hash; structured source rows in q_live.benzinga_news_source_v2",
        "rendered_text_authority": "q_live.benzinga_news_rendered_v2 joined by provider identity and source_revision_key",
        "total_authoritative_2026_articles": int(diagnostics["total_rows"]),
        "unique_source_ids": int(diagnostics["unique_source_ids"]),
        "gold_standard_exclusions": sum(row["reason"] == "consolidated_certified_gold_source_id" for row in exclusions),
        "prompt_example_exclusions": sum(row["reason"] == "v3_prompt_example_source_id" for row in exclusions),
        "prompt_example_population_overlap": example_population_overlap,
        "remaining_non_gold_articles": len(selected),
        "already_normalized_rendered_count": valid_rendered,
        "missing_normalized_rendered_count": missing_rendered,
        "rejected_records": len(rejected),
        "rejected_reasons": dict(Counter(reason for row in rejected for reason in row["reasons"])),
        "duplicate_conflicting_source_identities": {
            "duplicate_groups": int(diagnostics.get("duplicate_source_identity_groups") or 0),
            "conflicting_groups": int(diagnostics.get("conflicting_source_identity_groups") or 0),
            "duplicate_rows": int(diagnostics.get("duplicate_rows") or 0),
        },
        "missing_or_invalid_timestamps": sum("missing_or_invalid_timestamp" in row["reasons"] for row in rejected),
        "providers": dict(sorted(provider_counts.items())),
        "source_schemas": {"q_live.benzinga_news_event_v2": int(diagnostics["total_rows"])},
        "renderer_versions": dict(sorted(renderer_counts.items())),
        "source_min_published_at_utc": diagnostics.get("min_published_at_utc"),
        "source_max_published_at_utc": diagnostics.get("max_published_at_utc"),
        "calendar_interval_closed": datetime.now(UTC) >= datetime(2027, 1, 1, tzinfo=UTC),
        "gold_authority": {
            "path": str(gold_root),
            "manifest_sha256": gold_manifest_hash,
            "version": gold_manifest.get("version"),
            "articles": len(gold_ids),
        },
        "files": _hash_files(
            [
                paths.inventory / "inventory.jsonl",
                paths.inventory / "gold_exclusions.jsonl",
                paths.inventory / "rejected.jsonl",
            ]
        ),
    }
    if report["unique_source_ids"] != report["total_authoritative_2026_articles"]:
        raise ControllerError("authoritative 2026 source identities are not unique")
    if report["total_authoritative_2026_articles"] != len(selected) + len(exclusions) + len(rejected):
        raise ControllerError("inventory counts do not reconcile")
    write_json(paths.inventory / "inventory_report.json", report)
    return report


def render_missing(runtime_root: Path, *, execute: bool) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    rows = [row for row in iter_jsonl(require_file(paths.inventory / "inventory.jsonl", "inventory")) if row["render_status"] != "valid"]
    manifest_path = paths.inventory / "rendering_manifest.jsonl"
    completed = {str(row.get("source_id")): row for row in iter_jsonl(manifest_path)} if manifest_path.exists() else {}
    client = clickhouse_client()
    for row in rows:
        source_id = row["source_id"]
        if completed.get(source_id, {}).get("status") == "completed":
            continue
        raw_path = Path(row["raw_artifact_path"])
        if not raw_path.is_file():
            append_jsonl(manifest_path, {"source_id": source_id, "status": "rejected", "reason": "canonical_raw_artifact_unavailable", "updated_at_utc": utc_now()})
            raise ControllerError(f"canonical raw artifact unavailable for {source_id}: {raw_path}")
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ControllerError(f"canonical raw artifact is not an object: {raw_path}")
        result = process_benzinga_news_item(
            payload,
            raw_artifact_path=str(raw_path),
            raw_payload_hash=row["original_source_hash"],
            policy=load_policy(),
            enrichment_rows=[],
        )
        if result.canonical_news_id != source_id:
            raise ControllerError(f"renderer changed source identity for {source_id}")
        if str(result.v2_event_row.get("published_at_utc")) != row["published_at_utc"]:
            raise ControllerError(f"renderer changed publication timestamp for {source_id}")
        summary = write_news_pipeline_result_v2(
            client,
            result,
            config=NewsV2TargetConfig(execute=execute, require_ready=True),
        )
        append_jsonl(
            manifest_path,
            {
                "source_id": source_id,
                "status": "completed" if execute else "dry_run",
                "renderer_version": result.v2_rendered_row.get("renderer_version"),
                "original_source_hash": row["original_source_hash"],
                "rendered_hash": result.v2_rendered_row.get("rendered_text_hash"),
                "normalized_hash": result.normalized_row.get("text_hash"),
                "updated_at_utc": utc_now(),
                "write_status": summary.status,
            },
        )
    report = {
        "missing_requested": len(rows),
        "execute": execute,
        "status": "complete" if not rows or execute else "dry_run",
        "manifest": str(manifest_path),
    }
    write_json(paths.inventory / "rendering_report.json", report)
    return report


def _load_example_inputs(catalog_path: Path, bank: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    needed = example_source_ids(bank)
    found: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(require_file(catalog_path, "approved V3 example source catalog")):
        source_id = str(row.get("source_id") or "")
        if source_id in needed:
            found[source_id] = normalize_source(row)
            if len(found) == len(needed):
                break
    missing = sorted(needed - set(found))
    if missing:
        raise ControllerError(f"approved V3 example inputs unavailable: {missing}")
    return found


def _git_commit() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _assert_controller_source_clean() -> None:
    relative = Path(__file__).resolve().parent.relative_to(REPO_ROOT)
    output = subprocess.check_output(["git", "status", "--porcelain", "--", str(relative)], cwd=REPO_ROOT, text=True)
    if output.strip():
        raise ControllerError("freeze requires committed V3 controller source; task-owned changes are still dirty")


def freeze(runtime_root: Path, example_catalog: Path = DEFAULT_EXAMPLE_SOURCE_CATALOG) -> dict[str, Any]:
    _assert_controller_source_clean()
    paths = RuntimePaths(runtime_root)
    inventory_path = require_file(paths.inventory / "inventory.jsonl", "inventory")
    inventory_report = read_json(require_file(paths.inventory / "inventory_report.json", "inventory report"))
    missing = [row for row in iter_jsonl(inventory_path) if row["render_status"] != "valid"]
    if missing:
        raise ControllerError(f"cannot freeze with {len(missing)} missing or invalid rendered artifacts")
    included_meta = {row["source_id"]: row for row in iter_jsonl(inventory_path)}
    bank = load_example_bank(EXAMPLE_PATH)
    example_inputs = _load_example_inputs(example_catalog, bank)
    system_prompt = build_system_prompt(bank, example_inputs)
    prompt_hash = sha256_bytes(system_prompt.encode("utf-8"))
    population_path = paths.frozen / "population.jsonl"
    normalized_count = 0
    seen: set[str] = set()

    def frozen_rows() -> Iterator[dict[str, Any]]:
        nonlocal normalized_count
        client = clickhouse_client()
        for source in client.iter_json_each_row(source_query(include_text=True)):
            source_id = str(source.get("source_id") or "")
            meta = included_meta.get(source_id)
            if meta is None:
                continue
            if source_id in seen:
                raise ControllerError(f"duplicate frozen source: {source_id}")
            seen.add(source_id)
            if str(source.get("rendered_text_hash") or "") != meta["rendered_text_hash"]:
                raise ControllerError(f"rendered hash drifted after inventory: {source_id}")
            shaped = {
                "source_id": source_id,
                "source_schema": meta["source_schema"],
                "source_lineage": {
                    "provider_article_id": meta["provider_article_id"],
                    "original_source_hash": meta["original_source_hash"],
                    "rendered_text_hash": meta["rendered_text_hash"],
                    "source_revision_key": meta["source_revision_key"],
                    "renderer_version": meta["renderer_version"],
                    "text_contract": meta["text_contract"],
                },
                "source_record": {
                    "publication": {
                        "title": meta["title"],
                        "provider": meta["provider"],
                        "published_at_utc": meta["published_at_utc"],
                    },
                    "rendered_product": {"text": str(source.get("rendered_text") or "")},
                },
            }
            sample = normalize_source(shaped)
            if sample["published_at_utc"] != meta["published_at_utc"]:
                raise ControllerError(f"normalization changed publication timestamp: {source_id}")
            sentence_ids = [item["sentence_id"] for item in sample["normalized_sentences"]]
            if sentence_ids != list(range(1, len(sentence_ids) + 1)):
                raise ControllerError(f"non-consecutive sentence IDs: {source_id}")
            sample.update(
                {
                    "dataset_version": DATASET_VERSION,
                    "original_source_hash": meta["original_source_hash"],
                    "rendered_text_hash": meta["rendered_text_hash"],
                    "renderer_version": meta["renderer_version"],
                    "text_contract": meta["text_contract"],
                    "rendered_chars": meta["rendered_chars"],
                    "source_revision_key": meta["source_revision_key"],
                }
            )
            normalized_count += 1
            yield sample

    write_jsonl(population_path, frozen_rows())
    if seen != set(included_meta):
        raise ControllerError(f"frozen population missed {len(set(included_meta) - seen)} inventory source IDs")
    exclusions_path = require_file(paths.inventory / "gold_exclusions.jsonl", "gold exclusion manifest")
    manifest = {
        "dataset_version": DATASET_VERSION,
        "created_at_utc": utc_now(),
        "time_boundary": inventory_report["time_boundary"],
        "calendar_interval_closed": inventory_report["calendar_interval_closed"],
        "source_query_version": SOURCE_QUERY_VERSION,
        "gold_manifest_sha256": inventory_report["gold_authority"]["manifest_sha256"],
        "included_source_ids_sha256": sha256_bytes("\n".join(sorted(included_meta)).encode("utf-8")),
        "included_source_text_pairs_sha256": sha256_bytes(
            "\n".join(f"{source_id}\t{included_meta[source_id]['rendered_text_hash']}" for source_id in sorted(included_meta)).encode("utf-8")
        ),
        "excluded_source_ids_manifest": {
            "path": str(exclusions_path),
            "sha256": sha256_file(exclusions_path),
            "rows": sum(1 for _ in iter_jsonl(exclusions_path)),
        },
        "renderer_versions": inventory_report["renderer_versions"],
        "normalizer_version": NORMALIZER_VERSION,
        "prompt_sha256": prompt_hash,
        "schema_sha256": sha256_json(OUTPUT_SCHEMA),
        "example_bank_sha256": sha256_file(EXAMPLE_PATH),
        "example_source_catalog_sha256": sha256_file(example_catalog),
        "example_source_ids_sha256": sha256_bytes("\n".join(sorted(example_source_ids(bank))).encode("utf-8")),
        "total_records": normalized_count,
        "packetization_policy": {
            "target_input_tokens": TARGET_PACKET_TOKENS,
            "short_article_max_count": 10,
            "ordinary_article_max_count": 6,
            "long_article_max_count": 2,
            "oversized_article_policy": "isolated with explicit larger allowance; never truncate",
            "estimated_tokens_method": "ceil(UTF-8 JSON character count divided by 3)",
        },
        "code_commit": _git_commit(),
        "files": _hash_files([population_path, exclusions_path, EXAMPLE_PATH]),
    }
    write_json(paths.frozen / "system_prompt.json", {"system_prompt": system_prompt, "sha256": prompt_hash})
    write_json(paths.frozen / "manifest.json", manifest)
    return manifest


def estimated_tokens(value: Any) -> int:
    return math.ceil(len(canonical_json(value)) / 3)


def article_size_class(article_tokens: int) -> str:
    if article_tokens <= 400:
        return "short"
    if article_tokens <= 4_000:
        return "ordinary"
    if article_tokens <= 12_000:
        return "long"
    return "oversized"


def prepare_packets(
    runtime_root: Path,
    *,
    lane: str = "single_pass",
    pilot_size: int = 0,
) -> dict[str, Any]:
    if lane not in {"single_pass", "qc"}:
        raise ControllerError(f"unsupported packet lane: {lane}")
    paths = RuntimePaths(runtime_root)
    packet_root = paths.packets / lane / ("pilot" if pilot_size else "full")
    existing_report = packet_root / "packet_report.json"
    if existing_report.exists():
        return read_json(existing_report)
    frozen_manifest = read_json(require_file(paths.frozen / "manifest.json", "frozen population manifest"))
    prompt_record = read_json(require_file(paths.frozen / "system_prompt.json", "frozen system prompt"))
    population = list(iter_jsonl(require_file(paths.frozen / "population.jsonl", "frozen population")))
    if lane == "qc":
        qc_ids = {str(row["source_id"]) for row in iter_jsonl(require_file(paths.outputs / "qc_sample.jsonl", "QC sample"))}
        population = [row for row in population if row["source_id"] in qc_ids]
    elif pilot_size:
        if pilot_size < 1:
            raise ControllerError("pilot_size must be positive")
        population = sorted(
            population,
            key=lambda row: sha256_bytes(f"{frozen_manifest['dataset_version']}|pilot|{row['source_id']}".encode()),
        )[:pilot_size]
    else:
        completed_ids = set(collect_validated(runtime_root, lane))
        population = [row for row in population if row["source_id"] not in completed_ids]
    population.sort(key=lambda row: (row["published_at_utc"], row["source_id"]))
    prompt_tokens = estimated_tokens(prompt_record["system_prompt"])
    prepared = []
    current: list[dict[str, Any]] = []
    current_tokens = prompt_tokens
    current_class = ""

    def flush() -> None:
        nonlocal current, current_tokens, current_class
        if current:
            prepared.append((current, current_tokens, current_class))
        current = []
        current_tokens = prompt_tokens
        current_class = ""

    for article in population:
        tokens = estimated_tokens(_worker_article(article))
        size_class = article_size_class(tokens)
        max_count = {"short": 10, "ordinary": 6, "long": 2, "oversized": 1}[size_class]
        if size_class == "oversized":
            flush()
            prepared.append(([article], prompt_tokens + tokens, size_class))
            continue
        if current and (
            current_tokens + tokens > TARGET_PACKET_TOKENS
            or len(current) >= max_count
            or (current_class and current_class != size_class)
        ):
            flush()
        current.append(article)
        current_tokens += tokens
        current_class = size_class
    flush()
    packet_rows = []
    for index, (articles, token_estimate, size_class) in enumerate(prepared, start=1):
        packet_id = f"{lane}-{'pilot' if pilot_size else 'full'}-{index:06d}"
        packet = {
            "packet_id": packet_id,
            "packet_version": PACKET_VERSION,
            "dataset_version": frozen_manifest["dataset_version"],
            "lane": lane,
            "source_ids": [row["source_id"] for row in articles],
            "articles": [_worker_article(row) for row in articles],
            "source_hashes": {row["source_id"]: row["rendered_text_hash"] for row in articles},
            "prompt_sha256": frozen_manifest["prompt_sha256"],
            "schema_sha256": frozen_manifest["schema_sha256"],
            "example_bank_sha256": frozen_manifest["example_bank_sha256"],
            "expected_article_count": len(articles),
            "estimated_input_tokens": token_estimate,
            "size_class": size_class,
            "max_output_tokens": 32_768 if size_class == "oversized" else 16_384,
        }
        packet_hash = sha256_json(packet)
        packet["packet_sha256"] = packet_hash
        packet_path = packet_root / "packet_data" / f"{packet_id}.json"
        write_json(packet_path, packet)
        task = build_worker_task(prompt_record["system_prompt"], packet)
        task_path = packet_root / "worker_tasks" / f"{packet_id}.txt"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(task, encoding="utf-8")
        packet_rows.append(
            {
                "packet_id": packet_id,
                "packet_sha256": packet_hash,
                "packet_path": str(packet_path),
                "worker_task_path": str(task_path),
                "source_ids": packet["source_ids"],
                "expected_article_count": len(articles),
                "estimated_input_tokens": token_estimate,
                "size_class": size_class,
                "lane": lane,
                "pilot": bool(pilot_size),
            }
        )
    index_path = packet_root / "packet_index.jsonl"
    write_jsonl(index_path, packet_rows)
    report = {
        "lane": lane,
        "pilot_size": pilot_size,
        "articles": len(population),
        "already_validated_articles": len(collect_validated(runtime_root, lane)),
        "packets": len(packet_rows),
        "estimated_input_tokens": sum(row["estimated_input_tokens"] for row in packet_rows),
        "packet_size_distribution": dict(Counter(row["size_class"] for row in packet_rows)),
        "packet_index": str(index_path),
        "packet_index_sha256": sha256_file(index_path),
    }
    write_json(packet_root / "packet_report.json", report)
    return report


def _worker_article(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "published_at_utc": row["published_at_utc"],
        "normalized_sentences": row["normalized_sentences"],
        "metadata": {"title": row.get("metadata", {}).get("title", ""), "provider": row.get("metadata", {}).get("provider", "")},
    }


def build_worker_task(system_prompt: str, packet: Mapping[str, Any]) -> str:
    envelope_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "required": ["source_id", "labels", "isolation_attestation"],
            "additionalProperties": False,
            "properties": {
                "source_id": {"type": "string"},
                "labels": OUTPUT_SCHEMA,
                "isolation_attestation": {
                    "type": "object",
                    "required": ["used_only_supplied_packet", "used_tools", "used_external_context"],
                    "additionalProperties": False,
                    "properties": {
                        "used_only_supplied_packet": {"const": True},
                        "used_tools": {"const": False},
                        "used_external_context": {"const": False},
                    },
                },
            },
        },
    }
    return (
        "You are a fresh blinded issuer-labeling worker.\n"
        "Do not call tools. Do not browse. Do not open files. Do not use memory or prior conversation.\n"
        "Use only the supplied V3 prompt, approved fixed examples, strict schema, and bounded packet.\n"
        "Return only the requested JSON array. Do not provide reasoning. Do not infer later outcomes.\n"
        "For every source, attest that no outside context was used.\n"
        "Do not use source IDs as semantic evidence; they are opaque transport identities.\n\n"
        "BEGIN EXACT FROZEN V3 PROMPT AND APPROVED EXAMPLES\n"
        + system_prompt
        + "\nEND EXACT FROZEN V3 PROMPT AND APPROVED EXAMPLES\n\n"
        "Transport envelope schema:\n"
        + canonical_json(envelope_schema)
        + "\n\nBounded packet:\n"
        + canonical_json(packet)
    )


def packet_index_paths(runtime_root: Path, lane: str) -> list[Path]:
    root = RuntimePaths(runtime_root).packets / lane
    return sorted(root.glob("*/packet_index.jsonl"))


def pending_packets(runtime_root: Path, lane: str) -> list[dict[str, Any]]:
    paths = RuntimePaths(runtime_root)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index_path in packet_index_paths(runtime_root, lane):
        for row in iter_jsonl(index_path):
            packet_id = row["packet_id"]
            if packet_id in seen:
                raise ControllerError(f"duplicate packet ID across packet indexes: {packet_id}")
            seen.add(packet_id)
            validated = paths.outputs / lane / "validated" / f"{packet_id}.jsonl"
            if not validated.exists():
                rows.append(row)
    return rows


def ingest_worker_output(
    runtime_root: Path,
    *,
    lane: str,
    packet_id: str,
    raw_bytes: bytes,
    worker_run_id: str,
) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    indexes = {
        row["packet_id"]: row
        for index_path in packet_index_paths(runtime_root, lane)
        for row in iter_jsonl(index_path)
    }
    if packet_id not in indexes:
        raise ControllerError(f"unknown {lane} packet: {packet_id}")
    packet = read_json(Path(indexes[packet_id]["packet_path"]))
    declared_packet_hash = str(packet.pop("packet_sha256", ""))
    recomputed_packet_hash = sha256_json(packet)
    packet["packet_sha256"] = declared_packet_hash
    if declared_packet_hash != indexes[packet_id]["packet_sha256"] or declared_packet_hash != recomputed_packet_hash:
        raise ControllerError(f"packet hash mismatch: {packet_id}")
    raw_hash = sha256_bytes(raw_bytes)
    attempt_path = paths.outputs / lane / "raw_attempts" / packet_id / f"{raw_hash}.json"
    if not attempt_path.exists():
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_path.write_bytes(raw_bytes)
    raw_path = paths.outputs / lane / "raw" / f"{packet_id}.json"
    valid_path = paths.outputs / lane / "validated" / f"{packet_id}.jsonl"
    lineage_path = paths.outputs / lane / "lineage" / f"{packet_id}.json"
    if valid_path.exists():
        lineage = read_json(lineage_path)
        if lineage.get("raw_output_sha256") == raw_hash:
            return {"status": "already_validated", "packet_id": packet_id, "validated": str(valid_path)}
        raise ControllerError(f"duplicate packet completion with different raw output: {packet_id}")
    try:
        decoded = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerError(f"worker output is not valid JSON: {packet_id}: {exc}") from exc
    if isinstance(decoded, dict) and isinstance(decoded.get("results"), list):
        decoded = decoded["results"]
    if not isinstance(decoded, list):
        raise ControllerError(f"worker output must be a JSON array: {packet_id}")
    expected_ids = list(packet["source_ids"])
    actual_ids = [str(row.get("source_id") or "") for row in decoded if isinstance(row, dict)]
    if len(decoded) != len(expected_ids) or sorted(actual_ids) != sorted(expected_ids) or len(set(actual_ids)) != len(actual_ids):
        raise ControllerError(
            f"packet source coverage mismatch: expected={len(expected_ids)} actual={len(decoded)} "
            f"missing={sorted(set(expected_ids)-set(actual_ids))} unexpected={sorted(set(actual_ids)-set(expected_ids))}"
        )
    articles = {row["source_id"]: row for row in packet["articles"]}
    validated_rows = []
    for envelope in decoded:
        if not isinstance(envelope, dict) or set(envelope) != {"source_id", "labels", "isolation_attestation"}:
            raise ControllerError(f"invalid transport envelope in {packet_id}")
        attestation = envelope["isolation_attestation"]
        if attestation != {
            "used_only_supplied_packet": True,
            "used_tools": False,
            "used_external_context": False,
        }:
            raise ControllerError(f"worker isolation attestation failed: {packet_id}/{envelope.get('source_id')}")
        source_id = str(envelope["source_id"])
        labels = canonicalize_output(envelope["labels"])
        sentence_ids = [row["sentence_id"] for row in articles[source_id]["normalized_sentences"]]
        errors = validate_output(labels, sentence_ids)
        if errors:
            raise ControllerError(f"invalid labels for {packet_id}/{source_id}: {errors}")
        validated_rows.append(
            {
                "source_id": source_id,
                "labels": labels,
                "isolation_attestation": attestation,
                "lineage": {
                    "dataset_version": packet["dataset_version"],
                    "packet_id": packet_id,
                    "packet_sha256": packet["packet_sha256"],
                    "source_text_sha256": packet["source_hashes"][source_id],
                    "prompt_sha256": packet["prompt_sha256"],
                    "schema_sha256": packet["schema_sha256"],
                    "example_bank_sha256": packet["example_bank_sha256"],
                    "worker_run_id": worker_run_id,
                    "raw_output_sha256": raw_hash,
                    "validated_at_utc": utc_now(),
                    "blinding": "fresh_same_model_run; not statistically independent human review",
                },
            }
        )
    validated_rows.sort(key=lambda row: expected_ids.index(row["source_id"]))
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = raw_path.with_suffix(".json.part")
    temporary.write_bytes(raw_bytes)
    temporary.replace(raw_path)
    write_jsonl(valid_path, validated_rows)
    write_json(
        lineage_path,
        {
            "packet_id": packet_id,
            "packet_sha256": packet["packet_sha256"],
            "worker_run_id": worker_run_id,
            "raw_output_sha256": raw_hash,
            "validated_output_sha256": sha256_file(valid_path),
            "records": len(validated_rows),
            "created_at_utc": utc_now(),
        },
    )
    return {"status": "validated", "packet_id": packet_id, "records": len(validated_rows), "validated": str(valid_path)}


def collect_validated(runtime_root: Path, lane: str) -> dict[str, dict[str, Any]]:
    root = RuntimePaths(runtime_root).outputs / lane / "validated"
    output: dict[str, dict[str, Any]] = {}
    if not root.exists():
        return output
    for path in sorted(root.glob("*.jsonl")):
        for row in iter_jsonl(path):
            source_id = str(row["source_id"])
            if source_id in output:
                raise ControllerError(f"duplicate validated {lane} source: {source_id}")
            output[source_id] = row
    return output


def validate_lane(runtime_root: Path, lane: str) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    expected = {
        source_id
        for index_path in packet_index_paths(runtime_root, lane)
        for row in iter_jsonl(index_path)
        for source_id in row["source_ids"]
    }
    actual = collect_validated(runtime_root, lane)
    pending = pending_packets(runtime_root, lane)
    report = {
        "lane": lane,
        "expected_articles": len(expected),
        "valid_articles": len(actual),
        "missing_articles": sorted(expected - set(actual)),
        "unexpected_articles": sorted(set(actual) - expected),
        "pending_packets": [row["packet_id"] for row in pending],
        "status": "complete" if set(actual) == expected and not pending else "partial",
    }
    write_json(paths.outputs / lane / "validation_report.json", report)
    return report


def derived_sentiment(issuer: Mapping[str, Any]) -> str:
    positive = float(issuer["positive_implication_probability"]) >= FORECAST_THRESHOLD
    negative = float(issuer["negative_implication_probability"]) >= FORECAST_THRESHOLD
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "neutral"


def issuer_key(issuer: Mapping[str, Any]) -> str:
    ticker = normalize_ticker(issuer.get("ticker"))
    return f"ticker:{ticker}" if ticker else f"null:{str(issuer.get('issuer_name') or '').strip().casefold()}"


def discrete_issuer(issuer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "forecast_eligible": float(issuer["forecast_relevance_probability"]) >= FORECAST_THRESHOLD,
        "sentiment": derived_sentiment(issuer),
        "event_tags": sorted(issuer["event_tags"]),
        "issuer_roles": sorted(issuer["issuer_roles"]),
    }


def compare_labels(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    a_rows = {issuer_key(row): row for row in a["issuers"]}
    b_rows = {issuer_key(row): row for row in b["issuers"]}
    identity_agreement = set(a_rows) == set(b_rows)
    field_disagreements: list[dict[str, Any]] = []
    evidence_disagreements: list[dict[str, Any]] = []
    probability_differences: list[dict[str, Any]] = []
    for key in sorted(set(a_rows) & set(b_rows)):
        da, db = discrete_issuer(a_rows[key]), discrete_issuer(b_rows[key])
        changed = sorted(field for field in da if da[field] != db[field])
        if changed:
            field_disagreements.append({"issuer_key": key, "fields": changed, "a": da, "b": db})
        if a_rows[key]["evidence_sentence_ids"] != b_rows[key]["evidence_sentence_ids"]:
            evidence_disagreements.append(
                {"issuer_key": key, "a": a_rows[key]["evidence_sentence_ids"], "b": b_rows[key]["evidence_sentence_ids"]}
            )
        for field in (
            "identity_confidence_probability", "forecast_relevance_probability",
            "positive_implication_probability", "negative_implication_probability",
        ):
            difference = abs(float(a_rows[key][field]) - float(b_rows[key][field]))
            probability_differences.append(
                {
                    "issuer_key": key,
                    "field": field,
                    "absolute_difference": round(difference, 6),
                    "tolerance_band": "<=0.1" if difference <= 0.1 else "<=0.25" if difference <= 0.25 else ">0.25",
                }
            )
    discrete_agreement = identity_agreement and not field_disagreements
    return {
        "identity_agreement": identity_agreement,
        "discrete_agreement": discrete_agreement,
        "field_disagreements": field_disagreements,
        "evidence_disagreements": evidence_disagreements,
        "probability_differences": probability_differences,
        "article_agreement": discrete_agreement,
    }


def _article_classes(article: Mapping[str, Any]) -> list[str]:
    text = " ".join(
        [
            str(article.get("metadata", {}).get("title") or ""),
            " ".join(str(row.get("text") or "") for row in article.get("normalized_sentences") or []),
        ]
    ).casefold()
    classes = [name for name, terms in ARTICLE_CLASS_TERMS.items() if any(term in text for term in terms)]
    return classes or ["other"]


def prepare_qc(runtime_root: Path) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    population = {row["source_id"]: row for row in iter_jsonl(require_file(paths.frozen / "population.jsonl", "frozen population"))}
    labels = collect_validated(runtime_root, "single_pass")
    if set(labels) != set(population):
        raise ControllerError("QC sampling requires complete single-pass coverage of the frozen population")
    records: list[dict[str, Any]] = []
    dimension_candidates: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for source_id, output in labels.items():
        article = population[source_id]
        issuers = output["labels"]["issuers"]
        article_tokens = estimated_tokens(_worker_article(article))
        length_bucket = article_size_class(article_tokens)
        sentiments = sorted({derived_sentiment(row) for row in issuers}) or ["none"]
        eligibility = sorted({"eligible" if float(row["forecast_relevance_probability"]) >= .5 else "ineligible" for row in issuers}) or ["none"]
        tags = sorted({tag for row in issuers for tag in row["event_tags"]}) or ["none"]
        classes = _article_classes(article)
        high_risk_reasons: list[str] = []
        if length_bucket in {"long", "oversized"} or "roundup" in classes:
            high_risk_reasons.append("long_or_roundup")
        if len(issuers) > 3:
            high_risk_reasons.append("more_than_three_issuers")
        if any(row.get("ticker") is None for row in issuers):
            high_risk_reasons.append("null_ticker_resolved_issuer")
        if any(float(row["identity_confidence_probability"]) < LOW_IDENTITY_CONFIDENCE for row in issuers):
            high_risk_reasons.append("low_identity_confidence")
        if any(abs(float(row["forecast_relevance_probability"]) - .5) <= NEAR_THRESHOLD for row in issuers):
            high_risk_reasons.append("forecast_near_threshold")
        if any(
            float(row["forecast_relevance_probability"]) >= .5 and derived_sentiment(row) in {"mixed", "neutral"}
            for row in issuers
        ):
            high_risk_reasons.append("mixed_or_neutral_eligible")
        if any(set(row["event_tags"]) & RISK_TAGS for row in issuers):
            high_risk_reasons.append("high_risk_event_tag")
        dimensions = [
            f"provider:{article.get('metadata', {}).get('provider', '')}",
            f"length:{length_bucket}",
            f"issuer_count:{'0' if not issuers else '1' if len(issuers)==1 else '2-3' if len(issuers)<=3 else '4+'}",
            f"multi_issuer:{len(issuers)>1}",
            *[f"eligibility:{value}" for value in eligibility],
            *[f"sentiment:{value}" for value in sentiments],
            *[f"tag:{value}" for value in tags],
            *[f"class:{value}" for value in classes],
        ]
        rank = sha256_bytes(f"{DATASET_VERSION}|qc|{source_id}".encode())
        record = {
            "source_id": source_id,
            "deterministic_rank": rank,
            "high_risk": bool(high_risk_reasons),
            "high_risk_reasons": high_risk_reasons,
            "strata": dimensions,
            "article_tokens": article_tokens,
        }
        records.append(record)
        for dimension in dimensions:
            dimension_candidates[dimension].append((rank, source_id))
    selected = {row["source_id"] for row in records if row["high_risk"]}
    for candidates in dimension_candidates.values():
        selected.add(min(candidates)[1])
    target = math.ceil(len(records) * QC_FRACTION)
    for row in sorted(records, key=lambda item: item["deterministic_rank"]):
        if len(selected) >= target:
            break
        selected.add(row["source_id"])
    sample = [row for row in sorted(records, key=lambda item: item["source_id"]) if row["source_id"] in selected]
    write_jsonl(paths.outputs / "qc_sample.jsonl", sample)
    report = {
        "version": "llm_issuer_labeling_codex_qc_sample_v1",
        "population": len(records),
        "minimum_fraction": QC_FRACTION,
        "minimum_target": target,
        "selected": len(sample),
        "selected_fraction": len(sample) / len(records) if records else 0,
        "high_risk_selected": sum(row["high_risk"] for row in sample),
        "strata_covered": len({value for row in sample for value in row["strata"]}),
        "sampling": "all declared high-risk records plus deterministic per-stratum representatives, then deterministic fill to at least 15%",
        "sample_sha256": sha256_file(paths.outputs / "qc_sample.jsonl"),
    }
    write_json(paths.outputs / "qc_sample_report.json", report)
    return report


def compare_qc(runtime_root: Path) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    population = {row["source_id"]: row for row in iter_jsonl(require_file(paths.frozen / "population.jsonl", "frozen population"))}
    first = collect_validated(runtime_root, "single_pass")
    second = collect_validated(runtime_root, "qc")
    expected = {row["source_id"] for row in iter_jsonl(require_file(paths.outputs / "qc_sample.jsonl", "QC sample"))}
    if set(second) != expected:
        raise ControllerError("QC comparison requires complete relabel coverage")
    rows = []
    for source_id in sorted(expected):
        comparison = compare_labels(first[source_id]["labels"], second[source_id]["labels"])
        rows.append(
            {
                "agreement_version": AGREEMENT_VERSION,
                "source_id": source_id,
                "provider": population[source_id].get("metadata", {}).get("provider", ""),
                "length_bucket": article_size_class(estimated_tokens(_worker_article(population[source_id]))),
                "article_classes": _article_classes(population[source_id]),
                **comparison,
                "candidate_a_lineage": first[source_id]["lineage"],
                "candidate_b_lineage": second[source_id]["lineage"],
            }
        )
    write_jsonl(paths.outputs / "agreement_records.jsonl", rows)
    disagreements = [row for row in rows if not row["discrete_agreement"]]
    write_jsonl(paths.outputs / "disagreements.jsonl", disagreements)
    report = _agreement_report(rows)
    write_json(paths.outputs / "agreement_report.json", report)
    return report


def _agreement_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    common_units = sum(
        len(row.get("probability_differences", [])) // 4 for row in rows
    )
    field_counts = Counter(
        field
        for row in rows
        for disagreement in row["field_disagreements"]
        for field in disagreement["fields"]
    )
    matched_field_units = Counter({"forecast_eligible": common_units, "sentiment": common_units, "event_tags": common_units, "issuer_roles": common_units})
    field_agreement = {
        field: ((matched_field_units[field] - field_counts[field]) / matched_field_units[field] if matched_field_units[field] else 0)
        for field in matched_field_units
    }
    disagreement_rows = [row for row in rows if not row["discrete_agreement"]]
    return {
        "articles": total,
        "article_level_agreement": sum(bool(row["article_agreement"]) for row in rows) / total if total else 0,
        "issuer_identity_agreement": sum(bool(row["identity_agreement"]) for row in rows) / total if total else 0,
        "disagreement_articles": sum(not bool(row["discrete_agreement"]) for row in rows),
        "evidence_disagreement_articles": sum(bool(row["evidence_disagreements"]) for row in rows),
        "matched_issuer_units": common_units,
        "forecast_eligibility_agreement": field_agreement["forecast_eligible"],
        "sentiment_agreement": field_agreement["sentiment"],
        "event_tag_exact_set_agreement": field_agreement["event_tags"],
        "role_exact_set_agreement": field_agreement["issuer_roles"],
        "field_disagreement_counts": dict(sorted(field_counts.items())),
        "probability_tolerance_bands": dict(
            Counter(item["tolerance_band"] for row in rows for item in row["probability_differences"])
        ),
        "disagreements_by_provider": dict(Counter(str(row.get("provider") or "") for row in disagreement_rows)),
        "disagreements_by_length": dict(Counter(str(row.get("length_bucket") or "") for row in disagreement_rows)),
        "disagreements_by_article_class": dict(
            Counter(value for row in disagreement_rows for value in row.get("article_classes") or ["other"])
        ),
        "disagreements_by_label_field": dict(sorted(field_counts.items())),
    }


def prepare_adjudication(runtime_root: Path) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    disagreements = list(iter_jsonl(require_file(paths.outputs / "disagreements.jsonl", "disagreement records")))
    population = {row["source_id"]: row for row in iter_jsonl(require_file(paths.frozen / "population.jsonl", "frozen population"))}
    first = collect_validated(runtime_root, "single_pass")
    second = collect_validated(runtime_root, "qc")
    prompt = read_json(require_file(paths.frozen / "system_prompt.json", "frozen system prompt"))["system_prompt"]
    root = paths.packets / "adjudication" / "full"
    packet_rows = []
    for index, disagreement in enumerate(disagreements, start=1):
        source_id = disagreement["source_id"]
        candidates = [first[source_id]["labels"], second[source_id]["labels"]]
        if int(sha256_bytes(f"{DATASET_VERSION}|adjudication-order|{source_id}".encode())[:2], 16) % 2:
            candidates.reverse()
        packet_id = f"adjudication-full-{index:06d}"
        packet = {
            "packet_id": packet_id,
            "packet_version": PACKET_VERSION,
            "dataset_version": DATASET_VERSION,
            "lane": "adjudication",
            "source_ids": [source_id],
            "articles": [_worker_article(population[source_id])],
            "source_hashes": {source_id: population[source_id]["rendered_text_hash"]},
            "prompt_sha256": sha256_bytes(prompt.encode()),
            "schema_sha256": sha256_json(OUTPUT_SCHEMA),
            "example_bank_sha256": sha256_file(EXAMPLE_PATH),
            "expected_article_count": 1,
            "estimated_input_tokens": estimated_tokens(prompt) + estimated_tokens(_worker_article(population[source_id])) + estimated_tokens(candidates),
            "candidate_outputs": {"candidate_1": candidates[0], "candidate_2": candidates[1]},
        }
        packet["packet_sha256"] = sha256_json(packet)
        packet_path = root / "packet_data" / f"{packet_id}.json"
        write_json(packet_path, packet)
        task_path = root / "worker_tasks" / f"{packet_id}.txt"
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(build_adjudication_task(prompt, packet), encoding="utf-8")
        packet_rows.append(
            {
                "packet_id": packet_id,
                "packet_sha256": packet["packet_sha256"],
                "packet_path": str(packet_path),
                "worker_task_path": str(task_path),
                "source_ids": [source_id],
                "expected_article_count": 1,
                "estimated_input_tokens": packet["estimated_input_tokens"],
                "lane": "adjudication",
            }
        )
    index_path = root / "packet_index.jsonl"
    write_jsonl(index_path, packet_rows)
    return {"disagreements": len(disagreements), "adjudication_packets": len(packet_rows), "packet_index": str(index_path)}


def build_adjudication_task(system_prompt: str, packet: Mapping[str, Any]) -> str:
    return (
        "You are a fresh blinded issuer-label adjudicator.\n"
        "Do not call tools. Do not browse. Do not open files. Do not use memory or prior conversation.\n"
        "Use only the supplied V3 prompt, examples, schema, article, and randomized candidate outputs.\n"
        "Return only one JSON object with keys source_id, decision, labels, changed_fields, isolation_attestation.\n"
        "decision must be candidate_1, candidate_2, or corrected. labels must always contain the chosen or corrected exact V3 object.\n"
        "changed_fields is a sorted list of concise JSON field paths changed from both candidates, or an empty list when choosing one.\n"
        "Do not provide reasoning. Do not infer later outcomes. Attest that no outside context was used.\n\n"
        "BEGIN EXACT FROZEN V3 PROMPT AND APPROVED EXAMPLES\n"
        + system_prompt
        + "\nEND EXACT FROZEN V3 PROMPT AND APPROVED EXAMPLES\n\n"
        "Blinded adjudication packet:\n"
        + canonical_json(packet)
    )


def ingest_adjudication(
    runtime_root: Path,
    *,
    packet_id: str,
    raw_bytes: bytes,
    worker_run_id: str,
) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    indexes = {
        row["packet_id"]: row
        for index_path in packet_index_paths(runtime_root, "adjudication")
        for row in iter_jsonl(index_path)
    }
    if packet_id not in indexes:
        raise ControllerError(f"unknown adjudication packet: {packet_id}")
    packet = read_json(Path(indexes[packet_id]["packet_path"]))
    raw_hash = sha256_bytes(raw_bytes)
    attempt_path = paths.outputs / "adjudication" / "raw_attempts" / packet_id / f"{raw_hash}.json"
    if not attempt_path.exists():
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_path.write_bytes(raw_bytes)
    raw_path = paths.outputs / "adjudication" / "raw" / f"{packet_id}.json"
    valid_path = paths.outputs / "adjudication" / "validated" / f"{packet_id}.jsonl"
    lineage_path = paths.outputs / "adjudication" / "lineage" / f"{packet_id}.json"
    if valid_path.exists():
        lineage = read_json(lineage_path)
        if lineage["raw_output_sha256"] == raw_hash:
            return {"status": "already_validated", "packet_id": packet_id}
        raise ControllerError(f"duplicate adjudication completion: {packet_id}")
    value = json.loads(raw_bytes.decode("utf-8-sig"))
    required = {"source_id", "decision", "labels", "changed_fields", "isolation_attestation"}
    if not isinstance(value, dict) or set(value) != required:
        raise ControllerError(f"invalid adjudication envelope: {packet_id}")
    source_id = packet["source_ids"][0]
    if value["source_id"] != source_id or value["decision"] not in {"candidate_1", "candidate_2", "corrected"}:
        raise ControllerError(f"invalid adjudication identity or decision: {packet_id}")
    if value["isolation_attestation"] != {"used_only_supplied_packet": True, "used_tools": False, "used_external_context": False}:
        raise ControllerError(f"adjudicator isolation attestation failed: {packet_id}")
    labels = canonicalize_output(value["labels"])
    sentence_ids = [row["sentence_id"] for row in packet["articles"][0]["normalized_sentences"]]
    errors = validate_output(labels, sentence_ids)
    if errors:
        raise ControllerError(f"invalid adjudicated labels: {packet_id}: {errors}")
    if value["decision"] in {"candidate_1", "candidate_2"} and labels != packet["candidate_outputs"][value["decision"]]:
        raise ControllerError(f"chosen adjudication labels do not match selected candidate: {packet_id}")
    if not isinstance(value["changed_fields"], list) or value["changed_fields"] != sorted(set(value["changed_fields"])):
        raise ControllerError(f"adjudication changed_fields must be sorted and unique: {packet_id}")
    row = {
        "source_id": source_id,
        "decision": value["decision"],
        "labels": labels,
        "changed_fields": value["changed_fields"],
        "isolation_attestation": value["isolation_attestation"],
        "lineage": {
            "packet_id": packet_id,
            "packet_sha256": packet["packet_sha256"],
            "worker_run_id": worker_run_id,
            "raw_output_sha256": raw_hash,
            "validated_at_utc": utc_now(),
            "blinding": "fresh_same_model_adjudication; randomized candidate order; not human certification",
        },
    }
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw_bytes)
    write_jsonl(valid_path, [row])
    write_json(lineage_path, {**row["lineage"], "validated_output_sha256": sha256_file(valid_path)})
    return {"status": "validated", "packet_id": packet_id, "source_id": source_id}


def consolidate(runtime_root: Path) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    population = {row["source_id"]: row for row in iter_jsonl(require_file(paths.frozen / "population.jsonl", "frozen population"))}
    first = collect_validated(runtime_root, "single_pass")
    if set(first) != set(population):
        raise ControllerError("consolidation requires complete single-pass coverage")
    second = collect_validated(runtime_root, "qc")
    agreement = {
        row["source_id"]: row
        for row in iter_jsonl(paths.outputs / "agreement_records.jsonl")
    } if (paths.outputs / "agreement_records.jsonl").exists() else {}
    adjudications = collect_validated(runtime_root, "adjudication")
    output = []
    authority_counts: Counter[str] = Counter()
    for source_id in sorted(population):
        authority = "codex_single_pass"
        labels = first[source_id]["labels"]
        lineage: dict[str, Any] = {"single_pass": first[source_id]["lineage"]}
        if source_id in second:
            lineage["qc"] = second[source_id]["lineage"]
            if agreement[source_id]["discrete_agreement"]:
                authority = "codex_agreement_confirmed"
            elif source_id in adjudications:
                authority = "codex_adjudicated"
                labels = adjudications[source_id]["labels"]
                lineage["adjudication"] = adjudications[source_id]["lineage"]
            else:
                raise ControllerError(f"unadjudicated QC disagreement: {source_id}")
        authority_counts[authority] += 1
        output.append(
            {
                "source_id": source_id,
                "labels": labels,
                "authority_level": authority,
                "human_certified": False,
                "lineage": lineage,
            }
        )
    labels_path = paths.outputs / "consolidated_labels.jsonl"
    write_jsonl(labels_path, output)
    authority_manifest = {
        "version": AUTHORITY_VERSION,
        "created_at_utc": utc_now(),
        "dataset_version": DATASET_VERSION,
        "counts": dict(sorted(authority_counts.items())),
        "human_certified": 0,
        "policy": {
            "codex_single_pass": "teacher/silver only",
            "codex_agreement_confirmed": "same-model blinded agreement; high-confidence provisional",
            "codex_adjudicated": "same-model third-run adjudication; not human-certified",
            "human_certified": "reserved; never assigned automatically",
        },
        "consolidated_labels_sha256": sha256_file(labels_path),
    }
    write_json(paths.outputs / "authority_level_manifest.json", authority_manifest)
    return authority_manifest


def report(runtime_root: Path) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    inventory_report = read_json(require_file(paths.inventory / "inventory_report.json", "inventory report"))
    population = list(iter_jsonl(require_file(paths.frozen / "population.jsonl", "frozen population")))
    first = collect_validated(runtime_root, "single_pass")
    qc = collect_validated(runtime_root, "qc")
    adjudicated = collect_validated(runtime_root, "adjudication")
    issuer_rows = [issuer for row in first.values() for issuer in row["labels"]["issuers"]]
    packet_rows = [row for index in packet_index_paths(runtime_root, "single_pass") for row in iter_jsonl(index)]
    agreement_report = read_json(paths.outputs / "agreement_report.json") if (paths.outputs / "agreement_report.json").exists() else {}
    authority = read_json(paths.outputs / "authority_level_manifest.json") if (paths.outputs / "authority_level_manifest.json").exists() else {}
    adjudication_rows = list(adjudicated.values())
    probability_fields = (
        "identity_confidence_probability", "forecast_relevance_probability",
        "positive_implication_probability", "negative_implication_probability",
    )
    coverage = {
        "version": "llm_issuer_labeling_codex_2026_report_v1",
        "created_at_utc": utc_now(),
        "inventory": inventory_report,
        "rendering": read_json(paths.inventory / "rendering_report.json") if (paths.inventory / "rendering_report.json").exists() else {},
        "packets": {
            "count": len(packet_rows),
            "articles": sum(row["expected_article_count"] for row in packet_rows),
            "estimated_input_tokens": sum(row["estimated_input_tokens"] for row in packet_rows),
            "size_distribution": dict(Counter(row["size_class"] for row in packet_rows if "size_class" in row)),
        },
        "worker_completion": {
            "single_pass_valid": len(first),
            "single_pass_remaining": len(population) - len(first),
            "qc_valid": len(qc),
            "adjudicated": len(adjudicated),
            "raw_single_pass_outputs": len(list((paths.outputs / "single_pass" / "raw").glob("*.json"))) if (paths.outputs / "single_pass" / "raw").exists() else 0,
            "raw_single_pass_attempts": len(list((paths.outputs / "single_pass" / "raw_attempts").rglob("*.json"))) if (paths.outputs / "single_pass" / "raw_attempts").exists() else 0,
            "failed_or_invalid_single_pass_attempts": max(
                0,
                (len(list((paths.outputs / "single_pass" / "raw_attempts").rglob("*.json"))) if (paths.outputs / "single_pass" / "raw_attempts").exists() else 0)
                - (len(list((paths.outputs / "single_pass" / "raw").glob("*.json"))) if (paths.outputs / "single_pass" / "raw").exists() else 0),
            ),
        },
        "single_pass_distributions": {
            "articles": len(first),
            "issuer_rows": len(issuer_rows),
            "forecast_eligible_issuers": sum(float(row["forecast_relevance_probability"]) >= .5 for row in issuer_rows),
            "sentiment": dict(Counter(derived_sentiment(row) for row in issuer_rows)),
            "event_tags": dict(Counter(tag for row in issuer_rows for tag in row["event_tags"])),
            "roles": dict(Counter(role for row in issuer_rows for role in row["issuer_roles"])),
            "null_ticker_issuers": sum(row.get("ticker") is None for row in issuer_rows),
        },
        "probability_distributions": {
            field: dict(
                Counter(
                    "0.00-0.24" if float(row[field]) < .25 else
                    "0.25-0.49" if float(row[field]) < .5 else
                    "0.50-0.74" if float(row[field]) < .75 else
                    "0.75-1.00"
                    for row in issuer_rows
                )
            )
            for field in probability_fields
        },
        "agreement": agreement_report,
        "adjudication_outcomes": dict(Counter(str(row.get("decision") or "") for row in adjudication_rows)),
        "adjudication_changed_fields": dict(
            Counter(field for row in adjudication_rows for field in row.get("changed_fields") or [])
        ),
        "authority_levels": authority.get("counts", {}),
        "evidence_id_validity": {"valid": len(first), "invalid": 0 if first else 0},
        "high_risk_queue_size": sum(1 for row in iter_jsonl(paths.outputs / "qc_sample.jsonl") if row["high_risk"]) if (paths.outputs / "qc_sample.jsonl").exists() else 0,
        "token_usage": {
            "estimated_input_tokens": sum(row["estimated_input_tokens"] for row in packet_rows),
            "actual_tokens_observable": False,
        },
        "remaining_human_review_candidates": agreement_report.get("disagreement_articles", 0),
        "accuracy_claim": "not_applicable_without_independent_gold_truth",
        "completion_scope": {
            "frozen_records": len(population),
            "single_pass_records": len(first),
            "complete_frozen_population": len(first) == len(population),
            "complete_calendar_2026": bool(inventory_report["calendar_interval_closed"]),
        },
    }
    write_json(paths.reports / "coverage_agreement_report.json", coverage)
    hash_paths = [path for path in runtime_root.rglob("*") if path.is_file() and path.name != "hash_manifest.json"]
    hash_manifest = {str(path.relative_to(runtime_root)): sha256_file(path) for path in sorted(hash_paths)}
    write_json(paths.reports / "hash_manifest.json", hash_manifest)
    return coverage


def status(runtime_root: Path) -> dict[str, Any]:
    paths = RuntimePaths(runtime_root)
    frozen = read_json(paths.frozen / "manifest.json") if (paths.frozen / "manifest.json").exists() else {}
    return {
        "runtime_root": str(runtime_root),
        "inventory_ready": (paths.inventory / "inventory_report.json").exists(),
        "frozen": bool(frozen),
        "frozen_records": frozen.get("total_records", 0),
        "calendar_interval_closed": frozen.get("calendar_interval_closed", False),
        "pending_single_pass_packets": len(pending_packets(runtime_root, "single_pass")),
        "validated_single_pass_articles": len(collect_validated(runtime_root, "single_pass")),
        "pending_qc_packets": len(pending_packets(runtime_root, "qc")),
        "validated_qc_articles": len(collect_validated(runtime_root, "qc")),
        "pending_adjudication_packets": len(pending_packets(runtime_root, "adjudication")),
        "validated_adjudications": len(collect_validated(runtime_root, "adjudication")),
        "consolidated": (paths.outputs / "consolidated_labels.jsonl").exists(),
    }


def _hash_files(paths: Sequence[Path]) -> dict[str, Any]:
    return {str(path): {"bytes": path.stat().st_size, "sha256": sha256_file(path)} for path in paths}


def synthetic_dry_run(runtime_root: Path) -> dict[str, Any]:
    root = runtime_root / "synthetic_dry_run"
    paths = RuntimePaths(root)
    articles = [
        {
            "source_id": "synthetic-1",
            "published_at_utc": "2026-01-02 12:00:00.000000000",
            "normalized_sentences": [{"sentence_id": 1, "text": "Acme said it signed a material customer contract."}],
            "metadata": {"title": "Acme signs contract", "provider": "synthetic"},
            "rendered_text_hash": sha256_bytes(b"Acme said it signed a material customer contract."),
        },
        {
            "source_id": "synthetic-2",
            "published_at_utc": "2026-01-03 12:00:00.000000000",
            "normalized_sentences": [{"sentence_id": 1, "text": "Beta shares rose in morning trading."}],
            "metadata": {"title": "Beta shares rise", "provider": "synthetic"},
            "rendered_text_hash": sha256_bytes(b"Beta shares rose in morning trading."),
        },
    ]
    write_jsonl(paths.frozen / "population.jsonl", articles)
    system_prompt = "synthetic frozen prompt"
    write_json(paths.frozen / "system_prompt.json", {"system_prompt": system_prompt, "sha256": sha256_bytes(system_prompt.encode())})
    write_json(
        paths.frozen / "manifest.json",
        {
            "dataset_version": "synthetic",
            "prompt_sha256": sha256_bytes(system_prompt.encode()),
            "schema_sha256": sha256_json(OUTPUT_SCHEMA),
            "example_bank_sha256": sha256_file(EXAMPLE_PATH),
            "total_records": 2,
        },
    )
    packet_report = prepare_packets(root, lane="single_pass")
    index = next(iter_jsonl(Path(packet_report["packet_index"])))
    labels = {
        "schema_version": SCHEMA_VERSION,
        "issuers": [],
        "unresolved_issuer_mentions": [],
    }
    raw = canonical_json(
        [
            {
                "source_id": source_id,
                "labels": labels,
                "isolation_attestation": {"used_only_supplied_packet": True, "used_tools": False, "used_external_context": False},
            }
            for source_id in index["source_ids"]
        ]
    ).encode()
    first = ingest_worker_output(root, lane="single_pass", packet_id=index["packet_id"], raw_bytes=raw, worker_run_id="synthetic-worker-1")
    rerun = ingest_worker_output(root, lane="single_pass", packet_id=index["packet_id"], raw_bytes=raw, worker_run_id="synthetic-worker-1")
    validation = validate_lane(root, "single_pass")
    result = {"packet_report": packet_report, "first_ingest": first, "restart_ingest": rerun, "validation": validation}
    write_json(root / "SYNTHETIC_DRY_RUN.json", result)
    return result
