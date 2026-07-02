from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


DEFAULT_BASE_URL = "https://localhost:5000/v1/api"


@dataclass(frozen=True, slots=True)
class IbkrGatewayConfig:
    account_key: str
    account_id: str
    username: str
    password_present: bool
    client_library_path: Path
    gateway_config_path: Path
    run_bat_path: Path
    base_url: str
    login_url: str
    tickle_seconds: float
    status_seconds: float
    startup_timeout_seconds: float
    request_timeout_seconds: float
    max_auth_failures: int
    max_reauth_attempts: int
    auto_login: bool
    max_login_attempts: int
    login_retry_seconds: float
    login_timeout_seconds: float
    login_headless: bool
    launch_gateway: bool
    notification_email_to: str
    notification_email_from: str
    notification_smtp_host: str
    notification_smtp_port: int
    notification_smtp_user: str
    notification_smtp_password_present: bool
    log_root: Path

    @classmethod
    def from_env(cls, *, account_key: str = "paper") -> "IbkrGatewayConfig":
        normalized_key = normalize_account_key(account_key)
        prefix = "IBKR_PAPER" if normalized_key == "paper" else f"IBKR_{normalized_key.upper().replace('-', '_')}"
        client_library_path = Path(env_string("IBKR_CLIENT_LIBRARY_LOCAL_PATH", "")).expanduser()
        gateway_config_path = Path(env_string("IBKR_GATEWAY_CONFIG_PATH", str(client_library_path / "root" / "conf.yaml"))).expanduser()
        run_bat_path = Path(env_string("IBKR_GATEWAY_RUN_BAT_PATH", str(client_library_path / "bin" / "run.bat"))).expanduser()
        base_url = env_string("IBKR_CPAPI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        return cls(
            account_key=normalized_key,
            account_id=env_string(f"{prefix}_ACCOUNT_ID", ""),
            username=env_string(f"{prefix}_USER_NAME", ""),
            password_present=bool(env_string(f"{prefix}_PASSWORD", "")),
            client_library_path=client_library_path,
            gateway_config_path=gateway_config_path,
            run_bat_path=run_bat_path,
            base_url=base_url,
            login_url=env_string("IBKR_GATEWAY_LOGIN_URL", login_url_from_base_url(base_url)),
            tickle_seconds=env_float("IBKR_GATEWAY_TICKLE_SECONDS", 60.0),
            status_seconds=env_float("IBKR_GATEWAY_STATUS_SECONDS", 15.0),
            startup_timeout_seconds=env_float("IBKR_GATEWAY_STARTUP_TIMEOUT_SECONDS", 60.0),
            request_timeout_seconds=env_float("IBKR_GATEWAY_REQUEST_TIMEOUT_SECONDS", 8.0),
            max_auth_failures=max(1, env_int("IBKR_GATEWAY_MAX_AUTH_FAILURES", 3)),
            max_reauth_attempts=max(1, env_int("IBKR_GATEWAY_MAX_REAUTH_ATTEMPTS", 3)),
            auto_login=env_bool("IBKR_GATEWAY_AUTO_LOGIN", True),
            max_login_attempts=max(1, env_int("IBKR_GATEWAY_MAX_LOGIN_ATTEMPTS", 3)),
            login_retry_seconds=env_float("IBKR_GATEWAY_LOGIN_RETRY_SECONDS", 60.0),
            login_timeout_seconds=env_float("IBKR_GATEWAY_LOGIN_TIMEOUT_SECONDS", 180.0),
            login_headless=env_bool("IBKR_GATEWAY_LOGIN_HEADLESS", False),
            launch_gateway=env_bool("IBKR_GATEWAY_LAUNCH", True),
            notification_email_to=env_string("IBKR_GATEWAY_ALERT_EMAIL_TO", env_string("ALERT_EMAIL_TO", "")),
            notification_email_from=env_string("IBKR_GATEWAY_ALERT_EMAIL_FROM", env_string("ALERT_EMAIL_FROM", "")),
            notification_smtp_host=env_string("IBKR_GATEWAY_ALERT_SMTP_HOST", env_string("ALERT_SMTP_HOST", "")),
            notification_smtp_port=env_int("IBKR_GATEWAY_ALERT_SMTP_PORT", env_int("ALERT_SMTP_PORT", 587)),
            notification_smtp_user=env_string("IBKR_GATEWAY_ALERT_SMTP_USER", env_string("ALERT_SMTP_USER", "")),
            notification_smtp_password_present=bool(env_string("IBKR_GATEWAY_ALERT_SMTP_PASSWORD", env_string("ALERT_SMTP_PASSWORD", ""))),
            log_root=Path(env_string("IBKR_GATEWAY_LOG_ROOT", "tmp/ibkr_gateway_supervisor")).expanduser(),
        )

    def public_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["account_id"] = mask_account_id(self.account_id)
        payload["username"] = "present" if self.username else "missing"
        payload["password_present"] = self.password_present
        payload["notification_smtp_password_present"] = self.notification_smtp_password_present
        for key in ("client_library_path", "gateway_config_path", "run_bat_path", "log_root"):
            payload[key] = str(payload[key])
        return payload


def normalize_account_key(value: str) -> str:
    text = (value or "").strip().lower().replace("_", "-")
    return text or "paper"


def login_url_from_base_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    path = parts.path
    if path.endswith("/v1/api"):
        path = path[: -len("/v1/api")]
    elif "/v1/api" in path:
        path = path.split("/v1/api", 1)[0]
    if not path:
        path = "/"
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/") + "/", "", ""))


def env_string(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    text = os.environ.get(name, "").strip()
    if not text:
        return int(default)
    return int(text)


def env_float(name: str, default: float) -> float:
    text = os.environ.get(name, "").strip()
    if not text:
        return float(default)
    return float(text)


def env_bool(name: str, default: bool) -> bool:
    text = os.environ.get(name, "").strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "y", "on"}


def mask_account_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return ("*" * max(0, len(text) - 4)) + text[-4:]
