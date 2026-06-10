from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import parse


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from research.mlops.news_benzinga_normalize import (  # noqa: E402
    BENZINGA_PROVIDER,
    extract_pdf_urls,
    html_to_text_and_links,
    normalize_string_array,
    normalize_text,
    parse_provider_datetime,
    provider_id,
    resolve_url,
    stable_hash,
    url_domain,
)


DEFAULT_RAW_ROOT_WIN = Path("D:/market-data/news-benzinga")
ALT_RAW_ROOT_WIN = Path("D:/market-data/news_benzinga")
LEGACY_RAW_ROOT_WIN = Path("D:/market-data/benzinga_news_canonical/raw")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/benzinga_news_url_inventory")
URL_RE = re.compile(r"https?://[^\s\"'<>\\]+", re.IGNORECASE)
IMAGE_EXTENSIONS = {".apng", ".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
VIDEO_EXTENSIONS = {".m3u8", ".mov", ".mp4", ".webm"}
DOCUMENT_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx", ".xls", ".xlsx"}
QUOTE_PATH_RE = re.compile(r"^/(quote|stock|symbol)/", re.IGNORECASE)
REGULATOR_DOMAINS = {
    "cboe.com",
    "doj.gov",
    "fda.gov",
    "federalreserve.gov",
    "finra.org",
    "ftc.gov",
    "nasdaq.com",
    "nyse.com",
    "otcmarkets.com",
    "sec.gov",
    "treasury.gov",
}


@dataclass(frozen=True, slots=True)
class InventoryResult:
    file_count: int
    article_count: int
    url_count: int
    error_count: int
    url_rows: list[dict[str, Any]]
    errors: list[dict[str, Any]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan downloaded raw Benzinga JSON files once and build a URL inventory. "
            "No network calls and no ClickHouse writes are performed."
        )
    )
    parser.add_argument("--raw-root-win", default=None, help="Raw Benzinga JSON root. Defaults to NEWS_BENZINGA_RAW_ROOT_WIN or D:/market-data/news-benzinga.")
    parser.add_argument("--output-root-win", default=os.environ.get("NEWS_BENZINGA_URL_INVENTORY_OUTPUT_ROOT_WIN") or str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_INVENTORY_PROCESSES", "8")))
    parser.add_argument("--chunk-size", type=int, default=int(os.environ.get("NEWS_BENZINGA_URL_INVENTORY_CHUNK_SIZE", "1000")))
    parser.add_argument("--limit-files", type=int, default=0, help="Optional smoke-test cap.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    raw_root = resolve_raw_root(args)
    output_root = Path(args.output_root_win)
    if not raw_root.exists():
        raise SystemExit(f"raw root does not exist: {raw_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    url_inventory_path = run_root / "news_url_inventory.jsonl"
    error_path = run_root / "news_url_inventory_errors.jsonl"
    domain_summary_path = run_root / "news_domain_summary.csv"
    policy_seed_path = run_root / "news_url_policy_seed.json"
    manifest_path = run_root / "news_url_inventory_manifest.json"

    files = sorted(raw_root.rglob("*.json"))
    if args.limit_files:
        files = files[: max(0, args.limit_files)]
    chunk_size = max(1, args.chunk_size)
    chunks = [files[index : index + chunk_size] for index in range(0, len(files), chunk_size)]

    print("=" * 96, flush=True)
    print("Benzinga URL inventory", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"raw_root={raw_root}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"files={len(files):,} chunks={len(chunks):,} processes={max(1, args.processes)} chunk_size={chunk_size:,}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print(f"secret_status={secret_status(['MASSIVE_API_KEY'])}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    domain_stats: dict[str, dict[str, Any]] = defaultdict(new_domain_stats)
    policy_domains: dict[str, dict[str, Any]] = {}
    totals = Counter()

    with url_inventory_path.open("w", encoding="utf-8") as url_handle, error_path.open("w", encoding="utf-8") as error_handle:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max(1, args.processes)) as pool:
            futures = {pool.submit(process_chunk, [str(path) for path in chunk]): index for index, chunk in enumerate(chunks, start=1)}
            for future in concurrent.futures.as_completed(futures):
                chunk_index = futures[future]
                result = future.result()
                totals["files"] += result.file_count
                totals["articles"] += result.article_count
                totals["urls"] += result.url_count
                totals["errors"] += result.error_count
                for row in result.url_rows:
                    url_handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                    update_domain_stats(domain_stats[row["registered_domain"] or row["domain"]], row)
                    update_policy_seed(policy_domains, row)
                for error_row in result.errors:
                    error_handle.write(json.dumps(error_row, ensure_ascii=False, separators=(",", ":"), default=str) + "\n")
                print(
                    f"chunk={chunk_index:,}/{len(chunks):,} files={totals['files']:,} "
                    f"articles={totals['articles']:,} urls={totals['urls']:,} errors={totals['errors']:,}",
                    flush=True,
                )

    write_domain_summary(domain_summary_path, domain_stats)
    write_policy_seed(policy_seed_path, policy_domains)
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "raw_root": str(raw_root),
        "run_root": str(run_root),
        "url_inventory_path": str(url_inventory_path),
        "error_path": str(error_path),
        "domain_summary_path": str(domain_summary_path),
        "policy_seed_path": str(policy_seed_path),
        "file_count": totals["files"],
        "article_count": totals["articles"],
        "url_count": totals["urls"],
        "error_count": totals["errors"],
        "domain_count": len(domain_stats),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "loaded_env_files": [str(path) for path in loaded_env_files],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print("manifest_path=" + str(manifest_path), flush=True)
    print("summary=" + json.dumps(manifest, sort_keys=True), flush=True)


def resolve_raw_root(args: argparse.Namespace) -> Path:
    explicit_value = str(args.raw_root_win or "").strip()
    env_value = str(os.environ.get("NEWS_BENZINGA_RAW_ROOT_WIN") or "").strip()
    if explicit_value:
        return Path(explicit_value)
    if env_value:
        return Path(env_value)
    if DEFAULT_RAW_ROOT_WIN.exists():
        return DEFAULT_RAW_ROOT_WIN
    if ALT_RAW_ROOT_WIN.exists():
        return ALT_RAW_ROOT_WIN
    return LEGACY_RAW_ROOT_WIN


def process_chunk(paths: list[str]) -> InventoryResult:
    url_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    article_count = 0
    for raw_path_text in paths:
        raw_path = Path(raw_path_text)
        try:
            raw_text = raw_path.read_text(encoding="utf-8")
            payload = json.loads(raw_text)
            if not isinstance(payload, dict):
                raise ValueError("raw payload is not a JSON object")
            rows = inventory_payload(payload, raw_path=raw_path, raw_text=raw_text)
            article_count += 1
            url_rows.extend(rows)
        except Exception as exc:  # noqa: BLE001
            errors.append({"raw_artifact_path": str(raw_path), "exception": repr(exc)})
    return InventoryResult(
        file_count=len(paths),
        article_count=article_count,
        url_count=len(url_rows),
        error_count=len(errors),
        url_rows=url_rows,
        errors=errors,
    )


def inventory_payload(payload: dict[str, Any], *, raw_path: Path, raw_text: str) -> list[dict[str, Any]]:
    provider_article_id = provider_id(payload)
    if not provider_article_id:
        raise ValueError("missing provider_article_id")
    title = normalize_text(str(payload.get("title") or ""))
    published_raw = str(payload.get("published") or "")
    published_at = parse_provider_datetime(published_raw)
    canonical_news_id = stable_hash("|".join([BENZINGA_PROVIDER, provider_article_id, published_raw, title]))
    raw_payload_hash = stable_hash(json.dumps(payload, sort_keys=True, default=str))
    article_url = str(payload.get("url") or "").strip()
    body_html = str(payload.get("body") or "")
    body_text, body_links_raw = html_to_text_and_links(body_html)
    body_links = [safe_resolve_url(article_url, url) for url in body_links_raw]
    images = normalize_string_array(payload.get("images") or payload.get("image"))
    tickers = normalize_string_array(payload.get("tickers"), upper=True)
    channels = normalize_string_array(payload.get("channels"))
    tags = normalize_string_array(payload.get("tags"))

    candidates: list[tuple[str, str]] = []
    if article_url:
        candidates.append(("provider_article_url", article_url))
    for url in body_links:
        candidates.append(("body_link", url))
    for url in extract_pdf_urls(body_html):
        candidates.append(("body_pdf_regex", safe_resolve_url(article_url, url)))
    for url in images:
        candidates.append(("image_url", url))
    for url in recursive_url_strings(payload):
        candidates.append(("raw_json_url_string", safe_resolve_url(article_url, url)))

    rows: list[dict[str, Any]] = []
    seen_source_url: set[tuple[str, str]] = set()
    ordinal = 0
    for url_source, raw_url in candidates:
        normalized_url = normalize_url(raw_url)
        if not normalized_url:
            continue
        key = (url_source, normalized_url)
        if key in seen_source_url:
            continue
        seen_source_url.add(key)
        ordinal += 1
        labels = classify_url(normalized_url, url_source=url_source)
        url_hash = stable_hash(normalized_url)
        url_row_id = stable_hash("|".join([BENZINGA_PROVIDER, provider_article_id, raw_payload_hash, str(ordinal), url_hash]))
        rows.append(
            {
                "url_row_id": url_row_id,
                "provider": BENZINGA_PROVIDER,
                "provider_article_id": provider_article_id,
                "canonical_news_id": canonical_news_id,
                "raw_artifact_path": str(raw_path),
                "raw_payload_hash": raw_payload_hash,
                "published_at_utc": published_at.isoformat().replace("+00:00", "Z"),
                "published_raw": published_raw,
                "title": title,
                "tickers": tickers,
                "channels": channels,
                "provider_tags": tags,
                "url_source": url_source,
                "url_ordinal": ordinal,
                "url": raw_url,
                "normalized_url": normalized_url,
                "url_hash": url_hash,
                **labels,
                "body_text_chars": len(body_text),
                "title_only_flag": 1 if title and not body_text else 0,
            }
        )
    return rows


def recursive_url_strings(value: Any) -> list[str]:
    output: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            output.extend(recursive_url_strings(item))
    elif isinstance(value, list):
        for item in value:
            output.extend(recursive_url_strings(item))
    elif isinstance(value, str):
        output.extend(match.group(0).rstrip(").,;]") for match in URL_RE.finditer(value))
    return unique_preserve(output)


def safe_resolve_url(base_url: str, url: str) -> str:
    try:
        return resolve_url(base_url, url)
    except ValueError:
        return str(url or "").strip()


def normalize_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for candidate in candidate_url_variants(text):
        try:
            parsed = parse.urlparse(candidate)
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                continue
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            path = parsed.path or "/"
            query = parse.urlencode(sorted(parse.parse_qsl(parsed.query, keep_blank_values=True)))
            return parse.urlunparse((scheme, netloc, path, "", query, ""))
        except ValueError:
            continue
    return ""


def candidate_url_variants(value: str) -> list[str]:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return []
    candidates = [text]
    for match in re.finditer(r"https?://", text, flags=re.IGNORECASE):
        embedded = text[match.start() :].strip().strip("[]()<>\"'").rstrip("].,;")
        if embedded and embedded not in candidates:
            candidates.append(embedded)
    return candidates


def classify_url(url: str, *, url_source: str) -> dict[str, Any]:
    parsed = parse.urlparse(url)
    domain = parsed.netloc.lower()
    registered = registered_domain(domain)
    path = parsed.path or "/"
    extension = Path(path).suffix.lower()
    query_keys = sorted(key for key, _value in parse.parse_qsl(parsed.query, keep_blank_values=True))
    is_pdf = int(extension == ".pdf" or ".pdf" in path.lower())
    is_image = int(extension in IMAGE_EXTENSIONS or url_source == "image_url")
    is_video = int(extension in VIDEO_EXTENSIONS)
    is_benzinga = int(domain == "benzinga.com" or domain.endswith(".benzinga.com"))
    is_quote = int(bool(is_benzinga and QUOTE_PATH_RE.match(path)))
    is_sec = int(registered == "sec.gov" or domain.endswith(".sec.gov"))
    is_regulator = int(registered in REGULATOR_DOMAINS or is_sec)

    if is_quote:
        url_kind = "quote_page"
        action = "ignore"
        reason = "benzinga_quote_page"
        priority = 0
        handler = "none"
    elif is_image:
        url_kind = "image"
        action = "ignore"
        reason = "image_asset"
        priority = 0
        handler = "none"
    elif is_video:
        url_kind = "video"
        action = "metadata_only"
        reason = "video_asset"
        priority = 0
        handler = "none"
    elif is_sec:
        url_kind = "sec_archive" if "/Archives/edgar/" in path else "regulator_page"
        action = "sec_handler"
        reason = "sec_url"
        priority = 4
        handler = "sec"
    elif is_pdf:
        url_kind = "pdf"
        action = "fetch_pdf"
        reason = "pdf_candidate"
        priority = 3 if looks_material_path(path) else 2
        handler = "pdf"
    elif is_benzinga:
        url_kind = "provider_article"
        action = "ignore"
        reason = "benzinga_provider_page"
        priority = 0
        handler = "none"
    elif is_regulator:
        url_kind = "regulator_page"
        action = "fetch_html"
        reason = "regulator_domain"
        priority = 3
        handler = "browser_html"
    elif domain in {"twitter.com", "x.com", "facebook.com", "linkedin.com", "youtube.com", "youtu.be", "reddit.com"} or domain.endswith(".twitter.com"):
        url_kind = "social"
        action = "metadata_only"
        reason = "social_or_video"
        priority = 1
        handler = "none"
    else:
        url_kind = "article_html"
        action = "fetch_html"
        reason = "external_html_candidate"
        priority = 2
        handler = "browser_html"

    return {
        "domain": domain,
        "registered_domain": registered,
        "path": path,
        "query_key_signature": ",".join(unique_preserve(query_keys)),
        "extension": extension,
        "content_hint": content_hint(extension, url_source),
        "is_pdf": is_pdf,
        "is_image": is_image,
        "is_benzinga_url": is_benzinga,
        "is_quote_url": is_quote,
        "is_sec_url": is_sec,
        "url_kind": url_kind,
        "url_role": url_source,
        "candidate_action": action,
        "ignore_reason": reason if action in {"ignore", "metadata_only"} else "",
        "fetch_priority": priority,
        "rate_limit_group": registered or domain,
        "requires_special_handler": handler,
    }


def content_hint(extension: str, url_source: str) -> str:
    if extension == ".pdf":
        return "pdf"
    if extension in IMAGE_EXTENSIONS or url_source == "image_url":
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in DOCUMENT_EXTENSIONS:
        return "document"
    return "html_or_unknown"


def looks_material_path(path: str) -> bool:
    lowered = path.lower()
    keywords = ["8-k", "10-k", "10-q", "424b", "earnings", "fda", "guidance", "investor", "merger", "offering", "press-release", "prospectus", "s-1"]
    return any(keyword in lowered for keyword in keywords)


def registered_domain(domain: str) -> str:
    host = domain.lower().strip(".")
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if parts[-2] in {"co", "com", "net", "org"} and len(parts[-1]) == 2 and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def unique_preserve(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def new_domain_stats() -> dict[str, Any]:
    return {
        "url_count": 0,
        "article_ids": set(),
        "pdf_count": 0,
        "html_count": 0,
        "image_count": 0,
        "title_only_article_ids": set(),
        "short_body_article_ids": set(),
        "actions": Counter(),
        "kinds": Counter(),
        "paths": Counter(),
        "channels": Counter(),
        "tickers": Counter(),
    }


def update_domain_stats(stats: dict[str, Any], row: dict[str, Any]) -> None:
    stats["url_count"] += 1
    stats["article_ids"].add(row["provider_article_id"])
    if row["is_pdf"]:
        stats["pdf_count"] += 1
    elif row["is_image"]:
        stats["image_count"] += 1
    else:
        stats["html_count"] += 1
    if row["title_only_flag"]:
        stats["title_only_article_ids"].add(row["provider_article_id"])
    if 0 < int(row["body_text_chars"]) < 300:
        stats["short_body_article_ids"].add(row["provider_article_id"])
    stats["actions"][row["candidate_action"]] += 1
    stats["kinds"][row["url_kind"]] += 1
    stats["paths"][row["path"]] += 1
    stats["channels"].update(row.get("channels") or [])
    stats["tickers"].update(row.get("tickers") or [])


def write_domain_summary(path: Path, domain_stats: dict[str, dict[str, Any]]) -> None:
    rows = []
    for domain, stats in domain_stats.items():
        suggested = stats["actions"].most_common(1)[0][0] if stats["actions"] else "metadata_only"
        rows.append(
            {
                "domain": domain,
                "url_count": stats["url_count"],
                "article_count": len(stats["article_ids"]),
                "pdf_count": stats["pdf_count"],
                "html_count": stats["html_count"],
                "image_count": stats["image_count"],
                "title_only_article_count": len(stats["title_only_article_ids"]),
                "short_body_article_count": len(stats["short_body_article_ids"]),
                "suggested_policy": suggested,
                "top_actions": counter_summary(stats["actions"]),
                "top_kinds": counter_summary(stats["kinds"]),
                "top_paths": counter_summary(stats["paths"], limit=8),
                "top_channels": counter_summary(stats["channels"], limit=8),
                "top_tickers_sample": counter_summary(stats["tickers"], limit=8),
            }
        )
    rows.sort(key=lambda row: (-int(row["url_count"]), str(row["domain"])))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["domain", "url_count"])
        writer.writeheader()
        writer.writerows(rows)


def update_policy_seed(policy_domains: dict[str, dict[str, Any]], row: dict[str, Any]) -> None:
    domain_key = row["registered_domain"] or row["domain"]
    current = policy_domains.setdefault(
        domain_key,
        {
            "domain": domain_key,
            "suggested_action_counts": Counter(),
            "suggested_kind_counts": Counter(),
            "default_action": row["candidate_action"],
            "rate_limit_group": row["rate_limit_group"],
            "requires_special_handler": row["requires_special_handler"],
            "notes": "generated_from_url_inventory_review_before_enrichment",
        },
    )
    current["suggested_action_counts"][row["candidate_action"]] += 1
    current["suggested_kind_counts"][row["url_kind"]] += 1
    current["default_action"] = current["suggested_action_counts"].most_common(1)[0][0]
    if row["requires_special_handler"] != "none":
        current["requires_special_handler"] = row["requires_special_handler"]


def write_policy_seed(path: Path, policy_domains: dict[str, dict[str, Any]]) -> None:
    output = []
    for domain, row in sorted(policy_domains.items()):
        output.append(
            {
                "domain": domain,
                "default_action": row["default_action"],
                "rate_limit_group": row["rate_limit_group"],
                "requires_special_handler": row["requires_special_handler"],
                "suggested_action_counts": dict(row["suggested_action_counts"]),
                "suggested_kind_counts": dict(row["suggested_kind_counts"]),
                "notes": row["notes"],
            }
        )
    path.write_text(json.dumps({"version": "news-url-policy-seed-v1", "domains": output}, indent=2, sort_keys=True), encoding="utf-8")


def counter_summary(counter: Counter, *, limit: int = 5) -> str:
    return "; ".join(f"{key}:{count}" for key, count in counter.most_common(limit))


if __name__ == "__main__":
    main()
