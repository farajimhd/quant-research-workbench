from __future__ import annotations

import argparse
import random
import sys
import tarfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events.sec_packed_text_renderer import (  # noqa: E402
    SEC_PACKED_TEXT_RENDERER_VERSION,
    render_sec_packed_text,
)
from pipelines.sec.edgar.sec_filing_text_extract_parts import (  # noqa: E402
    DEFAULT_ARCHIVE_ROOT_WIN,
    FilingParent,
    archive_date_from_name,
    build_missing_parent_row,
    classify_document_role,
    detect_content_format,
    deterministic_id,
    discover_archives,
    parse_filing,
    should_persist_text_source,
    text_kind_for_role,
)


DEFAULT_OUTPUT_ROOT = REPO_ROOT / ".tmp" / "sec_packed_text_renderer_audit"
WORKSTATION_ARCHIVE_ROOT = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data\sec_core\daily_archives")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit SEC packed text rendering by sampling raw daily archive filings, "
            "separating source documents through the upstream parser, and rendering "
            "submitted text-source payloads into packed model text."
        )
    )
    parser.add_argument("--archive-root-win", default=str(default_archive_root()))
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default=(datetime.now(UTC).date() + timedelta(days=1)).isoformat())
    parser.add_argument("--samples-per-content-type", type=int, default=3)
    parser.add_argument("--content-formats", default="html,xml,plain_text")
    parser.add_argument("--sample-size", type=int, default=0, help="Deprecated. Use --samples-per-content-type.")
    parser.add_argument("--random-seed", type=int, default=711)
    parser.add_argument("--max-archives", type=int, default=0, help="Optional cap after date filtering and shuffling.")
    parser.add_argument("--max-filings-scanned", type=int, default=5000, help="Stop after this many archive filings. Use 0 for no cap.")
    parser.add_argument("--progress-every", type=int, default=500, help="Print archive scan progress every N filings. Use 0 to disable.")
    parser.add_argument("--min-text-chars", type=int, default=100)
    parser.add_argument("--excerpt-chars", type=int, default=4000)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = f"sec_packed_text_renderer_audit_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / f"{run_id}.md"
    samples = collect_samples(args)
    report_path.write_text(render_report(args, samples, report_path), encoding="utf-8")
    print(f"report={report_path}", flush=True)
    print(f"samples={len(samples)} renderer={SEC_PACKED_TEXT_RENDERER_VERSION}", flush=True)
    collected = Counter(str(sample.get("content_format") or "") for sample in samples)
    expected_formats = parse_content_formats(args.content_formats)
    expected_per_format = max(1, int(args.samples_per_content_type))
    return 0 if all(collected[fmt] >= expected_per_format for fmt in expected_formats) else 2


