from __future__ import annotations

import argparse
import json
import math
import os
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.mlops.env import discover_env_files, load_env_files
from research.news_labeling.openai_batch_v1.openai_api import OpenAIClient

from .prepare import default_runtime_root, ensure_manifest, utc_now
from .runner_common import (
    atomic_json,
    atomic_jsonl,
    chat_body,
    context_rows,
    parse_chat_response,
    result_row,
    sha256_file,
)


TERMINAL = {"completed", "failed", "expired", "cancelled"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit/reconcile the frozen 90-article Sol hypothesis benchmark."
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--prepare-workers", type=int, default=16)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--max-output-tokens", type=int, default=3000)
    parser.add_argument("--authorize-cost-usd", type=Decimal, default=Decimal("0"))
    parser.add_argument("--hard-max-cost-usd", type=Decimal, default=Decimal("10.00"))
    parser.add_argument("--runtime-root", type=Path, default=default_runtime_root())
    args = parser.parse_args()
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]), verbose=True)

    manifest = ensure_manifest(runtime_root=args.runtime_root, workers=args.prepare_workers)
    rows = context_rows(manifest)
    root = args.runtime_root / "sol"
    root.mkdir(parents=True, exist_ok=True)
    input_path = root / "input.jsonl"
    state_path = root / "state.json"
    results_path = root / "results.jsonl"
    build_input(input_path, rows, args.model, args.max_output_tokens)
    input_hash = sha256_file(input_path)
    conservative_input_tokens = math.ceil(input_path.stat().st_size / 3)
    conservative_output_tokens = len(rows) * args.max_output_tokens
    # Explicit Batch prices for gpt-5.6-sol: $2.50/M input and $15/M output.
    protected_cost = (
        Decimal(conservative_input_tokens) * Decimal("2.50")
        + Decimal(conservative_output_tokens) * Decimal("15.00")
    ) / Decimal(1_000_000)
    print(
        f"SOL BATCH | frozen=90 input={input_path} protected_cost=${protected_cost:.4f} "
        f"hard_max=${args.hard_max_cost_usd:.2f} "
        f"authorized=${args.authorize_cost_usd:.2f} execute={args.execute}",
        flush=True,
    )
    if protected_cost > args.hard_max_cost_usd:
        raise RuntimeError(
            f"Protected Batch cost ${protected_cost:.4f} exceeds hard maximum "
            f"${args.hard_max_cost_usd:.2f}"
        )
    if not args.execute:
        return 0
    if args.authorize_cost_usd < protected_cost:
        raise RuntimeError(
            f"Explicit authorization ${args.authorize_cost_usd:.2f} is below protected "
            f"Batch cost ${protected_cost:.4f}; no remote request was made"
        )
    if args.authorize_cost_usd > args.hard_max_cost_usd:
        raise RuntimeError(
            f"Authorization ${args.authorize_cost_usd:.2f} exceeds the hard maximum "
            f"${args.hard_max_cost_usd:.2f}"
        )

    client = OpenAIClient(
        os.environ.get("OPENAI_API_KEY", ""),
        project_id=os.environ.get("OPENAI_PROJECT_ID", ""),
    )
    state = read_state(state_path)
    if state and (
        state.get("input_sha256") != input_hash
        or state.get("model") != args.model
    ):
        raise RuntimeError("Existing Sol Batch state does not match this frozen input")
    if not state.get("input_file_id"):
        uploaded = client.upload_batch_file(input_path)
        state.update(
            {
                "contract": "news_trade_hypothesis_sol_batch_v2",
                "input_sha256": input_hash,
                "model": args.model,
                "input_file_id": str(uploaded["id"]),
                "status": "uploaded",
                "created_at_utc": utc_now(),
            }
        )
        atomic_json(state_path, state)
    if not state.get("batch_id"):
        batch = client.create_batch(
            str(state["input_file_id"]),
            {
                "experiment": "news_trade_hypothesis_v2",
                "population": "frozen_single_ticker_90",
                "input_hash": input_hash[:32],
            },
        )
        update_state_from_batch(state, batch)
        atomic_json(state_path, state)
    if args.no_wait:
        print(f"SOL SUBMITTED | batch={state['batch_id']} status={state['status']}")
        return 0

    while str(state.get("status")) not in TERMINAL:
        batch = client.retrieve_batch(str(state["batch_id"]))
        update_state_from_batch(state, batch)
        atomic_json(state_path, state)
        counts = state.get("request_counts") or {}
        print(
            f"SOL BATCH | status={state['status']} completed={counts.get('completed', 0)}/90 "
            f"failed={counts.get('failed', 0)}",
            flush=True,
        )
        if str(state.get("status")) not in TERMINAL:
            time.sleep(max(5, args.poll_seconds))
    collect(client, state, root, rows, results_path)
    results = [
        json.loads(line)
        for line in results_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    failures = sum(row.get("status") != "completed" for row in results)
    print(
        f"SOL COMPLETE | batch_status={state['status']} completed={90 - failures}/90 "
        f"failed={failures} output={results_path}"
    )
    return 1 if failures or state["status"] != "completed" else 0


def build_input(path: Path, rows: list[dict[str, Any]], model: str, max_tokens: int) -> None:
    requests = []
    for row in rows:
        body = chat_body(
            row,
            model=model,
            max_output_tokens=max_tokens,
            reasoning_effort="none",
        )
        requests.append(
            {
                "custom_id": str(row["canonical_news_id"]),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": body,
            }
        )
    atomic_jsonl(path, requests)


def update_state_from_batch(state: dict[str, Any], batch: dict[str, Any]) -> None:
    counts = batch.get("request_counts")
    state.update(
        {
            "batch_id": str(batch.get("id") or state.get("batch_id") or ""),
            "status": str(batch.get("status") or ""),
            "request_counts": counts if isinstance(counts, dict) else {},
            "output_file_id": str(batch.get("output_file_id") or ""),
            "error_file_id": str(batch.get("error_file_id") or ""),
            "updated_at_utc": utc_now(),
        }
    )


def collect(
    client: OpenAIClient,
    state: dict[str, Any],
    root: Path,
    sources: list[dict[str, Any]],
    results_path: Path,
) -> None:
    raw_output = root / "raw_output.jsonl"
    raw_error = root / "raw_error.jsonl"
    if state.get("output_file_id"):
        client.download_file(str(state["output_file_id"]), raw_output)
    if state.get("error_file_id"):
        client.download_file(str(state["error_file_id"]), raw_error)
    source_by_id = {str(row["canonical_news_id"]): row for row in sources}
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    if raw_output.exists():
        for row in (
            json.loads(line)
            for line in raw_output.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ):
            identifier = str(row.get("custom_id") or "")
            source = source_by_id.get(identifier)
            response = row.get("response") if isinstance(row.get("response"), dict) else {}
            try:
                if source is None:
                    raise ValueError("identity_not_in_frozen_population")
                if int(response.get("status_code") or 0) != 200:
                    raise ValueError(f"HTTP {response.get('status_code')}")
                prediction, usage = parse_chat_response(response["body"])
                result = result_row(
                    source,
                    model=str(state["model"]),
                    provider="openai_batch",
                    prediction=prediction,
                    usage=usage,
                )
            except Exception as exc:
                result = {
                    "canonical_news_id": identifier,
                    "model": state["model"],
                    "provider": "openai_batch",
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            seen.add(identifier)
    for identifier, source in source_by_id.items():
        if identifier not in seen:
            results.append(
                {
                    "canonical_news_id": identifier,
                    "ticker": source["ticker"],
                    "published_at_utc": source["published_at_utc"],
                    "model": state["model"],
                    "provider": "openai_batch",
                    "status": "failed",
                    "error": "missing_batch_response",
                }
            )
    results.sort(key=lambda row: (str(row.get("published_at_utc")), str(row.get("canonical_news_id"))))
    atomic_jsonl(results_path, results)


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
