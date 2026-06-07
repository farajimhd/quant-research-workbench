from __future__ import annotations

import hashlib
import html
import io
import json
import mimetypes
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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
    external_request_min_interval_seconds: float = 0.5
    benzinga_request_min_interval_seconds: float = 1.0
    sec_request_min_interval_seconds: float = 0.13
    external_max_retries: int = 3
    external_retry_base_seconds: float = 1.0
    external_rate_limit_root: str = ""
    default_user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36"
    sec_user_agent: str = ""


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
    diagnostics: list[dict[str, Any]] | None = None,
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
    if options.fetch_external and should_skip_external_fetch(article_url):
        external_status = "skipped_non_article_url"
        record_extraction_event(
            diagnostics,
            stage="external_fetch",
            status=external_status,
            provider_article_id=provider_article_id,
            published_raw=published_raw,
            url=article_url,
        )
    elif options.fetch_external and should_fetch_external(body_text, article_url, options):
        started_at = time.perf_counter()
        try:
            if looks_like_pdf_url(article_url):
                if article_url not in pdf_urls:
                    pdf_urls.append(article_url)
                external_status = "deferred_to_pdf"
                record_extraction_event(
                    diagnostics,
                    stage="external_fetch",
                    status=external_status,
                    provider_article_id=provider_article_id,
                    published_raw=published_raw,
                    url=article_url,
                    elapsed_seconds=time.perf_counter() - started_at,
                )
            else:
                fetched = fetch_url_text(
                    article_url,
                    options=options,
                )
                fetched_text, fetched_links = html_to_text_and_links(fetched)
                external_text = fetched_text
                external_status = "fetched" if fetched_text else "empty"
                record_extraction_event(
                    diagnostics,
                    stage="external_fetch",
                    status=external_status,
                    provider_article_id=provider_article_id,
                    published_raw=published_raw,
                    url=article_url,
                    fetched_bytes=len(fetched.encode("utf-8", errors="ignore")),
                    extracted_text_chars=len(fetched_text),
                    elapsed_seconds=time.perf_counter() - started_at,
                )
                for url in [*fetched_links, *extract_pdf_urls(fetched)]:
                    if looks_like_pdf_url(url) and url not in pdf_urls:
                        pdf_urls.append(url)
        except Exception as exc:  # noqa: BLE001
            external_status = "failed"
            external_error = repr(exc)
            record_extraction_event(
                diagnostics,
                stage="external_fetch",
                status=external_status,
                provider_article_id=provider_article_id,
                published_raw=published_raw,
                url=article_url,
                exception=external_error,
                elapsed_seconds=time.perf_counter() - started_at,
            )

    pdf_texts: list[str] = []
    pdf_artifact_paths: list[str] = []
    pdf_status = "not_needed"
    pdf_error = ""
    if options.extract_pdfs and pdf_urls:
        pdf_status = "started"
        for url in pdf_urls[:4]:
            started_at = time.perf_counter()
            try:
                pdf_bytes = fetch_url_bytes(
                    url,
                    max_bytes=options.max_pdf_bytes,
                    options=options,
                )
                pdf_path = write_pdf_artifact(artifact_root, published_at, provider_article_id, url, pdf_bytes)
                pdf_artifact_paths.append(str(pdf_path) if pdf_path else "")
                text = extract_pdf_text(pdf_bytes)
                if text:
                    pdf_texts.append(text)
                record_extraction_event(
                    diagnostics,
                    stage="pdf_fetch_extract",
                    status="extracted" if text else "empty",
                    provider_article_id=provider_article_id,
                    published_raw=published_raw,
                    url=url,
                    fetched_bytes=len(pdf_bytes),
                    extracted_text_chars=len(text),
                    artifact_path=str(pdf_path or ""),
                    elapsed_seconds=time.perf_counter() - started_at,
                )
            except Exception as exc:  # noqa: BLE001
                pdf_error = repr(exc)
                record_extraction_event(
                    diagnostics,
                    stage="pdf_fetch_extract",
                    status="failed",
                    provider_article_id=provider_article_id,
                    published_raw=published_raw,
                    url=url,
                    exception=pdf_error,
                    elapsed_seconds=time.perf_counter() - started_at,
                )
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
    return utc_value.strftime("%Y-%m-%d %H:%M:%S.%f")