def collect_samples(args: argparse.Namespace) -> list[dict[str, Any]]:
    archives = discover_archives(Path(args.archive_root_win), args.start_date, args.end_date)
    rng = random.Random(int(args.random_seed))
    rng.shuffle(archives)
    if int(args.max_archives) > 0:
        archives = archives[: int(args.max_archives)]
    target_formats = parse_content_formats(args.content_formats)
    samples_per_format = max(1, int(args.samples_per_content_type))
    samples_by_format: dict[str, list[dict[str, Any]]] = {fmt: [] for fmt in target_formats}
    seen_documents: set[tuple[str, str]] = set()
    payload_config = {
        "source_run_id": f"audit_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        "min_text_chars": int(args.min_text_chars),
        "max_text_chars": 0,
        "sample_text_chars": int(args.excerpt_chars),
    }
    inserted_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    scanned_archives = 0
    scanned_filings = 0
    scan_limited = False
    max_filings = max(0, int(args.max_filings_scanned))
    progress_every = max(0, int(args.progress_every))
    for archive in archives:
        if all(len(samples_by_format[fmt]) >= samples_per_format for fmt in target_formats) or scan_limited:
            break
        scanned_archives += 1
        try:
            archive_date = archive_date_from_name(archive.name)
        except ValueError:
            continue
        archive_date_text = archive_date.isoformat()
        try:
            with tarfile.open(archive, "r:gz") as tar:
                for member in tar:
                    if not member.isfile():
                        continue
                    if all(len(samples_by_format[fmt]) >= samples_per_format for fmt in target_formats):
                        break
                    if max_filings and scanned_filings >= max_filings:
                        scan_limited = True
                        break
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    scanned_filings += 1
                    if progress_every and scanned_filings % progress_every == 0:
                        collected_text = ", ".join(f"{fmt}={len(samples_by_format[fmt])}" for fmt in target_formats)
                        print(f"scan_progress filings={scanned_filings} collected={collected_text}", flush=True)
                    raw = handle.read()
                    needed_formats = {fmt for fmt in target_formats if len(samples_by_format[fmt]) < samples_per_format}
                    for sample in samples_from_member(payload_config, archive, archive_date, archive_date_text, member.name, raw, inserted_at, needed_formats):
                        content_format = str(sample.get("content_format") or "")
                        if content_format not in samples_by_format or len(samples_by_format[content_format]) >= samples_per_format:
                            continue
                        key = (str(sample.get("accession_number") or ""), str(sample.get("document_id") or ""))
                        if key in seen_documents:
                            continue
                        seen_documents.add(key)
                        samples_by_format[content_format].append(sample)
        except (tarfile.TarError, OSError, UnicodeError) as exc:
            print(f"archive_error path={archive} error={exc!r}", flush=True)
    args.audit_scanned_archives = scanned_archives
    args.audit_scanned_filings = scanned_filings
    args.audit_scan_limited = scan_limited
    return [sample for fmt in target_formats for sample in samples_by_format[fmt]]


def samples_from_member(
    payload_config: dict[str, Any],
    archive: Path,
    archive_date: datetime.date,
    archive_date_text: str,
    member_name: str,
    raw: bytes,
    inserted_at: str,
    needed_formats: set[str],
) -> list[dict[str, Any]]:
    filing = parse_filing(raw, member_name)
    if not filing.get("documents"):
        return []
    _filing_row, parent = build_missing_parent_row(payload_config, archive, archive_date, archive_date_text, member_name, raw, filing, inserted_at)
    samples: list[tuple[int, dict[str, Any]]] = []
    for document in filing["documents"]:
        content_format = detect_content_format(str(document.get("document_name") or ""), str(document.get("payload") or ""))
        if content_format not in needed_formats:
            continue
        document_role = classify_document_role(parent, document, content_format)
        if not should_persist_text_source(document_role, content_format):
            continue
        source_text = str(document.get("payload") or "")
        if len(source_text) < int(payload_config["min_text_chars"]):
            continue
        doc_row = build_audit_doc_row(parent, document, content_format, document_role)
        text_source_row = build_audit_text_source_row(parent, document, doc_row, source_text)
        samples.append((_document_role_priority(document_role), build_sample(archive, member_name, parent, doc_row, text_source_row)))
    return [sample for _priority, sample in sorted(samples, key=lambda item: item[0])]


def build_audit_doc_row(parent: FilingParent, document: dict[str, Any], content_format: str, document_role: str) -> dict[str, Any]:
    doc_name = str(document.get("document_name") or "")
    doc_type = str(document.get("document_type") or "")
    document_id = deterministic_id(
        "sec-document-v2",
        parent.cik,
        parent.accession_number,
        str(document.get("sequence_number") or 0),
        doc_name,
        doc_type,
    )
    return {
        "document_id": document_id,
        "sequence_number": int(document.get("sequence_number") or 0),
        "document_name": doc_name,
        "document_type": doc_type,
        "document_role": document_role,
        "content_format": content_format,
    }


def build_audit_text_source_row(
    parent: FilingParent,
    document: dict[str, Any],
    doc_row: dict[str, Any],
    source_text: str,
) -> dict[str, Any]:
    return {
        "document_id": doc_row["document_id"],
        "accession_number": parent.accession_number,
        "accession_number_compact": parent.accession_number_compact,
        "cik": parent.cik,
        "sequence_number": int(doc_row["sequence_number"]),
        "document_name": doc_row["document_name"],
        "document_type": doc_row["document_type"],
        "document_role": doc_row["document_role"],
        "description": document.get("description") or None,
        "text_kind": text_kind_for_role(str(doc_row["document_role"])),
        "content_format": doc_row["content_format"],
        "source_text": source_text,
        "source_text_char_count": len(source_text),
        "source_text_byte_count": len(source_text.encode("utf-8", errors="replace")),
    }


