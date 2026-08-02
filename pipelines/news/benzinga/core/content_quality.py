from __future__ import annotations

import re


TRANSPORT_ARTIFACT_FLAG = "transport_artifact"

_TRANSPORT_ARTIFACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "bot_challenge",
        re.compile(
            r"made us think you were a bot|verify you are human|"
            r"checking (?:if|whether) (?:the site connection|you are human)|captcha",
            re.IGNORECASE,
        ),
    ),
    (
        "javascript_gate",
        re.compile(
            r"(?:to (?:use|continue (?:to|using)) (?:this )?(?:site|website)|"
            r"please)\s*,?\s*(?:please\s+)?enable javascript",
            re.IGNORECASE,
        ),
    ),
    (
        "access_denied",
        re.compile(r"\baccess denied\b|\brequest blocked\b", re.IGNORECASE),
    ),
    (
        "loading_gate",
        re.compile(
            r"please stand by.{0,120}(?:getting everything ready|page is loading)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
)


def transport_artifact_reasons(text: str) -> tuple[str, ...]:
    """Return high-confidence fetch/transport artifacts, never article semantics."""
    value = str(text or "").strip()
    if not value:
        return ()
    return tuple(name for name, pattern in _TRANSPORT_ARTIFACT_PATTERNS if pattern.search(value))


def is_transport_artifact(text: str) -> bool:
    return bool(transport_artifact_reasons(text))


_PACKED_SOURCE_HEADER_RE = re.compile(
    r"^Source \[(?P<kind>[a-z_]+):(?P<ordinal>\d+)](?:\s+.*)?$",
    re.IGNORECASE,
)


def sanitize_packed_news_text(text: str) -> tuple[str, tuple[str, ...]]:
    """Remove rejected external source blocks from packed News text.

    Provider text is never discarded. New rendering rejects these artifacts
    before packing; this guard keeps historical rendered rows equivalent until
    their next versioned rebuild.
    """
    value = str(text or "")
    lines = value.splitlines()
    if not lines:
        return value, ()
    output: list[str] = []
    rejected: set[str] = set()
    index = 0
    while index < len(lines):
        match = _PACKED_SOURCE_HEADER_RE.match(lines[index].strip())
        if not match:
            output.append(lines[index])
            index += 1
            continue
        end = index + 1
        while end < len(lines) and not _PACKED_SOURCE_HEADER_RE.match(
            lines[end].strip()
        ):
            end += 1
        block = "\n".join(lines[index + 1:end])
        reasons = (
            transport_artifact_reasons(block)
            if match.group("kind").casefold() == "external"
            else ()
        )
        if reasons:
            rejected.update(reasons)
        else:
            output.extend(lines[index:end])
        index = end
    return "\n".join(output).strip(), tuple(sorted(rejected))
