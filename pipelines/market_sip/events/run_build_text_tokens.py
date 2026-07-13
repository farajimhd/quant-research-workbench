from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
SCRIPT = REPO_ROOT / "pipelines" / "market_sip" / "events" / "clickhouse_build_text_tokens.py"


DEFAULTS = {
    "source_database": "q_live",
    "context_database": "market_sip_compact",
    "target_database": "market_sip_compact",
    "news_token_table": "news_text_tokens",
    "sec_token_table": "sec_filing_text_tokens_v3",
    "news_embedding_table": "news_text_embeddings",
    "sec_embedding_table": "sec_filing_text_embeddings_v3",
    "sec_filing_table": "sec_filing_v3",
    "sec_document_table": "sec_filing_document_v3",
    "sec_rendered_text_table": "sec_filing_text_rendered_v3",
    "sec_bridge_table": "id_sec_market_bridge_v3",
    "start_date": "2019-01-01",
    "end_date": "2026-12-31",
    "sources": "news,sec",
    "tokenizer_model": "Qwen/Qwen3-0.6B",
    "embedding_input_source": "source_text",
    "embedding_model": "Qwen/Qwen3-Embedding-0.6B",
    "news_max_tokens": 1024,
    "news_max_chunks": 2,
    "sec_chunk_tokens": 1024,
    "sec_max_chunks": 0,
    "chunk_days": 1,
    "insert_batch_size": 2048,
    "embedding_batch_size": 16,
    "embedding_insert_batch_size": 64,
    "max_threads": 16,
    "max_memory_usage": "120G",
    "output_root_win": r"D:\market-data\prepared\clickhouse_sip_ingest\text_tokens",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launcher for market Qwen text token and optional embedding table build.")
    parser.add_argument("--source-database", default=DEFAULTS["source_database"])
    parser.add_argument("--context-database", default=DEFAULTS["context_database"])
    parser.add_argument("--target-database", default=DEFAULTS["target_database"])
    parser.add_argument("--news-token-table", default=DEFAULTS["news_token_table"])
    parser.add_argument("--sec-token-table", default=DEFAULTS["sec_token_table"])
    parser.add_argument("--news-embedding-table", default=DEFAULTS["news_embedding_table"])
    parser.add_argument("--sec-embedding-table", default=DEFAULTS["sec_embedding_table"])
    parser.add_argument("--sec-filing-table", default=DEFAULTS["sec_filing_table"])
    parser.add_argument("--sec-document-table", default=DEFAULTS["sec_document_table"])
    parser.add_argument("--sec-rendered-text-table", default=DEFAULTS["sec_rendered_text_table"])
    parser.add_argument("--sec-bridge-table", default=DEFAULTS["sec_bridge_table"])
    parser.add_argument("--start-date", default=DEFAULTS["start_date"])
    parser.add_argument("--end-date", default=DEFAULTS["end_date"])
    parser.add_argument("--sources", default=DEFAULTS["sources"])
    parser.add_argument("--tokenizer-model", default=DEFAULTS["tokenizer_model"])
    parser.add_argument("--build-embeddings", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--embedding-input-source", choices=("source_text", "token_tables"), default=DEFAULTS["embedding_input_source"])
    parser.add_argument("--embedding-model", default=DEFAULTS["embedding_model"])
    parser.add_argument("--embedding-device", default="auto")
    parser.add_argument("--embedding-torch-dtype", default="float32")
    parser.add_argument("--embedding-pooling", choices=("mean", "last_token"), default="last_token")
    parser.add_argument("--embedding-batch-size", type=int, default=DEFAULTS["embedding_batch_size"])
    parser.add_argument("--embedding-insert-batch-size", type=int, default=DEFAULTS["embedding_insert_batch_size"])
    parser.add_argument("--profile-embeddings-only", action="store_true")
    parser.add_argument("--embedding-profile-source-rows", type=int, default=256)
    parser.add_argument("--max-tokens", type=int, default=0)
    parser.add_argument("--news-max-tokens", type=int, default=DEFAULTS["news_max_tokens"])
    parser.add_argument("--news-max-chunks", type=int, default=DEFAULTS["news_max_chunks"])
    parser.add_argument("--sec-chunk-tokens", type=int, default=DEFAULTS["sec_chunk_tokens"])
    parser.add_argument("--sec-max-chunks", type=int, default=DEFAULTS["sec_max_chunks"])
    parser.add_argument("--chunk-days", type=int, default=DEFAULTS["chunk_days"])
    parser.add_argument("--insert-batch-size", type=int, default=DEFAULTS["insert_batch_size"])
    parser.add_argument("--news-text-prefix-chars", type=int, default=12000)
    parser.add_argument("--news-body-prefix-chars", type=int, default=0)
    parser.add_argument("--news-external-prefix-chars", type=int, default=0)
    parser.add_argument("--news-pdf-prefix-chars", type=int, default=0)
    parser.add_argument("--sec-text-prefix-chars", type=int, default=0, help="Deprecated no-op. SEC tokenization reads full rendered document text.")
    parser.add_argument("--max-threads", type=int, default=DEFAULTS["max_threads"])
    parser.add_argument("--max-memory-usage", default=DEFAULTS["max_memory_usage"])
    parser.add_argument("--output-root-win", default=DEFAULTS["output_root_win"])
    parser.add_argument("--storage-policy", default="")
    parser.add_argument("--mutation-timeout-seconds", type=int, default=7200)
    parser.add_argument("--clickhouse-url", default="")
    parser.add_argument("--user", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--no-local-files-only", action="store_true")
    parser.add_argument("--strict-tokenizer", action="store_true")
    parser.add_argument("--allow-fallback-tokenizer", action="store_true")
    parser.add_argument("--no-replace-range", action="store_true")
    parser.add_argument("--no-wait-mutations", action="store_true")
    parser.add_argument("--drop-target-tables", action="store_true")
    parser.add_argument("--limit-rows-per-chunk", type=int, default=0)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    argv = [
        sys.executable,
        str(SCRIPT),
        "--source-database",
        args.source_database,
        "--context-database",
        args.context_database,
        "--target-database",
        args.target_database,
        "--news-token-table",
        args.news_token_table,
        "--sec-token-table",
        args.sec_token_table,
        "--news-embedding-table",
        args.news_embedding_table,
        "--sec-embedding-table",
        args.sec_embedding_table,
        "--sec-filing-table",
        args.sec_filing_table,
        "--sec-document-table",
        args.sec_document_table,
        "--sec-rendered-text-table",
        args.sec_rendered_text_table,
        "--sec-bridge-table",
        args.sec_bridge_table,
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--sources",
        args.sources,
        "--tokenizer-model",
        args.tokenizer_model,
        "--embedding-model",
        args.embedding_model,
        "--embedding-input-source",
        args.embedding_input_source,
        "--embedding-device",
        args.embedding_device,
        "--embedding-torch-dtype",
        args.embedding_torch_dtype,
        "--embedding-pooling",
        args.embedding_pooling,
        "--embedding-batch-size",
        str(args.embedding_batch_size),
        "--embedding-insert-batch-size",
        str(args.embedding_insert_batch_size),
        "--embedding-profile-source-rows",
        str(args.embedding_profile_source_rows),
        "--news-max-tokens",
        str(args.news_max_tokens),
        "--news-max-chunks",
        str(args.news_max_chunks),
        "--sec-chunk-tokens",
        str(args.sec_chunk_tokens),
        "--sec-max-chunks",
        str(args.sec_max_chunks),
        "--chunk-days",
        str(args.chunk_days),
        "--insert-batch-size",
        str(args.insert_batch_size),
        "--news-text-prefix-chars",
        str(args.news_text_prefix_chars),
        "--news-body-prefix-chars",
        str(args.news_body_prefix_chars),
        "--news-external-prefix-chars",
        str(args.news_external_prefix_chars),
        "--news-pdf-prefix-chars",
        str(args.news_pdf_prefix_chars),
        "--sec-text-prefix-chars",
        str(args.sec_text_prefix_chars),
        "--max-threads",
        str(args.max_threads),
        "--max-memory-usage",
        args.max_memory_usage,
        "--output-root-win",
        args.output_root_win,
        "--mutation-timeout-seconds",
        str(args.mutation_timeout_seconds),
    ]
    if args.storage_policy:
        argv.extend(["--storage-policy", args.storage_policy])
    if args.clickhouse_url:
        argv.extend(["--clickhouse-url", args.clickhouse_url])
    if args.user:
        argv.extend(["--user", args.user])
    if args.password:
        argv.extend(["--password", args.password])
    if args.max_tokens:
        argv.extend(["--max-tokens", str(args.max_tokens)])
    if args.build_embeddings:
        argv.append("--build-embeddings")
    if args.profile_embeddings_only:
        argv.append("--profile-embeddings-only")
    if args.no_local_files_only:
        argv.append("--no-local-files-only")
    if args.strict_tokenizer:
        argv.append("--strict-tokenizer")
    if args.allow_fallback_tokenizer:
        argv.append("--allow-fallback-tokenizer")
    if args.no_replace_range:
        argv.append("--no-replace-range")
    if args.no_wait_mutations:
        argv.append("--no-wait-mutations")
    if args.drop_target_tables:
        argv.append("--drop-target-tables")
    if args.limit_rows_per_chunk:
        argv.extend(["--limit-rows-per-chunk", str(args.limit_rows_per_chunk)])
    if args.summary_only:
        argv.append("--summary-only")
    if args.dry_run:
        argv.append("--dry-run")

    print("Equivalent command:", flush=True)
    print(" ".join(argv), flush=True)
    if args.print_only:
        return 0
    try:
        return subprocess.call(argv)
    except KeyboardInterrupt:
        print("Interrupted by user.", flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
