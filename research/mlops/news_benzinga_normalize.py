from __future__ import annotations

import hashlib
import html
import json
import mimetypes
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib import error, parse, request


BENZINGA_PROVIDER = "benzinga"
BENZINGA_NORMALIZER_VERSION = "benzinga-normalizer-v1"
DEFAULT_TEXT_LIMIT_CHARS = 24_000
DEFAULT_EXTRACTION_MIN_BODY_CHARS = 300
PDF_URL_RE = re.compile(r"https?://[^\s\"'<>]+?\.pdf(?:[?#][^\s\"'<>]*)?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class NewsExtractionOptions:
    fetch_external: bool = True
    extract_pdfs: bool = True
    external_min_body_chars: int = DEFAULT_EXTRACTION_MIN_BODY_CHARS
    request_timeout_seconds: float = 8.0
    max_pdf_bytes: int = 12_000_000
    text_limit_chars: int = DEFAULT_TEXT_LIMIT_CHARS


class HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        if tag_l in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag_l in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")
        if tag_l == "a":
            for key, value in attrs:
                if key.lower() == "href" and value:
                    self.links.append(html.unescape(value.strip()))

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag_l in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def normalize_benzinga_payload(
    payload: dict[str, Any],
    *,
    raw_artifact_path: str = "",
    raw_payload_hash: str = "",
    downloaded_at_utc: datetime | None = None,
    artifact_root: Path | None = None,
    options: NewsExtractionOptions | None = None,
) -> dict[str, Any]:
    options = options or NewsExtractionOptions()
    downloaded_at = downloaded_at_utc or datetime.now(UTC)
    provider_article_id = provider_id(payload)
    if not provider_article_id:
        raise ValueError("missing benzinga provider id")
    title = normalize_text(str(payload.get("title") or ""))
    if not title:
        raise ValueError("missing benzinga title")

    published_raw = str(payload.get("published") or "")
    published_at = parse_provider_datetime(published_raw)
    last_updated_raw = str(payload.get("last_updated") or "")
    last_updated_at = parse_provider_datetime(last_updated_raw) if last_updated_raw else None

    body_html = str(payload.get("body") or "")
    body_text, body_links = html_to_text_and_links(body_html)
    teaser = normalize_text(str(payload.get("teaser") or ""))
    article_url = str(payload.get("url") or "").strip()
    links = unique_strings([article_url, *body_links, *extract_pdf_urls(body_html), *extract_pdf_urls(json.dumps(payload, default=str))])
    pdf_urls = [url for url in links if looks_like_pdf_url(url)]

    external_text = ""
    external_status = "not_needed"
    external_error = ""
    if options.fetch_external and should_fetch_external(body_text, article_url, options):
        try:
            if looks_like_pdf_url(article_url):
                if article_url not in pdf_urls:
                    pdf_urls.append(article_url)
                external_status = "deferred_to_pdf"
            else:
                fetched = fetch_url_text(article_url, timeout_seconds=options.request_timeout_seconds)
                fetched_text, fetched_links = html_to_text_and_links(fetched)
                external_text = fetched_text
                external_status = "fetched" if fetched_text else "empty"
                for url in [*fetched_links, *extract_pdf_urls(fetched)]:
                    if looks_like_pdf_url(url) and url not in pdf_urls:
                        pdf_urls.append(url)
        except Exception as exc:  # noqa: BLE001
            external_status = "failed"
            external_error = repr(exc)

    pdf_texts: list[str] = []
    pdf_artifact_paths: list[str] = []
    pdf_status = "not_needed"
    pdf_error = ""
    if options.extract_pdfs and pdf_urls:
        pdf_status = "started"
        for url in pdf_urls[:4]:
            try:
                pdf_bytes = fetch_url_bytes(
                    url,
                    timeout_seconds=options.request_timeout_seconds,
                    max_bytes=options.max_pdf_bytes,
                )
                pdf_path = write_pdf_artifact(artifact_root, published_at, provider_article_id, url, pdf_bytes)
                pdf_artifact_paths.append(str(pdf_path) if pdf_path else "")
                text = extract_pdf_text(pdf_bytes)
                if text:
                    pdf_texts.append(text)
            except Exception as exc:  # noqa: BLE001
                pdf_error = repr(exc)
        pdf_status = "extracted" if pdf_texts else ("failed" if pdf_error else "empty")

    full_text_parts = [title, teaser, body_text, external_text, *pdf_texts]
    normalized_full_text = truncate_text(normalize_text(" ".join(part for part in full_text_parts if part)), options.text_limit_chars)
    body_text = truncate_text(body_text, options.text_limit_chars)
    external_text = truncate_text(external_text, options.text_limit_chars)
    pdf_text = truncate_text(normalize_text(" ".join(pdf_texts)), options.text_limit_chars)
    raw_hash = raw_payload_hash or stable_hash(json.dumps(payload, sort_keys=True, default=str))
    canonical_news_id = stable_hash("|".join([BENZINGA_PROVIDER, provider_article_id, published_raw, title]))

    content_quality_flags = content_flags(body_text, external_text, pdf_text, pdf_urls, external_status, pdf_status)
    return {
        "provider": BENZINGA_PROVIDER,
        "provider_article_id": provider_article_id,
        "canonical_news_id": canonical_news_id,
        "published_date": published_at.date().isoformat(),
        "published_at_utc": to_clickhouse_dt64(published_at),
        "published_raw": published_raw,
        "last_updated_at_utc": to_clickhouse_dt64(last_updated_at) if last_updated_at else None,
        "last_updated_raw": last_updated_raw,
        "downloaded_at_utc": to_clickhouse_dt64(downloaded_at),
        "provider_delay_ns": datetime_delay_ns(downloaded_at, published_at),
        "title": title,
        "normalized_title": normalize_title(title),
        "teaser": teaser,
        "body_text": body_text,
        "external_text": external_text,
        "pdf_text": pdf_text,
        "normalized_full_text": normalized_full_text,
        "text_hash": stable_hash(normalized_full_text),
        "article_url": article_url,
        "url_domain": url_domain(article_url),
        "author": normalize_text(str(payload.get("author") or "")),
        "tickers": normalize_string_array(payload.get("tickers"), upper=True),
        "channels": normalize_string_array(payload.get("channels")),
        "provider_tags": normalize_string_array(payload.get("tags")),
        "image_urls": normalize_string_array(payload.get("images")),
        "links": links,
        "has_body": 1 if body_text else 0,
        "is_title_only": 1 if not body_text and not external_text and not pdf_text else 0,
        "has_external_text": 1 if external_text else 0,
        "has_pdf": 1 if pdf_urls else 0,
        "pdf_urls": pdf_urls,
        "pdf_artifact_paths": [path for path in pdf_artifact_paths if path],
        "content_quality_flags": content_quality_flags,
        "external_fetch_status": external_status,
        "external_fetch_error": external_error,
        "pdf_extract_status": pdf_status,
        "pdf_extract_error": pdf_error,
        "raw_artifact_path": raw_artifact_path,
        "raw_payload_hash": raw_hash,
        "normalizer_version": BENZINGA_NORMALIZER_VERSION,
    }


def provider_id(payload: dict[str, Any]) -> str:
    value = payload.get("benzinga_id", payload.get("id", ""))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or "").strip()


