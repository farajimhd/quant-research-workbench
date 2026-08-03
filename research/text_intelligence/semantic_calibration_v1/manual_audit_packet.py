from __future__ import annotations

import html
import json
import re
from pathlib import Path


SECTION_RE = re.compile(r"(?m)^## (?P<name>.+?)\r?$\n")
INCLUDED_SECTIONS = (
    "Original provider payload downloaded by News Gateway",
    "Complete News Gateway retained record",
    "Original news texts",
    "Audit summary",
    "Article-level labels",
    "Issuer-level labels",
    "Human evidence and rationale",
    "V9 deterministic rule trace",
)


def audit_path(article_root: Path, sample_id: str) -> Path:
    matches = sorted(article_root.glob(f"{sample_id}_*.md"))
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one audit for {sample_id}, found {len(matches)} under {article_root}"
        )
    return matches[0]


def render_manual_review_packet(path: Path) -> str:
    """Return exact review-critical sections without duplicate rendered copies."""
    text = path.read_text(encoding="utf-8")
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group("name").strip()] = text[match.start():end].strip()
    missing = [name for name in INCLUDED_SECTIONS if name not in sections]
    if missing:
        raise RuntimeError(f"Audit {path.name} lacks required sections: {missing}")
    title = text.splitlines()[0]
    body = "\n\n".join(sections[name] for name in INCLUDED_SECTIONS)
    # Markdown audits encode retained provider material as HTML so it renders
    # safely. Console review restores the exact characters for readability.
    return html.unescape(f"{title}\n\n{body}")


def render_compact_manual_review_packet(path: Path, *, include_trace: bool = True) -> str:
    """Return one exact source lane plus every field needed for manual review."""
    text = path.read_text(encoding="utf-8")
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group("name").strip()] = text[match.start():end].strip()
    missing = [name for name in INCLUDED_SECTIONS if name not in sections]
    if missing:
        raise RuntimeError(f"Audit {path.name} lacks required sections: {missing}")

    provider = sections["Original provider payload downloaded by News Gateway"]
    payload_match = re.search(r"<pre>(\{.*?\})</pre>", provider, flags=re.DOTALL)
    if payload_match:
        payload = json.loads(html.unescape(payload_match.group(1)))
        payload.pop("body", None)
        payload.pop("images", None)
        provider_compact = (
            "## Original provider metadata\n\n```json\n"
            + json.dumps(payload, indent=2, ensure_ascii=False)
            + "\n```"
        )
    else:
        provider_compact = (
            "## Original provider metadata\n\n"
            "Original gateway source authority is explicitly unavailable for this frozen item."
        )
    title = text.splitlines()[0]
    selected = [
        provider_compact,
        sections["Original news texts"],
        sections["Audit summary"],
        sections["Article-level labels"],
        sections["Issuer-level labels"],
        sections["Human evidence and rationale"],
    ]
    if include_trace:
        selected.append(re.sub(
            r"(?m)^- \*\*Point-in-time issuer facts:\*\*.*(?:\r?\n)?",
            "",
            sections["V9 deterministic rule trace"],
        ))
    return html.unescape(title + "\n\n" + "\n\n".join(selected))


