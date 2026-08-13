from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import DEFAULT_OUTPUT_ROOT, cancel, collect, evaluate, merge_retry, prepare, prepare_retry, refresh, run_synchronous, submit


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare, submit, collect, and evaluate the LLM issuer-label gold batch.")
    parser.add_argument("command", choices=("prepare", "submit", "status", "cancel", "collect", "evaluate", "prepare-retry", "submit-retry", "collect-retry", "merge-retry", "run-sync"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--sample-size", type=int, default=250)
    parser.add_argument("--seed", type=int, default=20260812)
    parser.add_argument("--authorize-cost-usd", type=float)
    parser.add_argument("--retry-max-completion-tokens", type=int, default=8192)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.output_root, sample_size=args.sample_size, seed=args.seed)
    elif args.command == "prepare-retry":
        result = prepare_retry(args.output_root, max_completion_tokens=args.retry_max_completion_tokens)
    elif args.command == "submit-retry":
        if args.authorize_cost_usd is None:
            parser.error("submit-retry requires --authorize-cost-usd")
        result = submit(args.output_root / "retry_01", authorize_cost_usd=args.authorize_cost_usd)
    elif args.command == "collect-retry":
        result = collect(args.output_root / "retry_01")
    elif args.command == "merge-retry":
        result = merge_retry(args.output_root)
    elif args.command == "run-sync":
        if args.authorize_cost_usd is None:
            parser.error("run-sync requires --authorize-cost-usd")
        result = run_synchronous(args.output_root, authorize_cost_usd=args.authorize_cost_usd)
    elif args.command == "submit":
        if args.authorize_cost_usd is None:
            parser.error("submit requires --authorize-cost-usd")
        result = submit(args.output_root, authorize_cost_usd=args.authorize_cost_usd)
    elif args.command == "status":
        result = refresh(args.output_root)
    elif args.command == "cancel":
        result = cancel(args.output_root)
    elif args.command == "collect":
        result = collect(args.output_root)
    else:
        result = evaluate(args.output_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
