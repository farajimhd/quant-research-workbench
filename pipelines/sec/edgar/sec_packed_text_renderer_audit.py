from __future__ import annotations

import argparse
import random
import sys
import tarfile
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
    build_rows,
    discover_archives,
    parse_filing,
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
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--random-seed", type=int, default=711)
    parser.add_argument("--max-archives", type=int, default=0, help="Optional cap after date filtering and shuffling.")
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
    return 0 if len(samples) >= int(args.sample_size) else 2


def collect_samples(args: argparse.Namespace) -> list[dict[str, Any]]:
    archives = discover_archives(Path(args.archive_root_win), args.start_date, args.end_date)
    rng = random.Random(int(args.random_seed))
    rng.shuffle(archives)
    if int(args.max_archives) > 0:
        archives = archives[: int(args.max_archives)]
    samples: list[dict[str, Any]] = []
    seen_accessions: set[str] = set()
    payload_config = {
        "source_run_id": f"audit_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        "min_text_chars": int(args.min_text_chars),
        "max_text_chars": 0,
        "sample_text_chars": int(args.excerpt_chars),
    }
    inserted_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    for archive in archives:
        if len(samples) >= int(args.sample_size):
            break
        try:
            archive_date = archive_date_from_name(archive.name)
        except ValueError:
            continue
        archive_date_text = archive_date.isoformat()
        try:
            with tarfile.open(archive, "r:gz") as tar:
                members = [member for member in tar if member.isfile()]
                rng.shuffle(members)
                for member in members:
                    if len(samples) >= int(args.sample_size):
                        break
                    handle = tar.extractfile(member)
                    if handle is None:
                        continue
                    raw = handle.read()
                    sample = sample_from_member(payload_config, archive, archive_date, archive_date_text, member.name, raw, inserted_at)
                    if sample is None:
                        continue
                    accession = str(sample["accession_number"])
                    if accession in seen_accessions:
                        continue
                    seen_accessions.add(accession)
                    samples.append(sample)
        except (tarfile.TarError, OSError, UnicodeError) as exc:
            samples.append(
                {
                    "archive_path": str(archive),
                    "error": repr(exc),
                    "accession_number": "",
                    "cik": "",
                    "form_type": "",
                    "document_name": "",
                    "document_type": "",
                    "content_format": "",
                    "document_role": "",
                    "text_kind": "",
                    "source_chars": 0,
                    "upstream_text_chars": 0,
                    "packed_text_chars": 0,
                    "renderer": SEC_PACKED_TEXT_RENDERER_VERSION,
                    "quality_flags": "archive_error",
                    "original": "",
                    "upstream_text": "",
                    "intermediate": "",
                    "packed_text": "",
                }
            )
    return samples[: int(args.sample_size)]


def sample_from_member(
    payload_config: dict[str, Any],
    archive: Path,
    archive_date: datetime.date,
    archive_date_text: str,
    member_name: str,
    raw: bytes,
    inserted_at: str,
) -> dict[str, Any] | None:
    filing = parse_filing(raw, member_name)
    if not filing.get("documents"):
        return None
    _filing_row, parent = build_missing_parent_row(payload_config, archive, archive_date, archive_date_text, member_name, raw, filing, inserted_at)
    preferred: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
    fallback: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]] = []
    for document in filing["documents"]:
        doc_row, text_source_row, text_row, _skip_row, _sample_row = build_rows(payload_config, archive, archive_date_text, member_name, parent, document, inserted_at)
        if not text_source_row:
            continue
        item = (doc_row, text_source_row, text_row)
        if doc_row.get("document_role") in {"primary_document", "press_release_exhibit", "material_exhibit", "proxy_document", "prospectus"}:
            preferred.append(item)
        else:
            fallback.append(item)
    if not preferred and not fallback:
        return None
    doc_row, text_source_row, text_row = (preferred or fallback)[0]
    source_text = str(text_source_row.get("source_text") or "")
    result = render_sec_packed_text(
        source_text,
        str(text_source_row.get("content_format") or ""),
        document_name=str(text_source_row.get("document_name") or ""),
        document_type=str(text_source_row.get("document_type") or ""),
        text_kind=str(text_source_row.get("text_kind") or ""),
    )
    return {
        "archive_path": str(archive),
        "archive_member": member_name,
        "accession_number": parent.accession_number,
        "cik": parent.cik,
        "form_type": parent.form_type,
        "document_name": text_source_row.get("document_name") or "",
        "document_type": text_source_row.get("document_type") or "",
        "content_format": text_source_row.get("content_format") or "",
        "document_role": doc_row.get("document_role") or "",
        "text_kind": text_source_row.get("text_kind") or "",
        "source_chars": len(source_text),
        "upstream_text_chars": len(str((text_row or {}).get("text") or "")),
        "packed_text_chars": len(result.packed_text),
        "block_count": result.block_count,
        "table_block_count": result.table_block_count,
        "duplicate_block_count": result.duplicate_block_count,
        "source_text_hash": result.source_text_hash,
        "packed_text_hash": result.packed_text_hash,
        "renderer": result.renderer_version,
        "quality_flags": ",".join(result.quality_flags),
        "original": source_text,
        "upstream_text": str((text_row or {}).get("text") or ""),
        "intermediate": result.intermediate_text,
        "packed_text": result.packed_text,
    }


