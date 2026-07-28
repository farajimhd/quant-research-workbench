from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files

from .config import CandidateInventoryConfig
from .pipeline import run


def parser() -> argparse.ArgumentParser:
    defaults = CandidateInventoryConfig()
    value = argparse.ArgumentParser(
        description=(
            "Read-only, resumable news and SEC phrase/value candidate mining. "
            "Dry-run is the default."
        )
    )
    value.add_argument("--execute", action="store_true")
    value.add_argument("--source", choices=("all", "news", "sec"), default="all")
    value.add_argument("--start-date", default=defaults.start_date)
    value.add_argument("--end-date-exclusive", default=defaults.end_date_exclusive)
    value.add_argument("--workers", type=int, default=defaults.workers)
    value.add_argument("--news-page-size", type=int, default=defaults.news_page_size)
    value.add_argument("--sec-page-size", type=int, default=defaults.sec_page_size)
    value.add_argument("--checkpoint-pages", type=int, default=defaults.checkpoint_pages)
    value.add_argument("--min-ngram", type=int, default=defaults.min_ngram)
    value.add_argument("--max-ngram", type=int, default=defaults.max_ngram)
    value.add_argument(
        "--unit-candidate-capacity",
        type=int,
        default=defaults.unit_candidate_capacity,
    )
    value.add_argument(
        "--merged-candidate-capacity",
        type=int,
        default=defaults.merged_candidate_capacity,
    )
    value.add_argument(
        "--max-unique-candidates-per-document",
        type=int,
        default=defaults.max_unique_candidates_per_document,
        help="A nonzero safety bound. Any hit makes the final inventory partial.",
    )
    value.add_argument(
        "--min-document-frequency",
        type=int,
        default=defaults.min_document_frequency,
    )
    value.add_argument(
        "--top-output-candidates",
        type=int,
        default=defaults.top_output_candidates,
    )
    value.add_argument(
        "--max-documents-per-source",
        type=int,
        default=defaults.max_documents_per_source,
        help="Validation-only bound. A nonzero value can never produce a complete inventory.",
    )
    value.add_argument(
        "--runtime-root",
        type=Path,
        default=defaults.runtime_root,
    )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    load_env_files(discover_clickhouse_env_files())
    sources = ("news", "sec") if args.source == "all" else (args.source,)
    config = replace(
        CandidateInventoryConfig(),
        sources=sources,
        start_date=args.start_date,
        end_date_exclusive=args.end_date_exclusive,
        workers=args.workers,
        news_page_size=args.news_page_size,
        sec_page_size=args.sec_page_size,
        checkpoint_pages=args.checkpoint_pages,
        min_ngram=args.min_ngram,
        max_ngram=args.max_ngram,
        unit_candidate_capacity=args.unit_candidate_capacity,
        merged_candidate_capacity=args.merged_candidate_capacity,
        max_unique_candidates_per_document=args.max_unique_candidates_per_document,
        min_document_frequency=args.min_document_frequency,
        top_output_candidates=args.top_output_candidates,
        max_documents_per_source=args.max_documents_per_source,
        runtime_root=args.runtime_root,
    )
    command = [
        "python",
        "-m",
        "research.text_intelligence.candidate_inventory_v1.run_build",
        "--source",
        args.source,
        "--workers",
        str(args.workers),
    ]
    if args.execute:
        command.append("--execute")
    print("COMMAND " + " ".join(command), flush=True)
    return run(config, execute=args.execute)


if __name__ == "__main__":
    raise SystemExit(main())