def to_provider_rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def should_skip_external_fetch(article_url: str) -> bool:
    parsed = parse.urlparse(article_url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return host.endswith("benzinga.com") and path.startswith("/quote/")


def fetch_url_text(url: str, *, options: NewsExtractionOptions) -> str:
    data = fetch_url_with_retries(url, options=options)
    return data.decode("utf-8", errors="replace")


def fetch_url_bytes(url: str, *, max_bytes: int, options: NewsExtractionOptions) -> bytes:
    data = fetch_url_with_retries(url, options=options, max_bytes=max_bytes)
    if len(data) > max_bytes:
        raise ValueError("pdf_too_large")
    return data


def fetch_url_with_retries(url: str, *, options: NewsExtractionOptions, max_bytes: int | None = None) -> bytes:
    attempts = max(1, options.external_max_retries + 1)
    last_exception: Exception | None = None
    for attempt in range(1, attempts + 1):
        apply_host_rate_limit(url, options=options)
        try:
            with request.urlopen(build_request(url, options=options), timeout=options.request_timeout_seconds) as response:  # noqa: S310
                if max_bytes is None:
                    return response.read()
                return response.read(max_bytes + 1)
        except error.HTTPError as exc:
            last_exception = exc
            if exc.code not in {408, 425, 429, 500, 502, 503, 504} or attempt >= attempts:
                raise
            time.sleep(retry_sleep_seconds(exc, attempt, options))
        except (TimeoutError, error.URLError) as exc:
            last_exception = exc
            if attempt >= attempts:
                raise
            time.sleep(retry_sleep_seconds(exc, attempt, options))
    if last_exception is not None:
        raise last_exception
    raise RuntimeError("external_fetch_failed_without_exception")


def build_request(url: str, *, options: NewsExtractionOptions) -> request.Request:
    user_agent = user_agent_for_url(url, options)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/pdf,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if is_benzinga_host(url):
        headers["Referer"] = "https://www.benzinga.com/"
    return request.Request(url, headers=headers)


def user_agent_for_url(url: str, options: NewsExtractionOptions) -> str:
    if is_sec_host(url) and options.sec_user_agent:
        return options.sec_user_agent
    return options.default_user_agent


def retry_sleep_seconds(exc: Exception, attempt: int, options: NewsExtractionOptions) -> float:
    retry_after = ""
    if isinstance(exc, error.HTTPError):
        retry_after = exc.headers.get("Retry-After", "")
    parsed_retry_after = parse_retry_after_seconds(retry_after)
    if parsed_retry_after is not None:
        return min(300.0, parsed_retry_after)
    base = max(0.0, options.external_retry_base_seconds)
    return min(300.0, base * (2 ** (attempt - 1)))


def parse_retry_after_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0.0, (parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds())


def apply_host_rate_limit(url: str, *, options: NewsExtractionOptions) -> None:
    min_interval_seconds = request_min_interval_seconds(url, options)
    rate_limit_root = options.external_rate_limit_root
    if min_interval_seconds <= 0 or not rate_limit_root:
        return
    host = parse.urlparse(url).netloc.lower() or "unknown"
    folder = Path(rate_limit_root)
    folder.mkdir(parents=True, exist_ok=True)
    safe_host = safe_filename(host)
    lock_path = folder / f"{safe_host}.lock"
    state_path = folder / f"{safe_host}.last_request"
    stale_seconds = max(30.0, min_interval_seconds * 20)
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > stale_seconds:
                    lock_path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            time.sleep(0.05)
    try:
        last_request = 0.0
        try:
            last_request = float(state_path.read_text(encoding="utf-8").strip() or "0")
        except (FileNotFoundError, ValueError):
            last_request = 0.0
        wait_seconds = last_request + min_interval_seconds - time.time()
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        state_path.write_text(f"{time.time():.6f}", encoding="utf-8")
    finally:
        os.close(fd)
        lock_path.unlink(missing_ok=True)


def request_min_interval_seconds(url: str, options: NewsExtractionOptions) -> float:
    if is_sec_host(url):
        return max(0.0, options.sec_request_min_interval_seconds)
    if is_benzinga_host(url):
        return max(0.0, options.benzinga_request_min_interval_seconds)
    return max(0.0, options.external_request_min_interval_seconds)


def is_sec_host(url: str) -> bool:
    host = parse.urlparse(url).netloc.lower()
    return host == "sec.gov" or host.endswith(".sec.gov")


def is_benzinga_host(url: str) -> bool:
    host = parse.urlparse(url).netloc.lower()
    return host == "benzinga.com" or host.endswith(".benzinga.com")


def record_extraction_event(diagnostics: list[dict[str, Any]] | None, **payload: Any) -> None:
    if diagnostics is None:
        return
    diagnostics.append({key: value for key, value in payload.items() if value not in ("", None, [])})


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
            try:
                from pypdf import PdfReader
            except ImportError as pypdf_exc:
                raise RuntimeError("pdf_parser_not_available") from pypdf_exc
            reader = PdfReader(io.BytesIO(pdf_bytes))
            return normalize_text(" ".join(page.extract_text() or "" for page in reader.pages))
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
