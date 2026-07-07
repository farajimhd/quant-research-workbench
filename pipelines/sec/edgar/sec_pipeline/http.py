from __future__ import annotations

import email.utils
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib import error, request

from pipelines.sec.edgar.sec_pipeline.rate_limit import SecRateLimiter


@dataclass(frozen=True, slots=True)
class SecHttpResponse:
    url: str
    status: int
    content_type: str
    body: bytes


class SecHttpError(RuntimeError):
    def __init__(self, *, status: int, url: str, body: bytes) -> None:
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"SEC HTTP {status} for {url}: {body[:500]!r}")


class SecHttpClient:
    def __init__(
        self,
        *,
        user_agent: str,
        rate_limiter: SecRateLimiter,
        timeout_seconds: float = 30.0,
        transient_error_cooldown_seconds: float = 60.0,
        rate_limit_cooldown_seconds: float = 300.0,
    ) -> None:
        self.user_agent = user_agent
        self.rate_limiter = rate_limiter
        self.timeout_seconds = timeout_seconds
        self.transient_error_cooldown_seconds = max(0.0, transient_error_cooldown_seconds)
        self.rate_limit_cooldown_seconds = max(0.0, rate_limit_cooldown_seconds)

    def get(self, url: str) -> SecHttpResponse:
        self.rate_limiter.wait()
        req = request.Request(url, method="GET")
        req.add_header("User-Agent", self.user_agent)
        req.add_header("Accept-Encoding", "identity")
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                return SecHttpResponse(
                    url=url,
                    status=int(response.status),
                    content_type=response.headers.get("Content-Type", ""),
                    body=response.read(),
                )
        except error.HTTPError as exc:
            body = exc.read()
            if exc.code in {403, 429}:
                retry_after = parse_retry_after(exc.headers.get("Retry-After"))
                self.rate_limiter.cooldown(
                    retry_after if retry_after is not None else self.rate_limit_cooldown_seconds,
                    reason=f"sec_http_{exc.code}",
                )
            elif 500 <= exc.code <= 599:
                self.rate_limiter.cooldown(self.transient_error_cooldown_seconds, reason=f"sec_http_{exc.code}")
            raise SecHttpError(status=int(exc.code), url=url, body=body) from exc
        except (TimeoutError, error.URLError, OSError):
            self.rate_limiter.cooldown(self.transient_error_cooldown_seconds, reason="sec_provider_transient")
            raise


def parse_retry_after(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return max(0.0, (parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds())
