from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

import requests


RETRIABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
QUOTA_ERROR_CODES = {
    "billing_hard_limit_reached",
    "billing_not_active",
    "insufficient_quota",
    "usage_limit_reached",
}


class OpenAIAPIError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        error_type: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        super().__init__(str(self))

    def __str__(self) -> str:
        label = self.code or self.error_type or "api_error"
        return f"OpenAI HTTP {self.status_code} {label}: {self.message}"

    @property
    def is_quota_error(self) -> bool:
        lowered = f"{self.code} {self.error_type} {self.message}".lower()
        return any(code in lowered for code in QUOTA_ERROR_CODES)


def error_from_response(response: requests.Response) -> OpenAIAPIError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "")
    error_type = str(error.get("type") or "")
    message = str(error.get("message") or response.text[:1_000] or response.reason)
    retryable = response.status_code in RETRIABLE_STATUS_CODES and not any(
        item in f"{code} {error_type} {message}".lower() for item in QUOTA_ERROR_CODES
    )
    return OpenAIAPIError(response.status_code, code, error_type, message, retryable)


class OpenAIHTTPClient:
    """Small explicit client for the Files and Batch APIs used by this job."""

    def __init__(
        self,
        api_key: str,
        *,
        project_id: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 120,
        retries: int = 5,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})
        if project_id:
            self.session.headers.update({"OpenAI-Project": project_id})

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = self.base_url + path
        timeout = kwargs.pop("timeout", self.timeout_seconds)
        last_error: OpenAIAPIError | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if attempt >= self.retries:
                    raise RuntimeError(f"OpenAI request failed after {attempt + 1} attempts: {exc}") from exc
                time.sleep(min(2**attempt, 30))
                continue
            if response.ok:
                return response
            last_error = error_from_response(response)
            if not last_error.retryable or attempt >= self.retries:
                raise last_error
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(2**attempt, 30)
            time.sleep(max(0.25, delay))
        assert last_error is not None
        raise last_error

    def verify_auth(self) -> None:
        self._request("GET", "/models")

    def upload_batch_file(self, path: Path) -> dict[str, Any]:
        with path.open("rb") as handle:
            response = self._request(
                "POST",
                "/files",
                files={"file": (path.name, handle, "application/jsonl")},
                data={"purpose": "batch"},
                timeout=600,
            )
        return response.json()

    def create_embedding_batch(self, input_file_id: str, metadata: dict[str, str]) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/batches",
            json={
                "input_file_id": input_file_id,
                "endpoint": "/v1/embeddings",
                "completion_window": "24h",
                "metadata": metadata,
            },
        )
        return response.json()

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        return self._request("GET", f"/batches/{batch_id}").json()

    def list_batches(self, *, limit: int = 100) -> Iterable[dict[str, Any]]:
        after = ""
        while True:
            params: dict[str, Any] = {"limit": min(100, max(1, limit))}
            if after:
                params["after"] = after
            payload = self._request("GET", "/batches", params=params).json()
            rows = payload.get("data") or []
            for row in rows:
                yield row
            if not payload.get("has_more") or not rows:
                return
            after = str(rows[-1].get("id") or "")

    def download_file(self, file_id: str, destination: Path) -> None:
        response = self._request("GET", f"/files/{file_id}/content", timeout=600, stream=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        with temporary.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        temporary.replace(destination)

    def delete_file(self, file_id: str) -> None:
        if file_id:
            self._request("DELETE", f"/files/{file_id}")


def batch_error_summary(batch: dict[str, Any]) -> str:
    errors = batch.get("errors") or {}
    data = errors.get("data") if isinstance(errors, dict) else None
    if not isinstance(data, list):
        return ""
    parts: list[str] = []
    for item in data[:10]:
        if isinstance(item, dict):
            parts.append(f"{item.get('code') or 'error'}: {item.get('message') or ''}".strip())
    return " | ".join(parts)


def is_quota_message(value: str) -> bool:
    lowered = value.lower()
    return any(code in lowered for code in QUOTA_ERROR_CODES)
