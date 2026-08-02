from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig

from .manual_acceptance_review import build_manual_annotation
from .storage import append_annotation, assert_runtime_root, read_json


def main(argv: list[str] | None = None) -> int:
    runtime = MLOpsPathConfig.from_env().runtimes_root
    parser = argparse.ArgumentParser(
        description="Persist reviewer-authored fresh-acceptance specifications from stdin."
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=(
            runtime
            / "text_intelligence"
            / "semantic_calibration_v1"
            / "news_acceptance_100_v1"
        ),
    )
    parser.add_argument(
        "--input-jsonl",
        type=Path,
        help="Optional reviewer-authored JSON/compact input file; stdin is used when omitted.",
    )
    args = parser.parse_args(argv)
    assert_runtime_root(args.runtime_root)
    # Windows PowerShell 5.1 prepends a UTF-8 BOM when piping text to a native
    # process. Accept that transport without weakening JSON validation.
    body = (
        args.input_jsonl.read_text(encoding="utf-8-sig")
        if args.input_jsonl is not None
        else sys.stdin.read().lstrip("\ufeff")
    )
    payload = (
        json.loads(body)
        if body.lstrip().startswith(("[", "{"))
        else _parse_compact_rows(body)
    )
    specs = payload if isinstance(payload, list) else [payload]
    for spec in specs:
        sample_id = str(spec["sample_id"])
        item = read_json(args.runtime_root / "blinded_articles" / f"{sample_id}.json")
        annotation = build_manual_annotation(item, spec)
        digest = append_annotation(args.runtime_root, annotation)
        print(f"RECORDED {sample_id} sha256={digest}", flush=True)
    return 0


def _parse_compact_rows(body: str) -> list[dict[str, object]]:
    """Parse reviewer-authored pipe rows without inferring semantic labels.

    Format: sample|role|origin|decision|default_disposition|unit[;unit...]
    Unit: ticker~issuer_role~concept[,concept...]~direction~pos~neg~FEH
    where FEH is three explicit 0/1 eligibility flags.
    """
    specs: list[dict[str, object]] = []
    for line_number, raw in enumerate(body.splitlines(), start=1):
        line = raw.strip().lstrip("\ufeff\u00ef\u00bb\u00bf")
        if not line or line.startswith("#"):
            continue
        parts = line.split("|", 5)
        if len(parts) != 6:
            raise ValueError(f"compact review line {line_number} must have 6 fields")
        sample_id, role, origin, decision, default_disposition, unit_text = parts
        role = {
            "P": "primary_event", "R": "regulatory_event", "A": "analyst_event",
            "E": "editorial_analysis", "S": "automated_summary",
            "M": "market_roundup", "V": "mover_recap",
            "W": "why_moving_followup", "Q": "preview",
        }.get(role, role)
        origin = {
            "I": "issuer_direct", "R": "regulatory_primary",
            "A": "analyst_research", "E": "editorial_original",
            "G": "editorial_aggregation", "S": "automated_summary",
        }.get(origin, origin)
        decision = {
            "L": "labeled", "N": "no_supported_event",
            "M": "non_issuer_market_content", "I": "identity_not_found",
        }.get(decision, decision)
        default_disposition = {
            "i": "incidental_context", "o": "observed_price_only",
            "a": "analyst_context", "d": "identity_error",
        }.get(default_disposition, default_disposition)
        units: list[dict[str, object]] = []
        for raw_unit in filter(None, unit_text.split(";")):
            values = raw_unit.split("~")
            if len(values) != 7:
                raise ValueError(
                    f"compact review line {line_number} issuer unit must have 7 fields"
                )
            ticker, issuer_role, concepts, direction, positive, negative, flags = values
            issuer_role = {
                "p": "primary_subject", "a": "analyst_subject",
                "m": "mentioned_subject", "t": "target",
                "c": "counterparty", "b": "acquirer",
            }.get(issuer_role, issuer_role)
            direction = {"+": "positive", "-": "negative", "0": "neutral", "x": "mixed"}.get(direction, direction)
            if len(flags) != 3 or any(value not in "01" for value in flags):
                raise ValueError(f"compact review line {line_number} FEH flags are invalid")
            units.append({
                "t": ticker,
                "r": issuer_role,
                "c": [value for value in concepts.split(",") if value],
                "d": direction,
                "pos": int(positive),
                "neg": int(negative),
                "f": flags[0] == "1",
                "e": flags[1] == "1",
                "h": flags[2] == "1",
            })
        spec: dict[str, object] = {
            "sample_id": sample_id,
            "role": role,
            "origin": origin,
            "extraction_decision": decision,
            "units": units,
        }
        if default_disposition:
            spec["default_ticker_disposition"] = default_disposition
        specs.append(spec)
    return specs


if __name__ == "__main__":
    raise SystemExit(main())