def parse_provider_datetime(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("missing provider datetime")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_clickhouse_dt64(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def datetime_delay_ns(later: datetime, earlier: datetime) -> int:
    return int((later.astimezone(UTC) - earlier.astimezone(UTC)).total_seconds() * 1_000_000_000)


def html_to_text_and_links(value: str) -> tuple[str, list[str]]:
    if not value:
        return "", []
    parser = HtmlTextExtractor()
    parser.feed(value)
    return normalize_text(html.unescape(" ".join(parser.parts))), unique_strings(parser.links)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_title(value: str) -> str:
    return normalize_text(value).casefold()


def truncate_text(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit].rstrip()


def normalize_string_array(value: Any, *, upper: bool = False) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    output: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = normalize_text(str(item or ""))
        if upper:
            text = text.upper()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def unique_strings(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = html.unescape(str(value or "")).strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output


def extract_pdf_urls(value: str) -> list[str]:
    return unique_strings([match.group(0).rstrip(").,;") for match in PDF_URL_RE.finditer(value or "")])


def looks_like_pdf_url(value: str) -> bool:
    return ".pdf" in value.lower()


def should_fetch_external(body_text: str, article_url: str, options: NewsExtractionOptions) -> bool:
    return bool(article_url and len(body_text) < options.external_min_body_chars)


def fetch_url_text(url: str, *, timeout_seconds: float) -> str:
    with request.urlopen(build_request(url), timeout=timeout_seconds) as response:  # noqa: S310
        return response.read().decode("utf-8", errors="replace")


def fetch_url_bytes(url: str, *, timeout_seconds: float, max_bytes: int) -> bytes:
    with request.urlopen(build_request(url), timeout=timeout_seconds) as response:  # noqa: S310
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("pdf_too_large")
    return data


def build_request(url: str) -> request.Request:
    return request.Request(
        url,
        headers={
            "User-Agent": "quant-research-workbench-news-normalizer/1.0",
            "Accept": "text/html,application/pdf,application/xhtml+xml,*/*;q=0.8",
        },
    )


def write_pdf_artifact(
    artifact_root: Path | None,
    published_at: datetime,
    provider_article_id: str,
    url: str,
    pdf_bytes: bytes,
) -> Path | None:
    if artifact_root is None:
        return None
    folder = artifact_root / "pdfs" / published_at.strftime("%Y") / published_at.strftime("%m") / published_at.strftime("%d") / safe_filename(provider_article_id)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = Path(parse.urlparse(url).path).suffix or ".pdf"
    name = safe_filename(Path(parse.urlparse(url).path).stem or stable_hash(url)) + suffix
    path = folder / name
    path.write_bytes(pdf_bytes)
    return path


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pymupdf_not_available") from exc
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as handle:
        handle.write(pdf_bytes)
        handle.flush()
        doc = pymupdf.open(handle.name)
        try:
            return normalize_text(" ".join(page.get_text("text") for page in doc))
        finally:
            doc.close()


def content_flags(
    body_text: str,
    external_text: str,
    pdf_text: str,
    pdf_urls: list[str],
    external_status: str,
    pdf_status: str,
) -> list[str]:
    flags: list[str] = []
    if not body_text and not external_text and not pdf_text:
        flags.append("title_only")
    if body_text and len(body_text) < DEFAULT_EXTRACTION_MIN_BODY_CHARS:
        flags.append("short_body")
    if external_text:
        flags.append("external_text")
    if pdf_urls:
        flags.append("pdf_link")
    if pdf_text:
        flags.append("pdf_text")
    if external_status == "failed":
        flags.append("external_fetch_failed")
    if pdf_status == "failed":
        flags.append("pdf_extract_failed")
    return flags


def url_domain(url: str) -> str:
    if not url:
        return ""
    return parse.urlparse(url).netloc.lower()


def stable_hash(value: str) -> str:
    return hashlib.blake2b(value.encode("utf-8", errors="ignore"), digest_size=16).hexdigest()


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())[:120].strip("._")
    return cleaned or "artifact"


def artifact_path_for_payload(root: Path, payload: dict[str, Any], published_at: datetime | None = None) -> Path:
    provider_article_id = provider_id(payload) or stable_hash(json.dumps(payload, sort_keys=True, default=str))
    if published_at is None:
        published_raw = str(payload.get("published") or "")
        published_at = parse_provider_datetime(published_raw) if published_raw else datetime.now(UTC)
    folder = root / "raw" / published_at.strftime("%Y") / published_at.strftime("%m") / published_at.strftime("%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"benzinga_{safe_filename(provider_article_id)}.json"


def write_raw_payload(path: Path, payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return stable_hash(raw)


def content_type_from_url(url: str) -> str:
    return mimetypes.guess_type(url)[0] or ""
