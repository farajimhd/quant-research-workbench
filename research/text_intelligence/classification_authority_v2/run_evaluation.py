from __future__ import annotations

from .evaluation import parse_args, run


def main() -> int:
    args = parse_args()
    print(
        "CLASSIFICATION AUTHORITY V2 | "
        f"sample={args.sample_size:,} news + {args.sample_size:,} SEC | "
        f"output={args.output_root}",
        flush=True,
    )
    payload = run(args)
    print(
        "COMPLETED | "
        f"news_agreement={payload['reaction']['news']['exact_direction_agreement']} "
        f"sec_agreement={payload['reaction']['sec']['exact_direction_agreement']} "
        f"output={args.output_root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
