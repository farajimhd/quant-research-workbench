from __future__ import annotations

import hmac
import os
import re
from dataclasses import dataclass
from typing import Mapping


MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_BROWSER_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class AuthorityDenied(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class ApplicationAuthority:
    user_id: str
    workspace_id: str
    environment: str
    mode: str
    account_key: str | None
    command: str
    authentication_mode: str

    def public_payload(self) -> dict[str, str | None]:
        return {
            "schema_version": "application-authority.v1",
            "user_id": self.user_id,
            "workspace_id": self.workspace_id,
            "environment": self.environment,
            "mode": self.mode,
            "account_key": self.account_key,
            "command": self.command,
            "authentication_mode": self.authentication_mode,
        }


@dataclass(frozen=True)
class AuthorityPolicy:
    authentication_mode: str
    user_id: str
    workspace_id: str
    environment: str
    allowed_modes: frozenset[str]
    allowed_accounts: frozenset[str]
    allowed_commands: frozenset[str]
    browser_origins: frozenset[str]
    proxy_token: str

    def public_payload(self) -> dict[str, object]:
        return {
            "schema_version": "authority-policy.v1",
            "authentication_mode": self.authentication_mode,
            "user_id": self.user_id if self.authentication_mode == "local" else None,
            "workspace_id": self.workspace_id if self.authentication_mode == "local" else None,
            "environment": self.environment,
            "allowed_modes": sorted(self.allowed_modes),
            "allowed_accounts": sorted(self.allowed_accounts),
            "allowed_commands": sorted(self.allowed_commands),
            "browser_origins": sorted(self.browser_origins),
            "proxy_token_configured": bool(self.proxy_token),
        }

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "AuthorityPolicy":
        source = os.environ if environ is None else environ
        authentication_mode = source.get("BACKEND_AUTHORITY_MODE", "local").strip().lower()
        if authentication_mode not in {"local", "proxy"}:
            raise ValueError("BACKEND_AUTHORITY_MODE must be local or proxy")
        proxy_token = source.get("BACKEND_AUTHORITY_PROXY_TOKEN", "").strip()
        if authentication_mode == "proxy" and not proxy_token:
            raise ValueError("BACKEND_AUTHORITY_PROXY_TOKEN is required in proxy mode")
        return cls(
            authentication_mode=authentication_mode,
            user_id=_required(source, "BACKEND_AUTHORITY_USER", "local-user"),
            workspace_id=_required(source, "BACKEND_AUTHORITY_WORKSPACE", "local"),
            environment=_required(source, "BACKEND_AUTHORITY_ENVIRONMENT", "local"),
            allowed_modes=_csv(source.get("BACKEND_AUTHORITY_ALLOWED_MODES", "system,research,replay,backtest,backtest_debug,paper,live")),
            allowed_accounts=_csv(source.get("BACKEND_AUTHORITY_ALLOWED_ACCOUNTS", "*")),
            allowed_commands=_csv(source.get("BACKEND_AUTHORITY_ALLOWED_COMMANDS", "*")),
            browser_origins=_csv(source.get("BACKEND_AUTHORITY_BROWSER_ORIGINS", ",".join(DEFAULT_BROWSER_ORIGINS))),
            proxy_token=proxy_token,
        )

    def authorize(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        client_host: str | None,
    ) -> ApplicationAuthority:
        normalized_method = method.upper()
        normalized_path = path.rstrip("/") or "/"
        if self.authentication_mode == "local":
            if (client_host or "").lower() not in LOCAL_CLIENTS:
                raise AuthorityDenied(
                    "local_authority_remote_client",
                    "Local authority accepts only loopback clients.",
                    status_code=401,
                )
            user_id = self.user_id
            workspace_id = self.workspace_id
        else:
            supplied = headers.get("x-qw-authority-token", "")
            if not hmac.compare_digest(supplied, self.proxy_token):
                raise AuthorityDenied(
                    "authority_authentication_failed",
                    "The trusted authority token is missing or invalid.",
                    status_code=401,
                )
            user_id = _header_required(headers, "x-qw-user")
            workspace_id = _header_required(headers, "x-qw-workspace")

        environment = headers.get("x-qw-environment", self.environment).strip().lower()
        mode = headers.get("x-qw-mode", infer_mode(normalized_path)).strip().lower()
        account_key = headers.get("x-qw-account", "").strip() or infer_account(normalized_path)
        command = classify_command(normalized_method, normalized_path)

        if normalized_method in MUTATING_METHODS or normalized_method == "WEBSOCKET":
            self._validate_browser_command(headers)
        if environment != self.environment:
            raise AuthorityDenied(
                "environment_authority_denied",
                f"Environment {environment!r} is outside this backend authority.",
            )
        _require_allowed("mode", mode, self.allowed_modes)
        if account_key:
            _require_allowed("account", account_key, self.allowed_accounts)
        _require_allowed("command", command, self.allowed_commands)
        return ApplicationAuthority(
            user_id=user_id,
            workspace_id=workspace_id,
            environment=environment,
            mode=mode,
            account_key=account_key,
            command=command,
            authentication_mode=self.authentication_mode,
        )

    def _validate_browser_command(self, headers: Mapping[str, str]) -> None:
        origin = headers.get("origin", "").rstrip("/")
        if origin and origin not in self.browser_origins:
            raise AuthorityDenied(
                "browser_origin_denied",
                f"Browser command origin {origin!r} is not allowed.",
            )
        fetch_site = headers.get("sec-fetch-site", "").strip().lower()
        if fetch_site == "cross-site":
            raise AuthorityDenied(
                "cross_site_command_denied",
                "Cross-site browser commands are not allowed.",
            )


def classify_command(method: str, path: str) -> str:
    if method not in MUTATING_METHODS:
        return "read"
    if path == "/api/trading/configuration/publish":
        return "configuration.publish"
    if path.startswith("/api/market-data/build"):
        return "market_data.build_control"
    if path.startswith("/api/real-live-trading/market-gateway"):
        return "market_gateway.control"
    if path.endswith("/trade-proposals"):
        return "trading.proposal"
    if "/commands" in path:
        return "trading.command"
    if path.startswith("/api/trading/"):
        return "trading.mutate"
    if path.startswith("/api/market-data/"):
        return "market_data.mutate"
    return f"http.{method.lower()}"


def infer_mode(path: str) -> str:
    if "/backtest_debug/" in path:
        return "backtest_debug"
    if "/backtest/" in path:
        return "backtest"
    if "/replay/" in path:
        return "replay"
    if path.startswith("/api/real-live-trading/"):
        return "live"
    if path.startswith("/api/research/"):
        return "research"
    return "system"


def infer_account(path: str) -> str | None:
    match = re.search(r"/portfolio-management/([^/]+)", path)
    return match.group(1) if match else None


def _csv(value: str) -> frozenset[str]:
    return frozenset(part.strip().lower() for part in value.split(",") if part.strip())


def _required(source: Mapping[str, str], key: str, default: str) -> str:
    value = source.get(key, default).strip()
    if not value:
        raise ValueError(f"{key} must not be empty")
    return value


def _header_required(headers: Mapping[str, str], key: str) -> str:
    value = headers.get(key, "").strip()
    if not value:
        raise AuthorityDenied(
            "authority_identity_missing",
            f"Trusted proxy header {key!r} is required.",
            status_code=401,
        )
    return value


def _require_allowed(kind: str, value: str, allowed: frozenset[str]) -> None:
    if "*" not in allowed and value.lower() not in allowed:
        raise AuthorityDenied(
            f"{kind}_authority_denied",
            f"{kind.title()} {value!r} is not allowed by this backend policy.",
        )
