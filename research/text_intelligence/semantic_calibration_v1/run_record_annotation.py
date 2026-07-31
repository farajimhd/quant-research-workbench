from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable

from research.mlops.paths import MLOpsPathConfig

from .storage import append_annotation, assert_runtime_root, read_json, write_json_atomic


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    default_root = (
        MLOpsPathConfig.from_env().runtimes_root
        / "text_intelligence"
        / "semantic_calibration_v1"
        / "news_1000"
    )
    parser = argparse.ArgumentParser(description="Persist one manually reviewed News annotation.")
    parser.add_argument(
        "annotation",
        nargs="?",
        help="Path to a completed annotation JSON file, or '-' to read JSON from stdin",
    )
    parser.add_argument("--runtime-root", type=Path, default=default_root)
    parser.add_argument("--stage-sample")
    parser.add_argument("--stage-index", type=int)
    parser.add_argument("--stage-total", type=int)
    parser.add_argument("--stage-base64")
    parser.add_argument(
        "--stage-stdin",
        action="store_true",
        help="Read one raw staged chunk from stdin instead of a base64 argument.",
    )
    parser.add_argument("--finalize-staged")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    assert_runtime_root(args.runtime_root)
    if args.stage_sample:
        payload = sys.stdin.buffer.read() if args.stage_stdin else None
        return stage_chunk(args, payload=payload)
    if args.finalize_staged:
        return finalize_staged(args.runtime_root, args.finalize_staged)
    if not args.annotation:
        raise SystemExit("annotation path, --stage-sample, or --finalize-staged is required")
    annotation = (
        json.load(sys.stdin)
        if args.annotation == "-"
        else json.loads(Path(args.annotation).read_text(encoding="utf-8"))
    )
    digest = append_annotation(args.runtime_root, annotation)
    print(f"RECORDED {annotation['sample_id']} sha256={digest}", flush=True)
    return 0


def stage_chunk(args: argparse.Namespace, *, payload: bytes | None = None) -> int:
    encoded = getattr(args, "stage_base64", None)
    if args.stage_index is None or args.stage_total is None or (
        payload is None and encoded is None
    ):
        raise SystemExit(
            "--stage-sample requires --stage-index, --stage-total, and either "
            "--stage-base64 or --stage-stdin"
        )
    if args.stage_index < 0 or args.stage_total <= 0 or args.stage_index >= args.stage_total:
        raise SystemExit("invalid staged chunk index/total")
    if payload is not None and encoded is not None:
        raise SystemExit("staged chunk cannot use both stdin and base64 payloads")
    if payload is None:
        try:
            payload = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise SystemExit(f"invalid base64 annotation chunk: {exc}") from exc
    encoded = base64.b64encode(payload).decode("ascii")
    stage = args.runtime_root / "annotation_staging_v2" / args.stage_sample
    chunk = {
        "sample_id": args.stage_sample,
        "index": args.stage_index,
        "total": args.stage_total,
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "payload_base64": encoded,
    }
    target = stage / f"{args.stage_index:05d}.json"
    if target.exists():
        existing = read_json(target)
        if existing != chunk:
            raise FileExistsError(f"conflicting staged chunk: {target}")
    else:
        write_json_atomic(target, chunk)
    print(
        f"STAGED {args.stage_sample} chunk={args.stage_index + 1}/{args.stage_total}",
        flush=True,
    )
    return 0


def finalize_staged(runtime_root: Path, sample_id: str) -> int:
    stage = runtime_root / "annotation_staging_v2" / sample_id
    paths = sorted(stage.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"no staged chunks for {sample_id}: {stage}")
    chunks = [read_json(path) for path in paths]
    totals = {int(chunk["total"]) for chunk in chunks}
    if len(totals) != 1:
        raise ValueError(f"inconsistent staged totals for {sample_id}")
    total = totals.pop()
    by_index = {int(chunk["index"]): chunk for chunk in chunks}
    if set(by_index) != set(range(total)):
        missing = sorted(set(range(total)) - set(by_index))
        raise ValueError(f"missing staged chunks for {sample_id}: {missing}")
    payloads: list[bytes] = []
    for index in range(total):
        chunk = by_index[index]
        if str(chunk.get("sample_id")) != sample_id:
            raise ValueError(f"staged sample mismatch at chunk {index}")
        payload = base64.b64decode(str(chunk["payload_base64"]), validate=True)
        if hashlib.sha256(payload).hexdigest() != chunk.get("payload_sha256"):
            raise ValueError(f"staged chunk hash mismatch at chunk {index}")
        payloads.append(payload)
    annotation = json.loads(b"".join(payloads).decode("utf-8"))
    if str(annotation.get("sample_id")) != sample_id:
        raise ValueError(f"assembled annotation sample mismatch for {sample_id}")
    digest = append_annotation(runtime_root, annotation)
    for path in paths:
        path.unlink()
    stage.rmdir()
    print(f"RECORDED {sample_id} sha256={digest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