def build_sample(
    archive: Path,
    member_name: str,
    parent: FilingParent,
    doc_row: dict[str, Any],
    text_source_row: dict[str, Any],
) -> dict[str, Any]:
    source_text = str(text_source_row.get("source_text") or "")
    result = render_sec_packed_text(
        source_text,
        str(text_source_row.get("content_format") or ""),
        document_name=str(text_source_row.get("document_name") or ""),
        document_type=str(text_source_row.get("document_type") or ""),
        form_type=parent.form_type,
        text_kind=str(text_source_row.get("text_kind") or ""),
    )
    return {
        "archive_path": str(archive),
        "archive_member": member_name,
        "document_id": text_source_row.get("document_id") or "",
        "accession_number": parent.accession_number,
        "cik": parent.cik,
        "form_type": parent.form_type,
        "document_name": text_source_row.get("document_name") or "",
        "document_type": text_source_row.get("document_type") or "",
        "content_format": text_source_row.get("content_format") or "",
        "document_role": doc_row.get("document_role") or "",
        "text_kind": text_source_row.get("text_kind") or "",
        "source_chars": len(source_text),
        "packed_text_chars": len(result.packed_text),
        "block_count": result.block_count,
        "table_block_count": result.table_block_count,
        "duplicate_block_count": result.duplicate_block_count,
        "source_text_hash": result.source_text_hash,
        "packed_text_hash": result.packed_text_hash,
        "renderer": result.renderer_version,
        "quality_flags": ",".join(result.quality_flags),
        "upstream_source_text": source_text,
        "intermediate": result.intermediate_text,
        "duplicate_block_samples": result.duplicate_block_samples,
        "packed_text": result.packed_text,
    }


def render_report(args: argparse.Namespace, samples: list[dict[str, Any]], report_path: Path) -> str:
    target_formats = parse_content_formats(args.content_formats)
    collected = Counter(str(sample.get("content_format") or "") for sample in samples)
    missing_formats = [fmt for fmt in target_formats if collected[fmt] < max(1, int(args.samples_per_content_type))]
    lines = [
        "# SEC Packed Text Renderer Audit",
        "",
        f"- Generated at UTC: `{datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')}`",
        f"- Renderer: `{SEC_PACKED_TEXT_RENDERER_VERSION}`",
        f"- Archive root: `{args.archive_root_win}`",
        f"- Date range: `{args.start_date}` to `{args.end_date}` exclusive",
        f"- Content formats: `{', '.join(target_formats)}`",
        f"- Requested samples per content format: `{max(1, int(args.samples_per_content_type))}`",
        f"- Collected by content format: `{', '.join(f'{fmt}={collected[fmt]}' for fmt in target_formats)}`",
        f"- Missing or incomplete content formats: `{', '.join(missing_formats) if missing_formats else 'none'}`",
        f"- Scanned archives: `{getattr(args, 'audit_scanned_archives', 0)}`",
        f"- Scanned filings: `{getattr(args, 'audit_scanned_filings', 0)}`",
        f"- Scan limited by cap: `{bool(getattr(args, 'audit_scan_limited', False))}`",
        f"- Collected samples: `{len(samples)}`",
        f"- Excerpt chars per section: `{int(args.excerpt_chars)}`",
        f"- Report path: `{report_path}`",
        "",
        "## Summary",
        "",
        "| # | Format | Accession | CIK | Form | Document | Role | Upstream Source Chars | Packed Chars | Blocks | Table Blocks | Duplicates | Flags |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, sample in enumerate(samples, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    md_cell(sample.get("content_format")),
                    md_cell(sample.get("accession_number")),
                    md_cell(sample.get("cik")),
                    md_cell(sample.get("form_type")),
                    md_cell(sample.get("document_name")),
                    md_cell(sample.get("document_role")),
                    str(sample.get("source_chars", 0)),
                    str(sample.get("packed_text_chars", 0)),
                    str(sample.get("block_count", 0)),
                    str(sample.get("table_block_count", 0)),
                    str(sample.get("duplicate_block_count", 0)),
                    md_cell(sample.get("quality_flags")),
                ]
            )
            + " |"
        )
    lines.append("")
    for index, sample in enumerate(samples, 1):
        lines.extend(render_sample(index, sample, int(args.excerpt_chars)))
    return "\n".join(lines).rstrip() + "\n"


