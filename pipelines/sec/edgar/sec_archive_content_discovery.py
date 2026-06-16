from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import html
import json
import os
import re
import sys
import tarfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files  # noqa: E402


DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_archive_content_discovery")
DEFAULT_ARCHIVE_SUBDIR = "daily_archives"


@dataclass(frozen=True, slots=True)
class DocumentSample:
    archive_date: str
    accession_number: str
    form_type: str
    sequence: int
    document_type: str
    filename: str
    description: str
    content_format: str
    payload_chars: int
    clean_text_chars: int
    text_sha256: str
    prefix: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read SEC daily .nc.tar.gz archives and discover what filing/document/text "
            "content is actually present. This script does not write ClickHouse and does "
            "not download or fetch SEC headers."
        )
    )
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_CORE_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--archive-subdir", default=os.environ.get("SEC_DAILY_ARCHIVE_SUBDIR", DEFAULT_ARCHIVE_SUBDIR))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--start-date", default="", help="Inclusive archive date filter, YYYY-MM-DD.")
    parser.add_argument("--end-date", default="", help="Exclusive archive date filter, YYYY-MM-DD.")
    parser.add_argument("--archive-workers", type=int, default=int(os.environ.get("SEC_ARCHIVE_DISCOVERY_WORKERS", "4")))
    parser.add_argument("--limit-archives", type=int, default=0, help="Optional smoke-test cap after filtering.")
    parser.add_argument("--max-filings-per-archive", type=int, default=0, help="Optional per-archive cap; 0 scans all filings.")
    parser.add_argument("--sample-limit", type=int, default=250, help="Maximum representative document samples in the final sample file.")
    parser.add_argument("--sample-text-chars", type=int, default=600)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--hash-archives", action="store_true", help="Compute SHA-256 prefixes for compressed archives. Disabled by default because archives can be multi-GB.")
    parser.add_argument("--pending-multiplier", type=int, default=2, help="Maximum queued archive jobs per worker.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=False)
    artifact_root = Path(args.artifact_root_win)
    archive_root = artifact_root / args.archive_subdir.strip().strip("\\/")
    output_root = Path(args.output_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    archives = discover_archives(archive_root, args.start_date, args.end_date)
    if args.limit_archives:
        archives = archives[: max(0, args.limit_archives)]

    manifest_path = run_root / "sec_archive_discovery_manifest.json"
    archive_summary_path = run_root / "archive_summary.jsonl"
    samples_path = run_root / "document_samples.jsonl"
    aggregate_path = run_root / "aggregate_summary.json"
    errors_path = run_root / "errors.jsonl"

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).resolve()),
        "artifact_root": str(artifact_root),
        "archive_root": str(archive_root),
        "output_root": str(output_root),
        "run_root": str(run_root),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "archive_count": len(archives),
        "archive_workers": max(1, args.archive_workers),
        "max_filings_per_archive": max(0, args.max_filings_per_archive),
        "sample_limit": max(0, args.sample_limit),
        "sample_text_chars": max(0, args.sample_text_chars),
        "hash_archives": bool(args.hash_archives),
        "pending_multiplier": max(1, args.pending_multiplier),
        "loaded_env_files": [str(path) for path in loaded_env_files],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 96, flush=True)
    print("SEC archive content discovery", flush=True)
    print(f"archive_root={archive_root}", flush=True)
    print(f"archives={len(archives):,} workers={max(1, args.archive_workers)} run_root={run_root}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    aggregate = empty_aggregate()
    sample_reservoir: list[dict[str, Any]] = []
    completed = run_discovery(args, archives, archive_summary_path, errors_path, aggregate, sample_reservoir, started)

    sample_reservoir = rank_samples(sample_reservoir)[: max(0, args.sample_limit)]
    write_jsonl(samples_path, sample_reservoir)
    final_summary = finalize_aggregate(aggregate, manifest, time.perf_counter() - started)
    aggregate_path.write_text(json.dumps(final_summary, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 96, flush=True)
    print(f"done archive_summary={archive_summary_path}", flush=True)
    print(f"done aggregate_summary={aggregate_path}", flush=True)
    print(f"done document_samples={samples_path}", flush=True)
    print("=" * 96, flush=True)


def discover_archives(archive_root: Path, start_date: str, end_date: str) -> list[Path]:
    if not archive_root.exists():
        raise SystemExit(f"archive root does not exist: {archive_root}")
    start_key = start_date.replace("-", "") if start_date else ""
    end_key = end_date.replace("-", "") if end_date else ""
    paths = sorted(archive_root.rglob("*.nc.tar.gz"))
    filtered: list[Path] = []
    for path in paths:
        date_key = path.name[:8]
        if start_key and date_key < start_key:
            continue
        if end_key and date_key >= end_key:
            continue
        filtered.append(path)
    return filtered


def run_discovery(
    args: argparse.Namespace,
    archives: list[Path],
    archive_summary_path: Path,
    errors_path: Path,
    aggregate: dict[str, Any],
    sample_reservoir: list[dict[str, Any]],
    started: float,
) -> int:
    workers = max(1, args.archive_workers)
    max_pending = max(workers, workers * max(1, args.pending_multiplier))
    completed = 0
    submitted = 0
    archive_iter = iter(archives)
    futures: dict[concurrent.futures.Future[dict[str, Any]], Path] = {}
    pool: concurrent.futures.ProcessPoolExecutor | None = None

    def submit_one() -> bool:
        nonlocal submitted
        try:
            path = next(archive_iter)
        except StopIteration:
            return False
        future = pool.submit(  # type: ignore[union-attr]
            scan_archive,
            str(path),
            max(0, args.max_filings_per_archive),
            max(0, args.sample_text_chars),
            max(0, args.sample_limit),
            bool(args.hash_archives),
        )
        futures[future] = path
        submitted += 1
        return True

    with archive_summary_path.open("w", encoding="utf-8") as archive_out, errors_path.open("w", encoding="utf-8") as error_out:
        pool = concurrent.futures.ProcessPoolExecutor(max_workers=workers)
        try:
            while len(futures) < max_pending and submit_one():
                pass
            print(f"submitted_initial={submitted:,} max_pending={max_pending:,}", flush=True)

            while futures:
                done, _ = concurrent.futures.wait(futures, timeout=5.0, return_when=concurrent.futures.FIRST_COMPLETED)
                if not done:
                    elapsed = time.perf_counter() - started
                    print(
                        f"active={len(futures):,} submitted={submitted:,}/{len(archives):,} "
                        f"completed={completed:,} filings={aggregate['filings']:,} "
                        f"documents={aggregate['documents']:,} elapsed={elapsed:.1f}s",
                        flush=True,
                    )
                    continue
                for future in done:
                    path = futures.pop(future)
                    completed += 1
                    try:
                        result = future.result()
                    except Exception as exc:  # pragma: no cover - worker exception report path
                        row = {"archive_path": str(path), "status": "failed", "error": repr(exc)}
                        error_out.write(json.dumps(row, sort_keys=True) + "\n")
                        error_out.flush()
                        aggregate["failed_archives"] += 1
                    else:
                        archive_out.write(json.dumps(result["summary"], sort_keys=True) + "\n")
                        archive_out.flush()
                        merge_aggregate(aggregate, result["summary"])
                        sample_reservoir.extend(result["samples"])
                        if len(sample_reservoir) > args.sample_limit * 3:
                            sample_reservoir[:] = rank_samples(sample_reservoir)[: max(0, args.sample_limit)]
                    while len(futures) < max_pending and submit_one():
                        pass
                    if completed == 1 or completed % max(1, args.progress_every) == 0 or completed == len(archives):
                        elapsed = time.perf_counter() - started
                        print(
                            f"completed={completed:,}/{len(archives):,} submitted={submitted:,} "
                            f"active={len(futures):,} filings={aggregate['filings']:,} "
                            f"documents={aggregate['documents']:,} errors={aggregate['failed_archives']:,} "
                            f"elapsed={elapsed:.1f}s",
                            flush=True,
                        )
        except KeyboardInterrupt:
            print("KeyboardInterrupt received; terminating archive workers and writing partial outputs.", flush=True)
            aggregate["interrupted"] = 1
            terminate_process_pool(pool)
            return completed
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
    return completed


def terminate_process_pool(pool: concurrent.futures.ProcessPoolExecutor) -> None:
    processes = getattr(pool, "_processes", None)
    if not processes:
        return
    for process in list(processes.values()):
        try:
            process.terminate()
        except Exception:
            pass


def scan_archive(path_text: str, max_filings: int, sample_text_chars: int, sample_limit: int, hash_archive: bool = False) -> dict[str, Any]:
    path = Path(path_text)
    archive_date = path.name[:8]
    archive_date_iso = f"{archive_date[:4]}-{archive_date[4:6]}-{archive_date[6:8]}" if len(archive_date) == 8 else ""
    summary = {
        "archive_date": archive_date_iso,
        "archive_path": str(path),
        "archive_bytes": path.stat().st_size,
        "archive_sha256_prefix": sha256_prefix(path) if hash_archive else "",
        "status": "ok",
        "error": "",
        "members": 0,
        "filings": 0,
        "documents": 0,
        "parse_errors": 0,
        "truncated_by_limit": False,
        "forms": Counter(),
        "document_types": Counter(),
        "content_formats": Counter(),
        "file_extensions": Counter(),
        "document_type_by_format": Counter(),
        "form_by_document_type": Counter(),
        "payload_chars_by_format": Counter(),
        "clean_text_chars_by_format": Counter(),
        "empty_text_documents": 0,
        "binary_like_documents": 0,
        "non_ascii_documents": 0,
        "replacement_char_documents": 0,
        "mojibake_suspect_documents": 0,
        "max_payload_chars": 0,
        "max_clean_text_chars": 0,
    }
    samples: list[dict[str, Any]] = []
    try:
        with tarfile.open(path, "r:gz") as tar:
            for member in tar:
                if not member.isfile() or not member.name.lower().endswith(".nc"):
                    continue
                if max_filings and summary["filings"] >= max_filings:
                    summary["truncated_by_limit"] = True
                    break
                summary["members"] += 1
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                raw = handle.read()
                try:
                    filing = inspect_filing(archive_date_iso, member.name, raw, sample_text_chars)
                except Exception as exc:  # pragma: no cover - malformed filing path
                    summary["parse_errors"] += 1
                    samples.append(
                        {
                            "archive_date": archive_date_iso,
                            "member_name": member.name,
                            "sample_reason": "parse_error",
                            "error": repr(exc),
                        }
                    )
                    continue
                summary["filings"] += 1
                summary["forms"][filing["form_type"] or ""] += 1
                for document in filing["documents"]:
                    update_document_summary(summary, filing, document)
                    if should_sample(document):
                        samples.append(sample_row(archive_date_iso, filing, document))
                if len(samples) > sample_limit * 3 and sample_limit:
                    samples = rank_samples(samples)[:sample_limit]
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = repr(exc)
    summary = counter_to_plain(summary)
    return {"summary": summary, "samples": rank_samples(samples)[:sample_limit] if sample_limit else []}


def inspect_filing(archive_date: str, member_name: str, raw: bytes, sample_text_chars: int) -> dict[str, Any]:
    decoded = raw.decode("utf-8", errors="replace")
    header_text = decoded.split("<DOCUMENT>", 1)[0]
    accession = header_value(header_text, "ACCESSION NUMBER") or tag_value(header_text, "ACCESSION-NUMBER") or Path(member_name).stem
    form_type = header_value(header_text, "CONFORMED SUBMISSION TYPE") or tag_value(header_text, "TYPE")
    documents = []
    for block in re.findall(r"<DOCUMENT>\s*(.*?)\s*</DOCUMENT>", decoded, flags=re.S | re.I):
        sequence = parse_int(tag_value(block, "SEQUENCE"))
        document_type = tag_value(block, "TYPE")
        filename = tag_value(block, "FILENAME")
        description = tag_value(block, "DESCRIPTION")
        payload = document_payload(block)
        clean_text = extract_clean_text(payload)
        content_format = detect_content_format(filename, payload)
        documents.append(
            {
                "sequence": sequence,
                "document_type": document_type,
                "filename": filename,
                "description": description,
                "content_format": content_format,
                "file_extension": file_extension(filename),
                "payload_chars": len(payload),
                "clean_text_chars": len(clean_text),
                "text_sha256": hashlib.sha256(clean_text.encode("utf-8", errors="replace")).hexdigest() if clean_text else "",
                "prefix": clean_text[:sample_text_chars],
                "has_non_ascii": any(ord(ch) > 127 for ch in clean_text[:5000]),
                "has_replacement_char": "\ufffd" in payload,
                "has_mojibake": has_mojibake(clean_text),
                "binary_like": is_binary_like(payload),
            }
        )
    return {
        "archive_date": archive_date,
        "member_name": member_name,
        "accession_number": accession,
        "form_type": form_type,
        "documents": documents,
    }


def update_document_summary(summary: dict[str, Any], filing: dict[str, Any], document: dict[str, Any]) -> None:
    document_type = document["document_type"] or ""
    content_format = document["content_format"] or ""
    extension = document["file_extension"] or ""
    form_type = filing["form_type"] or ""
    summary["documents"] += 1
    summary["document_types"][document_type] += 1
    summary["content_formats"][content_format] += 1
    summary["file_extensions"][extension] += 1
    summary["document_type_by_format"][f"{document_type}|{content_format}"] += 1
    summary["form_by_document_type"][f"{form_type}|{document_type}"] += 1
    summary["payload_chars_by_format"][content_format] += document["payload_chars"]
    summary["clean_text_chars_by_format"][content_format] += document["clean_text_chars"]
    summary["max_payload_chars"] = max(summary["max_payload_chars"], document["payload_chars"])
    summary["max_clean_text_chars"] = max(summary["max_clean_text_chars"], document["clean_text_chars"])
    if document["clean_text_chars"] == 0:
        summary["empty_text_documents"] += 1
    if document["binary_like"]:
        summary["binary_like_documents"] += 1
    if document["has_non_ascii"]:
        summary["non_ascii_documents"] += 1
    if document["has_replacement_char"]:
        summary["replacement_char_documents"] += 1
    if document["has_mojibake"]:
        summary["mojibake_suspect_documents"] += 1


def should_sample(document: dict[str, Any]) -> bool:
    if document["clean_text_chars"] == 0:
        return True
    if document["binary_like"] or document["has_replacement_char"] or document["has_mojibake"]:
        return True
    if document["content_format"] in {"html", "xml", "xbrl", "plain_text"}:
        return True
    return False


def sample_row(archive_date: str, filing: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    sample = DocumentSample(
        archive_date=archive_date,
        accession_number=filing["accession_number"],
        form_type=filing["form_type"],
        sequence=document["sequence"],
        document_type=document["document_type"],
        filename=document["filename"],
        description=document["description"],
        content_format=document["content_format"],
        payload_chars=document["payload_chars"],
        clean_text_chars=document["clean_text_chars"],
        text_sha256=document["text_sha256"],
        prefix=document["prefix"],
    )
    row = asdict(sample)
    row["sample_score"] = sample_score(row)
    return row


def rank_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(samples, key=sample_score, reverse=True)


def sample_score(row: dict[str, Any]) -> tuple[int, int, str]:
    fmt = str(row.get("content_format", ""))
    doc_type = str(row.get("document_type", ""))
    score = 0
    if fmt in {"html", "xml", "xbrl", "plain_text"}:
        score += 10
    if fmt in {"binary_like", "unknown"}:
        score += 8
    if doc_type in {"10-K", "10-Q", "8-K", "EX-99.1", "EX-99", "GRAPHIC", "XML"}:
        score += 5
    if int(row.get("clean_text_chars") or 0) == 0:
        score += 4
    return score, int(row.get("clean_text_chars") or 0), str(row.get("filename", ""))


def document_payload(block: str) -> str:
    match = re.search(r"<TEXT>\s*(.*)", block, flags=re.S | re.I)
    payload = match.group(1) if match else ""
    return re.sub(r"</TEXT>\s*$", "", payload, flags=re.S | re.I).strip()


def extract_clean_text(payload: str) -> str:
    if not payload:
        return ""
    text = payload
    text = re.sub(r"(?is)<script\b.*?</script>", " ", text)
    text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\x00", " ")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return normalize_space(text)


def detect_content_format(filename: str, payload: str) -> str:
    lower_name = filename.lower()
    lower_payload = payload[:1000].lower().lstrip()
    if not payload:
        return "empty"
    if is_binary_like(payload):
        return "binary_like"
    if lower_name.endswith((".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff")):
        return "image"
    if lower_name.endswith(".pdf") or lower_payload.startswith("%pdf"):
        return "pdf"
    if lower_name.endswith((".xsd", ".xml", ".xsl")):
        if "xbrl" in lower_payload or "xbrli:" in lower_payload:
            return "xbrl"
        return "xml"
    if "xbrl" in lower_payload or "xbrli:" in lower_payload:
        return "xbrl"
    if lower_name.endswith((".htm", ".html")) or "<html" in lower_payload:
        return "html"
    if lower_name.endswith(".txt"):
        return "plain_text"
    return "plain_text" if len(extract_clean_text(payload)) > 0 else "unknown"


def is_binary_like(payload: str) -> bool:
    if not payload:
        return False
    sample = payload[:4096]
    if "\x00" in sample:
        return True
    replacement_count = sample.count("\ufffd")
    return replacement_count > max(8, len(sample) // 100)


def has_mojibake(text: str) -> bool:
    sample = text[:5000]
    markers = ("Ã¢â‚¬â„¢", "Ã¢â‚¬Å“", "Ã¢â‚¬Â", "Ã¢â‚¬â€œ", "Ã¢â‚¬â€", "Ã‚ ", "ÃƒÂ©", "ÃƒÂ¡", "ÃƒÂ±", "ÃƒÂ¶")
    return any(marker in sample for marker in markers)


def header_value(text: str, label: str) -> str:
    pattern = rf"(?im)^\s*{re.escape(label)}\s*:\s*(.*?)\s*$"
    match = re.search(pattern, text)
    return normalize_space(match.group(1)) if match else ""


def tag_value(text: str, tag: str) -> str:
    match = re.search(rf"<{re.escape(tag)}>\s*([^\r\n<]+)", text, flags=re.I)
    return normalize_space(match.group(1)) if match else ""


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def parse_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def file_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix or ""


def sha256_prefix(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()[:16]


def empty_aggregate() -> dict[str, Any]:
    return {
        "archives": 0,
        "failed_archives": 0,
        "archive_bytes": 0,
        "members": 0,
        "filings": 0,
        "documents": 0,
        "parse_errors": 0,
        "empty_text_documents": 0,
        "binary_like_documents": 0,
        "non_ascii_documents": 0,
        "replacement_char_documents": 0,
        "mojibake_suspect_documents": 0,
        "max_payload_chars": 0,
        "max_clean_text_chars": 0,
        "forms": Counter(),
        "document_types": Counter(),
        "content_formats": Counter(),
        "file_extensions": Counter(),
        "document_type_by_format": Counter(),
        "form_by_document_type": Counter(),
        "payload_chars_by_format": Counter(),
        "clean_text_chars_by_format": Counter(),
    }


def merge_aggregate(aggregate: dict[str, Any], summary: dict[str, Any]) -> None:
    aggregate["archives"] += 1
    if summary.get("status") != "ok":
        aggregate["failed_archives"] += 1
    for key in (
        "archive_bytes",
        "members",
        "filings",
        "documents",
        "parse_errors",
        "empty_text_documents",
        "binary_like_documents",
        "non_ascii_documents",
        "replacement_char_documents",
        "mojibake_suspect_documents",
    ):
        aggregate[key] += int(summary.get(key) or 0)
    aggregate["max_payload_chars"] = max(aggregate["max_payload_chars"], int(summary.get("max_payload_chars") or 0))
    aggregate["max_clean_text_chars"] = max(aggregate["max_clean_text_chars"], int(summary.get("max_clean_text_chars") or 0))
    for key in (
        "forms",
        "document_types",
        "content_formats",
        "file_extensions",
        "document_type_by_format",
        "form_by_document_type",
        "payload_chars_by_format",
        "clean_text_chars_by_format",
    ):
        aggregate[key].update(summary.get(key) or {})


def finalize_aggregate(aggregate: dict[str, Any], manifest: dict[str, Any], wall_seconds: float) -> dict[str, Any]:
    result = dict(aggregate)
    for key, value in list(result.items()):
        if isinstance(value, Counter):
            result[key] = value.most_common(250)
    result["manifest"] = manifest
    result["wall_seconds"] = round(wall_seconds, 3)
    return result


def counter_to_plain(summary: dict[str, Any]) -> dict[str, Any]:
    row = dict(summary)
    for key, value in list(row.items()):
        if isinstance(value, Counter):
            row[key] = dict(value)
    return row


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
