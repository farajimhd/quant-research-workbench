from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True, slots=True)
class HttpResult:
    ok: bool
    status_code: int
    payload: Any
    text: str
    error: str = ""


class IbkrClientPortalClient:
    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._ssl_context = ssl._create_unverified_context()

    @property
    def root_url(self) -> str:
        parts = urlsplit(self.base_url)
        return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))

    def is_gateway_reachable(self) -> bool:
        result = self.request_url(self.root_url, method="GET")
        return result.status_code > 0

    def auth_status(self) -> HttpResult:
        return self.request_api("/iserver/auth/status", method="POST", payload={})

    def reauthenticate(self) -> HttpResult:
        return self.request_api("/iserver/auth/ssodh/init", method="POST", payload={})

    def tickle(self) -> HttpResult:
        return self.request_api("/tickle", method="POST", payload={})

    def accounts(self) -> HttpResult:
        return self.request_api("/iserver/accounts", method="GET")

    def request_api(self, path: str, *, method: str, payload: dict[str, Any] | None = None) -> HttpResult:
        return self.request_url(self.base_url + path, method=method, payload=payload)

    def request_url(self, url: str, *, method: str, payload: dict[str, Any] | None = None) -> HttpResult:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json", "User-Agent": "quant-ibkr-gateway-supervisor/1.0"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, method=method, headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds, context=self._ssl_context) as response:  # noqa: S310
                text = response.read().decode("utf-8", errors="replace")
                return HttpResult(True, int(response.status), parse_json(text), text)
        except error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            return HttpResult(False, int(exc.code), parse_json(text), text, error=f"HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            return HttpResult(False, 0, {}, "", error=f"{type(exc).__name__}: {exc}")


def parse_json(text: str) -> Any:
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text[:500]}


def is_authenticated(status_payload: Any) -> bool:
    if not isinstance(status_payload, dict):
        return False
    return bool(status_payload.get("authenticated") or (status_payload.get("connected") and status_payload.get("competing") is False))


def can_reauthenticate(status_payload: Any) -> bool:
    if not isinstance(status_payload, dict):
        return False
    return bool(status_payload.get("connected") and not status_payload.get("authenticated"))


def account_ids(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        raw = payload.get("accounts") or payload.get("selectedAccount") or payload.get("accountIds") or []
    else:
        raw = payload
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for item in raw:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            value = item.get("accountId") or item.get("id") or item.get("account")
            if value:
                ids.append(str(value))
    return ids