def render_sample(index: int, sample: dict[str, Any], excerpt_chars: int) -> list[str]:
    title = f"## Sample {index} [{sample.get('content_format') or 'unknown'}]: {sample.get('accession_number') or 'error'}"
    lines = [
        title,
        "",
        f"- Archive: `{sample.get('archive_path', '')}`",
        f"- Member: `{sample.get('archive_member', '')}`",
        f"- CIK/Form: `{sample.get('cik', '')}` / `{sample.get('form_type', '')}`",
        f"- Document: `{sample.get('document_name', '')}` (`{sample.get('document_type', '')}`, `{sample.get('document_role', '')}`, `{sample.get('content_format', '')}`)",
        f"- Hashes: source `{sample.get('source_text_hash', 0)}`, packed `{sample.get('packed_text_hash', 0)}`",
        f"- Counts: upstream source `{sample.get('source_chars', 0)}`, packed `{sample.get('packed_text_chars', 0)}`, blocks `{sample.get('block_count', 0)}`, table blocks `{sample.get('table_block_count', 0)}`, duplicate blocks `{sample.get('duplicate_block_count', 0)}`",
        "",
        "### Upstream Extracted Text Source",
        "",
        fenced_excerpt(sample.get("upstream_source_text", ""), excerpt_chars),
        "",
        "### Renderer Intermediate Blocks",
        "",
        fenced_excerpt(sample.get("intermediate", ""), excerpt_chars),
        "",
        "### Repeated Blocks Found",
        "",
        *render_duplicate_block_samples(sample, excerpt_chars),
        "### Packed Renderer Output",
        "",
        fenced_excerpt(sample.get("packed_text", ""), excerpt_chars),
        "",
    ]
    return lines


def render_duplicate_block_samples(sample: dict[str, Any], excerpt_chars: int) -> list[str]:
    duplicate_samples = sample.get("duplicate_block_samples") or []
    if not duplicate_samples:
        return ["No repeated blocks detected in the renderer block hashes.", ""]
    lines = [f"Showing up to `{len(duplicate_samples)}` repeated block examples.", ""]
    duplicate_excerpt_chars = min(max(400, excerpt_chars // 3), 1200)
    for index, text in enumerate(duplicate_samples, 1):
        lines.extend([f"#### Repeated Block {index}", "", fenced_excerpt(text, duplicate_excerpt_chars), ""])
    return lines


def fenced_excerpt(value: Any, excerpt_chars: int) -> str:
    text = str(value or "")
    suffix = "\n\n[truncated]" if len(text) > excerpt_chars else ""
    return "```text\n" + text[:excerpt_chars].replace("```", "'''") + suffix + "\n```"


def md_cell(value: Any) -> str:
    text = str(value or "")
    return text.replace("|", "\\|").replace("\n", " ")[:220]


def parse_content_formats(value: str) -> list[str]:
    formats: list[str] = []
    for part in str(value or "").split(","):
        fmt = part.strip().lower()
        if fmt and fmt not in formats:
            formats.append(fmt)
    return formats or ["html", "xml", "plain_text"]


def _document_role_priority(role: str) -> int:
    order = {
        "primary_document": 0,
        "press_release_exhibit": 1,
        "material_exhibit": 2,
        "proxy_document": 3,
        "prospectus": 4,
        "other_text_exhibit": 5,
        "other_text_document": 6,
    }
    return order.get(role, 99)


def default_archive_root() -> Path:
    if DEFAULT_ARCHIVE_ROOT_WIN.exists():
        return DEFAULT_ARCHIVE_ROOT_WIN
    if WORKSTATION_ARCHIVE_ROOT.exists():
        return WORKSTATION_ARCHIVE_ROOT
    return DEFAULT_ARCHIVE_ROOT_WIN


if __name__ == "__main__":
    raise SystemExit(main())
