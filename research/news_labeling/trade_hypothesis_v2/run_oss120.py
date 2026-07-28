from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib import error, request

from research.mlops.env import discover_env_files, load_env_files

from .prepare import default_runtime_root, ensure_manifest
from .runner_common import (
    atomic_jsonl,
    chat_body,
    context_rows,
    parse_chat_response,
    read_jsonl,
    result_row,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen 90-article hypothesis benchmark against local OSS-120B."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prepare-workers", type=int, default=16)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1/chat/completions")
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--max-output-tokens", type=int, default=3000)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    args = parser.parse_args()
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]), verbose=True)

    manifest = ensure_manifest(
        runtime_root=args.runtime_root,
        workers=args.prepare_workers,
    )
    rows = context_rows(manifest)
    output = args.runtime_root / "oss120" / "results.jsonl"
    completed = {
        str(row.get("canonical_news_id"))
        for row in read_jsonl(output)
        if row.get("status") == "completed"
    }
    pending = [row for row in rows if str(row["canonical_news_id"]) not in completed]
    print(
        f"OSS120 HYPOTHESIS | frozen=90 completed={len(completed)} pending={len(pending)} "
        f"workers={max(1, args.workers)} execute={args.execute}",
        flush=True,
    )
    if not args.execute:
        return 0
    check_server(args.endpoint, args.model)
    results = read_jsonl(output)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(run_one, row, args): row
            for row in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "canonical_news_id": source["canonical_news_id"],
                    "ticker": source["ticker"],
                    "published_at_utc": source["published_at_utc"],
                    "model": args.model,
                    "provider": "local_vllm",
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results = [
                row
                for row in results
                if str(row.get("canonical_news_id")) != str(source["canonical_news_id"])
            ]
            results.append(result)
            results.sort(key=lambda row: (str(row.get("published_at_utc")), str(row.get("canonical_news_id"))))
            atomic_jsonl(output, results)
            done = len(completed) + index
            elapsed = time.monotonic() - started
            eta = ((len(pending) - index) * elapsed / index) if index else 0.0
            print(
                f"OSS120 {done}/90 id={source['canonical_news_id']} "
                f"status={result['status']} elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
                flush=True,
            )
    failures = sum(row.get("status") != "completed" for row in results)
    print(f"OSS120 COMPLETE | completed={90 - failures}/90 failed={failures} output={output}")
    return 1 if failures else 0


def check_server(endpoint: str, model: str) -> None:
    models_url = endpoint.rsplit("/chat/completions", 1)[0] + "/models"
    with request.urlopen(models_url, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    available = {
        str(row.get("id"))
        for row in payload.get("data", [])
        if isinstance(row, dict) and row.get("id")
    }
    if available and model not in available:
        raise RuntimeError(f"Requested model {model!r} is not served; available={sorted(available)}")


def run_one(row: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    payload = chat_body(
        row,
        model=args.model,
        max_output_tokens=args.max_output_tokens,
        reasoning_effort="low",
    )
    # vLLM accepts max_tokens for the OpenAI-compatible Chat Completions endpoint.
    payload["max_tokens"] = payload.pop("max_completion_tokens")
    last_error: Exception | None = None
    started = time.monotonic()
    for attempt in range(1, 4):
        try:
            body = post_json(args.endpoint, payload, args.timeout_seconds)
            prediction, usage = parse_chat_response(body)
            return result_row(
                row,
                model=args.model,
                provider="local_vllm",
                prediction=prediction,
                usage=usage,
                elapsed_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            last_error = exc
            if isinstance(exc, HttpError) and not exc.retryable:
                break
            if attempt < 3:
                time.sleep(min(2 ** (attempt - 1), 8))
    raise RuntimeError(f"request failed after 3 attempts: {last_error}") from last_error


class HttpError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        self.retryable = status in {408, 409, 425, 429} or status >= 500
        super().__init__(f"HTTP {status}: {body[:1000]}")


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = request.Request(url, data=encoded, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise HttpError(exc.code, exc.read().decode("utf-8", errors="replace")) from exc


if __name__ == "__main__":
    raise SystemExit(main())
