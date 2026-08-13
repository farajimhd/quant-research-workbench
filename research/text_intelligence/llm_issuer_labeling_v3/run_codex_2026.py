from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .codex_2026 import (
    DEFAULT_EXAMPLE_SOURCE_CATALOG,
    DEFAULT_GOLD_ROOT,
    DEFAULT_RUNTIME_ROOT,
    compare_qc,
    consolidate,
    freeze,
    ingest_adjudication,
    ingest_worker_output,
    inventory,
    pending_packets,
    prepare_adjudication,
    prepare_packets,
    prepare_qc,
    render_missing,
    report,
    status,
    synthetic_dry_run,
    validate_lane,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Restart-safe Codex issuer-labeling controller for the frozen 2026 non-gold population.")
    value.add_argument(
        "command",
        choices=(
            "inventory", "render-missing", "freeze", "prepare-packets", "label", "validate",
            "prepare-qc", "label-qc", "prepare-adjudication", "adjudicate", "consolidate",
            "report", "status", "synthetic-dry-run",
        ),
    )
    value.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    value.add_argument("--gold-root", type=Path, default=DEFAULT_GOLD_ROOT)
    value.add_argument("--example-source-catalog", type=Path, default=DEFAULT_EXAMPLE_SOURCE_CATALOG)
    value.add_argument("--execute", action="store_true", help="Authorize canonical renderer writes for render-missing only.")
    value.add_argument("--pilot-size", type=int, default=0)
    value.add_argument("--packet-id", default="")
    value.add_argument("--worker-run-id", default="")
    value.add_argument("--stdin", action="store_true", help="Read one raw worker/adjudicator JSON result from standard input.")
    return value


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    if args.command == "inventory":
        result = inventory(args.runtime_root, args.gold_root)
    elif args.command == "render-missing":
        result = render_missing(args.runtime_root, execute=args.execute)
    elif args.command == "freeze":
        result = freeze(args.runtime_root, args.example_source_catalog)
    elif args.command == "prepare-packets":
        result = prepare_packets(args.runtime_root, lane="single_pass", pilot_size=args.pilot_size)
    elif args.command == "label":
        result = _label(args, lane="single_pass")
    elif args.command == "validate":
        result = {
            "single_pass": validate_lane(args.runtime_root, "single_pass"),
            "qc": validate_lane(args.runtime_root, "qc"),
        }
    elif args.command == "prepare-qc":
        sample = prepare_qc(args.runtime_root)
        packets = prepare_packets(args.runtime_root, lane="qc")
        result = {"sample": sample, "packets": packets}
    elif args.command == "label-qc":
        result = _label(args, lane="qc")
    elif args.command == "prepare-adjudication":
        agreement = compare_qc(args.runtime_root)
        packets = prepare_adjudication(args.runtime_root)
        result = {"agreement": agreement, "packets": packets}
    elif args.command == "adjudicate":
        if not args.packet_id or not args.worker_run_id or not args.stdin:
            raise SystemExit("adjudicate requires --packet-id, --worker-run-id, and --stdin")
        result = ingest_adjudication(
            args.runtime_root,
            packet_id=args.packet_id,
            raw_bytes=sys.stdin.buffer.read(),
            worker_run_id=args.worker_run_id,
        )
    elif args.command == "consolidate":
        result = consolidate(args.runtime_root)
    elif args.command == "report":
        result = report(args.runtime_root)
    elif args.command == "synthetic-dry-run":
        result = synthetic_dry_run(args.runtime_root)
    else:
        result = status(args.runtime_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _label(args: argparse.Namespace, *, lane: str) -> dict[str, object]:
    if not args.packet_id:
        pending = pending_packets(args.runtime_root, lane)
        return {
            "lane": lane,
            "pending_packets": len(pending),
            "tasks": [
                {
                    "packet_id": row["packet_id"],
                    "worker_task_path": row["worker_task_path"],
                    "expected_article_count": row["expected_article_count"],
                    "estimated_input_tokens": row["estimated_input_tokens"],
                }
                for row in pending
            ],
        }
    if not args.worker_run_id or not args.stdin:
        raise SystemExit(f"{args.command} ingestion requires --packet-id, --worker-run-id, and --stdin")
    return ingest_worker_output(
        args.runtime_root,
        lane=lane,
        packet_id=args.packet_id,
        raw_bytes=sys.stdin.buffer.read(),
        worker_run_id=args.worker_run_id,
    )


if __name__ == "__main__":
    main()
