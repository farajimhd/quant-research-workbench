from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()),
    Path(__file__).resolve().parents[2],
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "research" / "market_references" / "massive"
MASSIVE_API_BASE = "https://api.massive.com"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Massive stock market reference tables.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--api-key-env", default="MASSIVE_API_KEY")
    parser.add_argument("--asset-class", default="stocks")
    parser.add_argument("--locale", default="us")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def with_query(url: str, params: dict[str, Any]) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in params.items():
        if value is not None:
            query[key] = str(value)
    return urlunparse(parsed._replace(query=urlencode(query)))


def request_json(url: str, *, api_key: str, timeout: float) -> dict[str, Any]:
    request_url = with_query(url, {"apiKey": api_key})
    request = Request(request_url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected JSON object from {redact_api_key(request_url)}")
    return data


def redact_api_key(url: str) -> str:
    parsed = urlparse(url)
    query = [(key, "***" if key.lower() == "apikey" else value) for key, value in parse_qsl(parsed.query)]
    return urlunparse(parsed._replace(query=urlencode(query)))


def collect_paginated(
    endpoint: str,
    *,
    params: dict[str, Any],
    api_key: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = with_query(f"{MASSIVE_API_BASE}{endpoint}", params)
    results: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    page_number = 0
    while url:
        page_number += 1
        started = time.perf_counter()
        data = request_json(url, api_key=api_key, timeout=timeout)
        page_results = data.get("results") or []
        if not isinstance(page_results, list):
            raise RuntimeError(f"Unexpected results payload from {redact_api_key(url)}")
        results.extend([row for row in page_results if isinstance(row, dict)])
        pages.append(
            {
                "page": page_number,
                "status": data.get("status"),
                "count": data.get("count"),
                "results": len(page_results),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        )
        print(
            f"FETCH page={page_number} endpoint={endpoint} rows={len(page_results)} total={len(results)} "
            f"elapsed={pages[-1]['elapsed_seconds']}s",
            flush=True,
        )
        next_url = data.get("next_url")
        url = str(next_url) if next_url else ""
    return results, pages


def bits_for_count(count: int, *, reserved: int = 1) -> int:
    return 0 if count + reserved <= 1 else math.ceil(math.log2(count + reserved))


def bits_for_max_id(rows: list[dict[str, Any]], field: str = "id") -> int | None:
    ids = [int(row[field]) for row in rows if row.get(field) is not None]
    if not ids:
        return None
    maximum = max(ids)
    return 0 if maximum <= 0 else math.ceil(math.log2(maximum + 1))


def table_payload(
    *,
    name: str,
    endpoint: str | None,
    params: dict[str, Any] | None,
    results: list[dict[str, Any]],
    pages: list[dict[str, Any]] | None = None,
    source_notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "provider": "massive",
        "name": name,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "params": params or {},
        "count": len(results),
        "pages": pages or [],
        "source_notes": source_notes or [],
        "results": results,
    }


def stock_tape_table() -> dict[str, Any]:
    results = [
        {
            "id": 1,
            "code": "NYSE",
            "name": "NYSE tape",
            "massive_doc_label": "NYSE",
            "description": "Tape value documented by Massive stock quote/trade websocket docs.",
        },
        {
            "id": 2,
            "code": "AMEX",
            "name": "AMEX tape",
            "massive_doc_label": "AMEX",
            "description": "Tape value documented by Massive stock quote/trade websocket docs.",
        },
        {
            "id": 3,
            "code": "NASDAQ",
            "name": "Nasdaq tape",
            "massive_doc_label": "Nasdaq",
            "description": "Tape value documented by Massive stock quote/trade websocket docs.",
        },
    ]
    return table_payload(
        name="stock_tapes",
        endpoint=None,
        params=None,
        results=results,
        source_notes=[
            "Massive websocket stock trades and quotes docs list z: tape as 1 = NYSE, 2 = AMEX, 3 = Nasdaq.",
        ],
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"WROTE {path}", flush=True)


def condition_breakdown(conditions: list[dict[str, Any]]) -> dict[str, Any]:
    by_data_type: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in conditions:
        for data_type in row.get("data_types") or []:
            by_data_type[str(data_type)] = by_data_type.get(str(data_type), 0) + 1
        condition_type = row.get("type")
        if condition_type is not None:
            by_type[str(condition_type)] = by_type.get(str(condition_type), 0) + 1
    return {"by_data_type": dict(sorted(by_data_type.items())), "by_type": dict(sorted(by_type.items()))}


def make_summary(
    *,
    exchanges: list[dict[str, Any]],
    conditions: list[dict[str, Any]],
    tapes: list[dict[str, Any]],
) -> dict[str, Any]:
    condition_counts = condition_breakdown(conditions)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "encoding_notes": {
            "raw_id_bits": "Bits needed if preserving Massive numeric IDs directly.",
            "dense_id_bits": "Bits needed if remapping table rows to dense IDs with 0 reserved for missing/unknown.",
            "recommended_model_input": "Use dense integer IDs for categorical embeddings; keep these tables to map back to Massive IDs.",
        },
        "tables": {
            "stock_exchanges": {
                "rows": len(exchanges),
                "max_raw_id": max((int(row["id"]) for row in exchanges if row.get("id") is not None), default=None),
                "raw_id_bits": bits_for_max_id(exchanges),
                "dense_id_bits_with_unknown": bits_for_count(len(exchanges), reserved=1),
            },
            "stock_conditions": {
                "rows": len(conditions),
                "max_raw_id": max((int(row["id"]) for row in conditions if row.get("id") is not None), default=None),
                "raw_id_bits": bits_for_max_id(conditions),
                "dense_id_bits_with_unknown": bits_for_count(len(conditions), reserved=1),
                **condition_counts,
            },
            "stock_tapes": {
                "rows": len(tapes),
                "max_raw_id": max((int(row["id"]) for row in tapes if row.get("id") is not None), default=None),
                "raw_id_bits": bits_for_max_id(tapes),
                "dense_id_bits_with_unknown": bits_for_count(len(tapes), reserved=1),
            },
        },
    }


def main() -> None:
    args = parse_args()
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise RuntimeError(f"{args.api_key_env} is not set in environment or {REPO_ROOT / '.env'}")

    print(f"START output_dir={args.output_dir} asset_class={args.asset_class} locale={args.locale}", flush=True)

    exchanges, exchange_pages = collect_paginated(
        "/v3/reference/exchanges",
        params={"asset_class": args.asset_class, "locale": args.locale},
        api_key=api_key,
        timeout=args.timeout,
    )
    conditions, condition_pages = collect_paginated(
        "/v3/reference/conditions",
        params={"asset_class": args.asset_class, "limit": args.limit},
        api_key=api_key,
        timeout=args.timeout,
    )
    tapes_payload = stock_tape_table()

    exchanges_payload = table_payload(
        name="stock_exchanges",
        endpoint="/v3/reference/exchanges",
        params={"asset_class": args.asset_class, "locale": args.locale},
        results=exchanges,
        pages=exchange_pages,
    )
    conditions_payload = table_payload(
        name="stock_conditions",
        endpoint="/v3/reference/conditions",
        params={"asset_class": args.asset_class, "limit": args.limit},
        results=conditions,
        pages=condition_pages,
    )
    summary = make_summary(exchanges=exchanges, conditions=conditions, tapes=tapes_payload["results"])

    write_json(args.output_dir / "stock_exchanges.json", exchanges_payload)
    write_json(args.output_dir / "stock_conditions.json", conditions_payload)
    write_json(args.output_dir / "stock_tapes.json", tapes_payload)
    write_json(args.output_dir / "reference_summary.json", summary)
    print(json.dumps(summary["tables"], indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR {exc}", file=sys.stderr, flush=True)
        raise