def render_report(args: argparse.Namespace, samples: list[dict[str, Any]], report_path: Path) -> str:
    lines = [
        "# SEC Packed Text Renderer Audit",
        "",
        f"- Generated at UTC: `{datetime.now(UTC).isoformat(timespec='seconds').replace('+00:00', 'Z')}`",
        f"- Renderer: `{SEC_PACKED_TEXT_RENDERER_VERSION}`",
        f"- Archive root: `{args.archive_root_win}`",
        f"- Date range: `{args.start_date}` to `{args.end_date}` exclusive",
        f"- Requested samples: `{int(args.sample_size)}`",
        f"- Collected samples: `{len(samples)}`",
        f"- Excerpt chars per section: `{int(args.excerpt_chars)}`",
        f"- Report path: `{report_path}`",
        "",
        "## Summary",
        "",
        "| # | Accession | CIK | Form | Document | Format | Role | Source Chars | Upstream Chars | Packed Chars | Blocks | Table Blocks | Duplicates | Flags |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for index, sample in enumerate(samples, 1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    md_cell(sample.get("accession_number")),
                    md_cell(sample.get("cik")),
                    md_cell(sample.get("form_type")),
                    md_cell(sample.get("document_name")),
                    md_cell(sample.get("content_format")),
                    md_cell(sample.get("document_role")),
                    str(sample.get("source_chars", 0)),
                    str(sample.get("upstream_text_chars", 0)),
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
    title = f"## Sample {index}: {sample.get('accession_number') or 'error'}"
    lines = [
        title,
        "",
        f"- Archive: `{sample.get('archive_path', '')}`",
        f"- Member: `{sample.get('archive_member', '')}`",
        f"- CIK/Form: `{sample.get('cik', '')}` / `{sample.get('form_type', '')}`",
        f"- Document: `{sample.get('document_name', '')}` (`{sample.get('document_type', '')}`, `{sample.get('document_role', '')}`, `{sample.get('content_format', '')}`)",
        f"- Hashes: source `{sample.get('source_text_hash', 0)}`, packed `{sample.get('packed_text_hash', 0)}`",
        f"- Counts: source `{sample.get('source_chars', 0)}`, upstream readable `{sample.get('upstream_text_chars', 0)}`, packed `{sample.get('packed_text_chars', 0)}`, blocks `{sample.get('block_count', 0)}`, table blocks `{sample.get('table_block_count', 0)}`",
        "",
        "### Original Separated Source Payload",
        "",
        fenced_excerpt(sample.get("original", ""), excerpt_chars),
        "",
        "### Upstream Readable Extraction",
        "",
        fenced_excerpt(sample.get("upstream_text", ""), excerpt_chars),
        "",
        "### Renderer Intermediate Blocks",
        "",
        fenced_excerpt(sample.get("intermediate", ""), excerpt_chars),
        "",
        "### Packed Renderer Output",
        "",
        fenced_excerpt(sample.get("packed_text", ""), excerpt_chars),
        "",
    ]
    return lines


def fenced_excerpt(value: Any, excerpt_chars: int) -> str:
    text = str(value or "")
    suffix = "\n\n[truncated]" if len(text) > excerpt_chars else ""
    return "```text\n" + text[:excerpt_chars].replace("```", "'''") + suffix + "\n```"


def md_cell(value: Any) -> str:
    text = str(value or "")
    return text.replace("|", "\\|").replace("\n", " ")[:220]


def default_archive_root() -> Path:
    if DEFAULT_ARCHIVE_ROOT_WIN.exists():
        return DEFAULT_ARCHIVE_ROOT_WIN
    if WORKSTATION_ARCHIVE_ROOT.exists():
        return WORKSTATION_ARCHIVE_ROOT
    return DEFAULT_ARCHIVE_ROOT_WIN


if __name__ == "__main__":
    raise SystemExit(main())
