from __future__ import annotations

import argparse
import html
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


REPO_ROOT = next(
    (parent for parent in Path(__file__).resolve().parents if (parent / ".git").exists()),
    Path(__file__).resolve().parents[2],
)
DEFAULT_URL = "https://massive.com/glossary/conditions-indicators"
DEFAULT_OUTPUT = REPO_ROOT / "research" / "market_references" / "massive" / "conditions_indicators_glossary.json"


TABLES = {
    "trade_conditions": {
        "heading": "Trade Conditions",
        "columns": [
            "modifier",
            "condition",
            "sip_mapping",
            "update_high_low",
            "update_last",
            "update_volume",
        ],
    },
    "quote_conditions": {
        "heading": "Quote Conditions",
        "columns": ["modifier", "condition"],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract Massive glossary condition tables into JSON.")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def bits_for_count(count: int, *, include_unknown: bool = True) -> int:
    required = count + (1 if include_unknown else 0)
    return 0 if required <= 1 else math.ceil(math.log2(required))


def bits_for_signed_values(values: list[int]) -> int:
    if not values:
        return 0
    minimum = min(values)
    maximum = max(values)
    for bits in range(1, 65):
        if minimum >= -(2 ** (bits - 1)) and maximum <= (2 ** (bits - 1)) - 1:
            return bits
    return 64


def fetch_text(url: str, *, timeout: float) -> str:
    request = Request(url, headers={"Accept": "text/html"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def normalize_next_payload(text: str) -> str:
    # The page embeds table markup in a Next.js payload with escaped JSON strings.
    return text.replace('\\"', '"').replace("\\n", "\n")


def extract_table_section(payload: str, heading: str, next_heading: str | None) -> str:
    start = payload.find(heading)
    if start < 0:
        raise RuntimeError(f"Could not find table heading {heading!r}")
    end = payload.find(next_heading, start + len(heading)) if next_heading else len(payload)
    if end < 0:
        end = len(payload)
    return payload[start:end]


def extract_rows(section: str, columns: list[str]) -> list[dict[str, str | int | None]]:
    starts = [match.start() for match in re.finditer(r'\["\$","tr","', section)]
    rows: list[dict[str, str | int | None]] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(section)
        row_payload = section[start:end]
        values = [html.unescape(value) for value in re.findall(r'"children":"([^"]*)"', row_payload)]
        if len(values) < len(columns):
            continue
        row: dict[str, str | int | None] = dict(zip(columns, values[: len(columns)]))
        modifier = row.get("modifier")
        try:
            row["modifier_int"] = int(str(modifier))
        except (TypeError, ValueError):
            row["modifier_int"] = None
        rows.append(row)
    return rows


def table_metadata(rows: list[dict[str, str | int | None]]) -> dict[str, int | None]:
    modifiers = [int(row["modifier_int"]) for row in rows if row.get("modifier_int") is not None]
    max_modifier = max(modifiers) if modifiers else None
    min_modifier = min(modifiers) if modifiers else None
    return {
        "rows": len(rows),
        "dense_combo_id_bits_with_unknown": bits_for_count(len(rows), include_unknown=True),
        "dense_combo_id_capacity": 2 ** bits_for_count(len(rows), include_unknown=True),
        "modifier_min": min_modifier,
        "modifier_max": max_modifier,
        "modifier_signed_bits": bits_for_signed_values(modifiers),
    }


def extract_tables(text: str, source_url: str) -> dict[str, object]:
    payload = normalize_next_payload(text)
    table_items = list(TABLES.items())
    result_tables: dict[str, object] = {}
    for index, (table_key, config) in enumerate(table_items):
        next_heading = table_items[index + 1][1]["heading"] if index + 1 < len(table_items) else None
        section = extract_table_section(payload, str(config["heading"]), next_heading)
        rows = extract_rows(section, list(config["columns"]))
        if not rows:
            raise RuntimeError(f"Extracted zero rows for {table_key}")
        result_tables[table_key] = {
            "heading": config["heading"],
            "columns": config["columns"],
            "metadata": table_metadata(rows),
            "rows": rows,
        }
    return {
        "provider": "massive",
        "source_url": source_url,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Extracted from Massive glossary page because quote and trade condition tables are separate.",
            "dense_combo_id_bits_with_unknown is for encoding one complete condition row/table entry plus ID 0 for missing or unknown.",
        ],
        "tables": result_tables,
    }


def main() -> None:
    args = parse_args()
    print(f"FETCH {args.url}", flush=True)
    text = fetch_text(args.url, timeout=args.timeout)
    payload = extract_tables(text, args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"WROTE {args.output}", flush=True)
    for table_key, table_payload in payload["tables"].items():
        metadata = table_payload["metadata"]
        print(
            f"{table_key}: rows={metadata['rows']} "
            f"dense_bits={metadata['dense_combo_id_bits_with_unknown']} "
            f"modifier_range={metadata['modifier_min']}..{metadata['modifier_max']}",
            flush=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR {exc}", file=sys.stderr, flush=True)
        raise
