from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


DEFAULT_INVENTORY_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_inventory")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_fetch_plan")
ACTIONABLE_ACTIONS = {"fetch_html", "fetch_pdf", "resolve_redirect", "sec_handler"}


DEFAULT_DOMAIN_POLICY = {
    "version": "benzinga-url-domain-policy-v1",
    "domain_actions": {
        "bit.ly": "resolve_redirect",
        "bitly.com": "resolve_redirect",
        "c212.net": "resolve_redirect",
        "feedburner.com": "resolve_redirect",
        "goo.gl": "resolve_redirect",
        "lnkd.in": "resolve_redirect",
        "ow.ly": "resolve_redirect",
        "prn.to": "resolve_redirect",
        "t.co": "resolve_redirect",
        "tinyurl.com": "resolve_redirect",
        "facebook.com": "metadata_only",
        "flickr.com": "metadata_only",
        "instagram.com": "metadata_only",
        "linkedin.com": "metadata_only",
        "pinterest.com": "metadata_only",
        "pixabay.com": "metadata_only",
        "reddit.com": "metadata_only",
        "twitter.com": "metadata_only",
        "unsplash.com": "metadata_only",
        "x.com": "metadata_only",
        "youtu.be": "metadata_only",
        "youtube.com": "metadata_only",
        "benzinga.com": "ignore",
        "doubleclick.net": "ignore",
        "google-analytics.com": "ignore",
        "googletagmanager.com": "ignore",
        "grsm.io": "ignore",
        "register.zacks.com": "ignore",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deduplicated Benzinga URL fetch plan from the URL inventory. "
            "The plan applies a domain policy layer before enrichment."
        )
    )
    parser.add_argument("--inventory-jsonl", default=os.environ.get("NEWS_BENZINGA_URL_INVENTORY_JSONL") or "")
    parser.add_argument("--inventory-root-win", default=os.environ.get("NEWS_BENZINGA_URL_INVENTORY_ROOT_WIN") or str(DEFAULT_INVENTORY_ROOT_WIN))
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_URL_FETCH_PLAN_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--policy-json", default=os.environ.get("NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON") or "")
    parser.add_argument("--shards", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_FETCH_PLAN_SHARDS", "128")))
    parser.add_argument("--limit-rows", type=int, default=0, help="Optional smoke-test cap over inventory rows.")
    parser.add_argument("--attachment-sample-limit", type=int, default=25)
    parser.add_argument("--progress-interval", type=int, default=1_000_000)
    parser.add_argument("--keep-shards", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    inventory_path = resolve_inventory_path(args)
    if not inventory_path.exists():
        raise SystemExit(f"inventory file does not exist: {inventory_path}")

    output_root = Path(args.output_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    shard_root = run_root / "shards"
    run_root.mkdir(parents=True, exist_ok=True)
    shard_root.mkdir(parents=True, exist_ok=True)

    policy = load_policy(args.policy_json)
    policy_path = run_root / "news_url_domain_policy_effective.json"
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True), encoding="utf-8")

    paths = {
        "fetch_plan": run_root / "news_url_fetch_plan.jsonl",
        "attachments": run_root / "news_url_fetch_plan_attachments.jsonl",
        "domain_summary": run_root / "news_url_fetch_plan_domain_summary.csv",
        "policy_path": policy_path,
        "manifest": run_root / "news_url_fetch_plan_manifest.json",
    }

    print("=" * 96, flush=True)
    print("Benzinga URL fetch plan", flush=True)
    print(f"inventory_path={inventory_path}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"shards={max(1, args.shards)}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    first_pass = write_candidate_shards(
        args=args,
        inventory_path=inventory_path,
        shard_root=shard_root,
        attachment_path=paths["attachments"],
        policy=policy,
    )
    second_pass = write_deduped_fetch_plan(
        args=args,
        shard_root=shard_root,
        fetch_plan_path=paths["fetch_plan"],
    )
    write_domain_summary(paths["domain_summary"], first_pass["domain_stats"])
    if not args.keep_shards:
        shutil.rmtree(shard_root, ignore_errors=True)

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "inventory_path": str(inventory_path),
        "run_root": str(run_root),
        "fetch_plan_path": str(paths["fetch_plan"]),
        "attachment_path": str(paths["attachments"]),
        "domain_summary_path": str(paths["domain_summary"]),
        "policy_path": str(paths["policy_path"]),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "limit_rows": args.limit_rows,
        "shards": max(1, args.shards),
        "inventory_rows_read": first_pass["rows_read"],
        "candidate_rows_written": first_pass["candidate_rows_written"],
        "attachment_rows_written": first_pass["attachment_rows_written"],
        "deduped_fetch_plan_rows": second_pass["fetch_plan_rows"],
        "unique_action_counts": dict(second_pass["final_action_counts"]),
        "occurrence_policy_counts": dict(first_pass["policy_counts"]),
        "wall_seconds": round(time.perf_counter() - started, 3),
    }
    paths["manifest"].write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest_path=" + str(paths["manifest"]), flush=True)
    print("summary=" + json.dumps(manifest, sort_keys=True), flush=True)


def resolve_inventory_path(args: argparse.Namespace) -> Path:
    explicit = str(args.inventory_jsonl or "").strip()
    if explicit:
        return Path(explicit)
    root = Path(args.inventory_root_win)
    manifests = sorted(root.glob("*/news_url_inventory_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            candidate = Path(manifest.get("url_inventory_path") or "")
            if candidate.exists():
                return candidate
        except Exception:  # noqa: BLE001
            continue
    latest = sorted(root.glob("*/news_url_inventory.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if latest:
        return latest[0]
    return root / "news_url_inventory.jsonl"


def load_policy(policy_json: str) -> dict[str, Any]:
    policy = json.loads(json.dumps(DEFAULT_DOMAIN_POLICY))
    if not policy_json:
        return policy
    override_path = Path(policy_json)
    override = json.loads(override_path.read_text(encoding="utf-8"))
    policy["domain_actions"].update(override.get("domain_actions") or {})
    policy["version"] = str(override.get("version") or policy["version"])
    return policy


def write_candidate_shards(
    *,
    args: argparse.Namespace,
    inventory_path: Path,
    shard_root: Path,
    attachment_path: Path,
    policy: dict[str, Any],
) -> dict[str, Any]:
    shard_count = max(1, args.shards)
    shard_handles: dict[int, Any] = {}
    policy_counts: Counter[str] = Counter()
    domain_stats: dict[str, Counter[str]] = defaultdict(Counter)
    rows_read = 0
    candidate_rows_written = 0
    attachment_rows_written = 0
    started = time.perf_counter()
    try:
        with inventory_path.open("r", encoding="utf-8") as inventory, attachment_path.open("w", encoding="utf-8") as attachments:
            for line in inventory:
                if args.limit_rows and rows_read >= args.limit_rows:
                    break
                rows_read += 1
                row = json.loads(line)
                decision = apply_domain_policy(row, policy)
                final_action = decision["final_action"]
                policy_counts[final_action] += 1
                domain_key = row.get("registered_domain") or row.get("domain") or ""
                update_policy_domain_stats(domain_stats[domain_key], row, decision)
                if args.progress_interval and rows_read % args.progress_interval == 0:
                    print(
                        f"pass=1 rows={rows_read:,} candidates={candidate_rows_written:,} "
                        f"elapsed={time.perf_counter() - started:.1f}s",
                        flush=True,
                    )
                if final_action not in ACTIONABLE_ACTIONS:
                    continue
                candidate = compact_candidate_row(row, decision)
                shard_index = shard_for_url(candidate["url_hash"], shard_count)
                handle = shard_handles.get(shard_index)
                if handle is None:
                    handle = (shard_root / f"candidate_shard_{shard_index:04d}.jsonl").open("w", encoding="utf-8")
                    shard_handles[shard_index] = handle
                handle.write(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")) + "\n")
                attachments.write(json.dumps(compact_attachment_row(row, decision), ensure_ascii=False, separators=(",", ":")) + "\n")
                candidate_rows_written += 1
                attachment_rows_written += 1
    finally:
        for handle in shard_handles.values():
            handle.close()

    return {
        "rows_read": rows_read,
        "candidate_rows_written": candidate_rows_written,
        "attachment_rows_written": attachment_rows_written,
        "policy_counts": policy_counts,
        "domain_stats": domain_stats,
    }


def write_deduped_fetch_plan(*, args: argparse.Namespace, shard_root: Path, fetch_plan_path: Path) -> dict[str, Any]:
    fetch_plan_rows = 0
    final_action_counts: Counter[str] = Counter()
    shard_paths = sorted(shard_root.glob("candidate_shard_*.jsonl"))
    started = time.perf_counter()
    with fetch_plan_path.open("w", encoding="utf-8") as output:
        for shard_number, shard_path in enumerate(shard_paths, start=1):
            aggregates: dict[str, dict[str, Any]] = {}
            with shard_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    candidate = json.loads(line)
                    aggregate_candidate(aggregates, candidate, args.attachment_sample_limit)
            for aggregate in sorted(aggregates.values(), key=lambda row: (row["final_action"], row["fetch_priority"] * -1, row["url_hash"])):
                aggregate["original_action_counts"] = dict(aggregate["original_action_counts"])
                aggregate["url_kind_counts"] = dict(aggregate["url_kind_counts"])
                aggregate["url_source_counts"] = dict(aggregate["url_source_counts"])
                aggregate["policy_reason_counts"] = dict(aggregate["policy_reason_counts"])
                output.write(json.dumps(aggregate, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                fetch_plan_rows += 1
                final_action_counts[aggregate["final_action"]] += 1
            print(
                f"pass=2 shard={shard_number:,}/{len(shard_paths):,} "
                f"unique_total={fetch_plan_rows:,} elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
    return {"fetch_plan_rows": fetch_plan_rows, "final_action_counts": final_action_counts}


def apply_domain_policy(row: dict[str, Any], policy: dict[str, Any]) -> dict[str, str]:
    original_action = str(row.get("candidate_action") or "")
    domain = str(row.get("domain") or "")
    registered_domain = str(row.get("registered_domain") or domain)
    domain_actions = policy.get("domain_actions") or {}
    domain_action = domain_actions.get(domain) or domain_actions.get(registered_domain)

    if original_action == "ignore":
        return {"final_action": "ignore", "policy_reason": "inventory_ignore"}
    if str(row.get("is_sec_url") or "0") == "1":
        return {"final_action": "sec_handler", "policy_reason": "sec_url"}
    if original_action == "fetch_pdf":
        return {"final_action": "fetch_pdf", "policy_reason": "pdf_candidate"}
    if domain_action:
        return {"final_action": str(domain_action), "policy_reason": f"domain_policy:{domain_action}"}
    if original_action == "metadata_only":
        return {"final_action": "metadata_only", "policy_reason": "inventory_metadata_only"}
    if original_action == "fetch_html":
        return {"final_action": "fetch_html", "policy_reason": "direct_html_candidate"}
    return {"final_action": "review", "policy_reason": f"unknown_action:{original_action}"}


def compact_candidate_row(row: dict[str, Any], decision: dict[str, str]) -> dict[str, Any]:
    return {
        "url_hash": row.get("url_hash") or "",
        "normalized_url": row.get("normalized_url") or "",
        "domain": row.get("domain") or "",
        "registered_domain": row.get("registered_domain") or "",
        "final_action": decision["final_action"],
        "policy_reason": decision["policy_reason"],
        "original_action": row.get("candidate_action") or "",
        "url_kind": row.get("url_kind") or "",
        "url_source": row.get("url_source") or "",
        "fetch_priority": int(row.get("fetch_priority") or 0),
        "provider_article_id": row.get("provider_article_id") or "",
        "canonical_news_id": row.get("canonical_news_id") or "",
        "published_at_utc": row.get("published_at_utc") or "",
        "title": row.get("title") or "",
    }


def compact_attachment_row(row: dict[str, Any], decision: dict[str, str]) -> dict[str, Any]:
    return {
        "url_hash": row.get("url_hash") or "",
        "normalized_url": row.get("normalized_url") or "",
        "final_action": decision["final_action"],
        "policy_reason": decision["policy_reason"],
        "provider_article_id": row.get("provider_article_id") or "",
        "canonical_news_id": row.get("canonical_news_id") or "",
        "raw_artifact_path": row.get("raw_artifact_path") or "",
        "raw_payload_hash": row.get("raw_payload_hash") or "",
        "published_at_utc": row.get("published_at_utc") or "",
        "url_source": row.get("url_source") or "",
        "url_ordinal": row.get("url_ordinal") or 0,
    }


def aggregate_candidate(aggregates: dict[str, dict[str, Any]], candidate: dict[str, Any], sample_limit: int) -> None:
    url_hash = candidate["url_hash"]
    current = aggregates.get(url_hash)
    if current is None:
        current = {
            "url_hash": url_hash,
            "normalized_url": candidate["normalized_url"],
            "domain": candidate["domain"],
            "registered_domain": candidate["registered_domain"],
            "final_action": candidate["final_action"],
            "fetch_priority": candidate["fetch_priority"],
            "occurrence_count": 0,
            "first_published_at_utc": candidate["published_at_utc"],
            "last_published_at_utc": candidate["published_at_utc"],
            "sample_provider_article_ids": [],
            "sample_canonical_news_ids": [],
            "sample_titles": [],
            "original_action_counts": Counter(),
            "url_kind_counts": Counter(),
            "url_source_counts": Counter(),
            "policy_reason_counts": Counter(),
        }
        aggregates[url_hash] = current
    current["occurrence_count"] += 1
    current["fetch_priority"] = max(int(current["fetch_priority"]), int(candidate["fetch_priority"]))
    current["first_published_at_utc"] = min_non_empty(current["first_published_at_utc"], candidate["published_at_utc"])
    current["last_published_at_utc"] = max_non_empty(current["last_published_at_utc"], candidate["published_at_utc"])
    current["original_action_counts"][candidate["original_action"]] += 1
    current["url_kind_counts"][candidate["url_kind"]] += 1
    current["url_source_counts"][candidate["url_source"]] += 1
    current["policy_reason_counts"][candidate["policy_reason"]] += 1
    append_sample(current["sample_provider_article_ids"], candidate["provider_article_id"], sample_limit)
    append_sample(current["sample_canonical_news_ids"], candidate["canonical_news_id"], sample_limit)
    append_sample(current["sample_titles"], candidate["title"], min(5, sample_limit))


def update_policy_domain_stats(stats: Counter[str], row: dict[str, Any], decision: dict[str, str]) -> None:
    stats["url_occurrences"] += 1
    stats[f"original_action:{row.get('candidate_action') or ''}"] += 1
    stats[f"final_action:{decision['final_action']}"] += 1
    stats[f"policy_reason:{decision['policy_reason']}"] += 1


def write_domain_summary(path: Path, domain_stats: dict[str, Counter[str]]) -> None:
    fields = [
        "domain",
        "url_occurrences",
        "fetch_html",
        "fetch_pdf",
        "resolve_redirect",
        "sec_handler",
        "metadata_only",
        "ignore",
        "review",
        "top_policy_reasons",
        "top_original_actions",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(fields) + "\n")
        for domain, stats in sorted(domain_stats.items(), key=lambda item: (-item[1]["url_occurrences"], item[0])):
            values = {
                "domain": domain,
                "url_occurrences": stats["url_occurrences"],
                "fetch_html": stats["final_action:fetch_html"],
                "fetch_pdf": stats["final_action:fetch_pdf"],
                "resolve_redirect": stats["final_action:resolve_redirect"],
                "sec_handler": stats["final_action:sec_handler"],
                "metadata_only": stats["final_action:metadata_only"],
                "ignore": stats["final_action:ignore"],
                "review": stats["final_action:review"],
                "top_policy_reasons": counter_prefix_summary(stats, "policy_reason:"),
                "top_original_actions": counter_prefix_summary(stats, "original_action:"),
            }
            handle.write(",".join(csv_cell(values[field]) for field in fields) + "\n")


def shard_for_url(url_hash: str, shard_count: int) -> int:
    try:
        return int(str(url_hash)[:8], 16) % shard_count
    except ValueError:
        return 0


def append_sample(values: list[str], value: str, limit: int) -> None:
    text = str(value or "").strip()
    if text and text not in values and len(values) < limit:
        values.append(text)


def min_non_empty(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return min(left, right)


def max_non_empty(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)


def counter_prefix_summary(counter: Counter[str], prefix: str, *, limit: int = 8) -> str:
    rows = []
    for key, count in counter.items():
        if key.startswith(prefix):
            rows.append((key.removeprefix(prefix), count))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return ";".join(f"{key}:{count}" for key, count in rows[:limit])


def csv_cell(value: Any) -> str:
    text = str(value)
    if any(char in text for char in [",", "\"", "\n", "\r"]):
        return "\"" + text.replace("\"", "\"\"") + "\""
    return text


if __name__ == "__main__":
    main()