def render_bounded_manual_review_packet(
    path: Path,
    *,
    source_chars: int = 1_600,
    include_trace: bool = True,
) -> str:
    """Return every comparison plus a clearly bounded source-text view.

    This is a navigation aid for large manual rounds.  Reviewers must open the
    complete packet for any truncated, multi-issuer, or ambiguous item before
    recording a judgment.
    """
    packet = render_compact_manual_review_packet(path, include_trace=include_trace)
    marker = "## Original news texts"
    next_marker = "## Audit summary"
    start = packet.index(marker)
    end = packet.index(next_marker, start)
    source = packet[start:end].rstrip()
    if len(source) > source_chars:
        head_size = max(400, (source_chars * 2) // 3)
        tail_size = max(200, source_chars - head_size)
        source = (
            source[:head_size]
            + "\n\n[... SOURCE TEXT TRUNCATED FOR NAVIGATION; OPEN FULL PACKET ...]\n\n"
            + source[-tail_size:]
        )
    return packet[:start] + source + "\n\n" + packet[end:]


def render_manual_review_digest(path: Path, *, source_chars: int = 700) -> str:
    """Return a dense first-pass digest without changing certification evidence.

    The digest deliberately includes the original provider metadata, bounded
    publication text, every aggregate comparison row, and the human rationale.
    It is only a navigation surface: reviewers still open the full audit when
    text is truncated or an issuer assignment is ambiguous.
    """
    packet = render_compact_manual_review_packet(path, include_trace=False)
    source_start = packet.index("## Original news texts")
    summary_start = packet.index("## Audit summary", source_start)
    evidence_start = packet.index("## Human evidence and rationale", summary_start)
    source = packet[source_start:summary_start].rstrip()
    if len(source) > source_chars:
        head_size = max(250, (source_chars * 2) // 3)
        tail_size = max(150, source_chars - head_size)
        source = (
            source[:head_size]
            + "\n[TRUNCATED: OPEN FULL AUDIT BEFORE FINAL JUDGMENT]\n"
            + source[-tail_size:]
        )

    header = packet[:source_start]
    comparisons = packet[summary_start:evidence_start]
    # Remove stable explanatory prose while preserving source identity and all
    # evaluator-generated table rows.
    comparisons = re.sub(
        r"\nThe result column below.*?difference\.\n",
        "\n",
        comparisons,
        flags=re.DOTALL,
    )
    evidence = packet[evidence_start:]
    return header + source + "\n\n" + comparisons + evidence


def render_manual_review_scan(path: Path, *, source_chars: int = 500) -> str:
    """Return a scan-dense view used to read every item in a large audit round."""
    raw_text = path.read_text(encoding="utf-8")
    packet = render_compact_manual_review_packet(path, include_trace=False)
    title = packet.splitlines()[0]
    metadata_match = re.search(
        r"## Original provider payload downloaded by News Gateway.*?<pre>(\{.*?\})</pre>",
        raw_text,
        flags=re.DOTALL,
    )
    metadata = (
        json.loads(html.unescape(metadata_match.group(1)))
        if metadata_match
        else {"author": "unavailable", "tickers": [], "channels": [], "tags": [], "published": ""}
    )
    source_start = packet.index("## Original news texts")
    summary_start = packet.index("## Audit summary", source_start)
    source = re.sub(r"<[^>]+>", " ", packet[source_start:summary_start])
    source = re.sub(r"\s+", " ", html.unescape(source)).strip()
    truncated = len(source) > source_chars
    source = source[:source_chars] + (" ... [OPEN FULL]" if truncated else "")

    article_start = packet.index("## Article-level labels", summary_start)
    issuer_start = packet.index("## Issuer-level labels", article_start)
    evidence_start = packet.index("## Human evidence and rationale", issuer_start)
    article_rows = [
        line for line in packet[article_start:issuer_start].splitlines()
        if line.startswith("|") and "Dimension" not in line and "---" not in line
    ]
    issuer_rows = [
        line for line in packet[issuer_start:evidence_start].splitlines()
        if line.startswith("|") and "Ticker" not in line and "---" not in line
    ]
    evidence = packet[evidence_start:].replace("## Human evidence and rationale", "").strip()
    compact_evidence = " ".join(line.strip(" -") for line in evidence.splitlines() if line.strip())
    compact_evidence = re.sub(r"\s+", " ", compact_evidence)

    compact_article: list[str] = []
    for row in article_rows:
        cells = [cell.strip().replace("**", "") for cell in row.strip("|").split("|")]
        if len(cells) >= 4:
            compact_article.append(f"{cells[0]}={cells[1]}/{cells[2]}:{cells[3]}")
    compact_issuer: list[str] = []
    for row in issuer_rows:
        cells = [cell.strip().replace("**", "").replace("<br>", ",") for cell in row.strip("|").split("|")]
        if len(cells) < 5:
            continue
        # Preserve every scored difference plus presence matches.  Omit stable
        # downstream matches so multi-issuer roundups remain manually readable.
        if "DIFF" in cells[4] or cells[1] == "Issuer unit present":
            compact_issuer.append(f"{cells[0]}:{cells[1]}={cells[2]}/{cells[3]}:{cells[4]}")

    return "\n".join((
        title,
        "META " + json.dumps({
            "author": metadata.get("author", ""),
            "tickers": metadata.get("tickers", []),
            "channels": metadata.get("channels", []),
            "tags": metadata.get("tags", []),
            "published": metadata.get("published", ""),
        }, ensure_ascii=False, separators=(",", ":")),
        "TEXT " + source,
        "ARTICLE " + " || ".join(compact_article),
        "ISSUER " + " || ".join(compact_issuer),
        "HUMAN " + compact_evidence,
    ))
