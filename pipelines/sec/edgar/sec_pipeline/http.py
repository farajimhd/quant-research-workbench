from __future__ import annotations

from dataclasses import dataclass
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
    def __init__(self, *, user_agent: str, rate_limiter: SecRateLimiter, timeout_seconds: float = 30.0) -> None:
        self.user_agent = user_agent
        self.rate_limiter = rate_limiter
        self.timeout_seconds = timeout_seconds

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
            raise SecHttpError(status=int(exc.code), url=url, body=body) from exc
