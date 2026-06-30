from __future__ import annotations

import argparse
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


EXPECTED_TABLES = {
    "trade_conditions": "Trade Conditions",
    "quote_conditions": "Quote Conditions",
    "trade_corrections_nyse": "Trade Corrections (NYSE)",
    "financial_status": "Financial Status (CTA/UTP)",
    "cta_security_status": "CTA Security Status",
    "halt_reason": "CTA Halt Reason / UTP Trade Action Reason",
    "utp_security_status": "UTP Security Status",
    "nbbo_indicators": "NBBO Indicators",
    "held_trade_indicators": "Held Trade Indicators",
    "misc_indicators": "Misc Indicators",
    "luld_indicators": "LULD Indicators",
}

HEADING_TO_KEY = {heading: key for key, heading in EXPECTED_TABLES.items()}


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
    # The page embeds table markup in Next.js flight string fragments. Decode each
    # fragment as JSON so escaped quotes inside table cells are preserved.
    segments = [
        json.loads(f'"{match.group(1)}"')
        for match in re.finditer(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)', text)
    ]
    if segments:
        return "\n".join(segments)
    return text


def decode_next_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "table"


def table_key_for_heading(heading: str) -> str:
    return HEADING_TO_KEY.get(heading, slugify(heading))


def normalize_column_name(value: str) -> str:
    column = slugify(value)
    aliases = {
        "modifier": "modifier",
        "condition": "condition",
        "sip_mapping": "sip_mapping",
        "update_high_low": "update_high_low",
        "update_last": "update_last",
        "update_volume": "update_volume",
    }
    return aliases.get(column, column)


def extract_h1_sections(payload: str) -> list[tuple[str, str]]:
    h1_pattern = re.compile(r'\["\$","h1",null,\{[^{}]*"children":"((?:\\.|[^"\\])*)"\}\]')
    matches = [(match.start(), decode_next_string(match.group(1)).strip()) for match in h1_pattern.finditer(payload)]
    sections: list[tuple[str, str]] = []
    for index, (start, heading) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(payload)
        section = payload[start:end]
        if '["$","table"' in section:
            sections.append((heading, section))
    return sections


def bracketed_array(payload: str, start: int) -> str:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(payload)):
        char = payload[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return payload[start : index + 1]
    raise RuntimeError("Unterminated React array in glossary payload")


def reference_cell_values(payload: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in re.finditer(r'([0-9A-Za-z]+):\["\$","t[dh]",', payload):
        ref = match.group(1)
        fragment = bracketed_array(payload, match.start() + len(ref) + 1)
        child = re.search(r'"children":"((?:\\.|[^"\\])*)"', fragment)
        if child:
            values[ref] = decode_next_string(child.group(1)).strip()
    return values


def reference_row_values(payload: str, cell_refs: dict[str, str]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for match in re.finditer(r'([0-9A-Za-z]+):\["\$","tr",', payload):
        ref = match.group(1)
        fragment = bracketed_array(payload, match.start() + len(ref) + 1)
        row = row_values(fragment, cell_refs)
        if row:
            values[ref] = row
    return values


def row_values(row_payload: str, cell_refs: dict[str, str]) -> list[str]:
    values: list[str] = []
    token_pattern = re.compile(r'"children":"((?:\\.|[^"\\])*)"|"\$L([0-9A-Za-z]+)"')
    for match in token_pattern.finditer(row_payload):
        if match.group(1) is not None:
            values.append(decode_next_string(match.group(1)).strip())
        elif match.group(2) in cell_refs:
            values.append(cell_refs[match.group(2)])
    return values


def extract_table_rows(
    section: str,
    cell_refs: dict[str, str],
    row_refs: dict[str, list[str]],
) -> tuple[list[str], list[dict[str, str | int | None]]]:
    raw_rows: list[list[str]] = []
    rows: list[dict[str, str | int | None]] = []
    row_pattern = re.compile(r'\["\$","tr",|"\$L([0-9A-Za-z]+)"')
    seen: set[tuple[str, ...]] = set()
    for match in row_pattern.finditer(section):
        ref = match.group(1)
        if ref is not None:
            values = row_refs.get(ref)
            if not values:
                continue
        else:
            row_payload = bracketed_array(section, match.start())
            values = row_values(row_payload, cell_refs)
        if values:
            row_key = tuple(values)
            if row_key in seen:
                continue
            seen.add(row_key)
            raw_rows.append(values)
    if len(raw_rows) < 2:
        raise RuntimeError("Expected a header row and at least one data row")
    columns = [normalize_column_name(value) for value in raw_rows[0]]
    if len(set(columns)) != len(columns):
        raise RuntimeError(f"Duplicate normalized table columns: {columns}")
    for values in raw_rows[1:]:
        if len(values) < len(columns):
            continue
        row: dict[str, str | int | None] = dict(zip(columns, values[: len(columns)]))
        row["source_row"] = len(rows) + 1
        modifier = row.get("modifier")
        try:
            row["modifier_int"] = int(str(modifier))
        except (TypeError, ValueError):
            row["modifier_int"] = None
        rows.append(row)
    return columns, rows


def table_metadata(rows: list[dict[str, str | int | None]]) -> dict[str, int | list[int] | None]:
    modifiers = [int(row["modifier_int"]) for row in rows if row.get("modifier_int") is not None]
    max_modifier = max(modifiers) if modifiers else None
    min_modifier = min(modifiers) if modifiers else None
    duplicate_modifiers = sorted({modifier for modifier in modifiers if modifiers.count(modifier) > 1})
    return {
        "rows": len(rows),
        "dense_combo_id_bits_with_unknown": bits_for_count(len(rows), include_unknown=True),
        "dense_combo_id_capacity": 2 ** bits_for_count(len(rows), include_unknown=True),
        "modifier_min": min_modifier,
        "modifier_max": max_modifier,
        "modifier_signed_bits": bits_for_signed_values(modifiers),
        "duplicate_modifier_count": len(duplicate_modifiers),
        "duplicate_modifiers": duplicate_modifiers,
    }


def extract_tables(text: str, source_url: str) -> dict[str, object]:
    payload = normalize_next_payload(text)
    cell_refs = reference_cell_values(payload)
    row_refs = reference_row_values(payload, cell_refs)
    result_tables: dict[str, object] = {}
    for heading, section in extract_h1_sections(payload):
        table_key = table_key_for_heading(heading)
        columns, rows = extract_table_rows(section, cell_refs, row_refs)
        if not rows:
            raise RuntimeError(f"Extracted zero rows for {table_key}")
        result_tables[table_key] = {
            "heading": heading,
            "columns": columns,
            "metadata": table_metadata(rows),
            "rows": rows,
        }
    missing = [heading for key, heading in EXPECTED_TABLES.items() if key not in result_tables]
    if missing:
        raise RuntimeError(f"Missing expected glossary tables: {missing}")
    return {
        "provider": "massive",
        "source_url": source_url,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Extracted from Massive glossary page because quote, trade, status, and indicator domains are separate.",
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
