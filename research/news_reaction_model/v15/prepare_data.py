from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import numpy as np

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v15 import HORIZONS, MODEL_VERSION
from research.news_reaction_model.v15.config import LoaderConfig, to_dict
from research.news_reaction_model.v15.context import (
    build_context_feature,
    context_contract,
    normalized_context_metadata,
)
from research.news_reaction_model.v15.prepared import (
    ARRAY_FILES,
    BUILD_STATE_FILE,
    MANIFEST_FILE,
    create_arrays,
    load_json,
    open_arrays,
    write_json_atomic,
)
from research.news_reaction_model.v15.time_features import (
    encode_time_features,
    parse_published_at_utc,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def q(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def qi(value: str) -> str:
    return "`" + str(value).replace("`", "``") + "`"


def float32_array_base64_sql(expression: str) -> str:
    return (
        "base64Encode(arrayStringConcat(arrayMap("
        f"x -> rightPad(reinterpretAsString(x), 4, char(0)), {expression}"
        ")))"
    )


def month_ranges(start: str, end_exclusive: str) -> list[tuple[dt.date, dt.date]]:
    cursor = dt.date.fromisoformat(start).replace(day=1)
    requested_start = dt.date.fromisoformat(start)
    end = dt.date.fromisoformat(end_exclusive)
    ranges: list[tuple[dt.date, dt.date]] = []
    while cursor < end:
        next_month = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        ranges.append((max(cursor, requested_start), min(next_month, end)))
        cursor = next_month
    return ranges


def source_audit_sql(config: LoaderConfig) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    return f"""
SELECT count(), uniqExact(tuple(canonical_news_id, ticker, published_at_utc)),
 min(published_at_utc), max(published_at_utc),
 uniqExact(representation_name), uniqExact(representation_sha256),
 any(representation_name), any(representation_sha256),
 countIf(length(openai_embedding) != {config.openai_embedding_dim}
      OR length(stock_state) != {config.stock_state_dim}
      OR length(horizon_codes) != length(return_targets))
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(config.train_start)}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(config.validation_end_exclusive)}, 9, 'UTC')
FORMAT TSV
"""


def source_page_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
    cursor: tuple[str, str, str],
) -> str:
    table = f"{qi(config.dataset_database)}.{qi(config.dataset_table)}"
    embedding = float32_array_base64_sql("openai_embedding")
    timestamp, ticker, canonical_id = cursor
    return f"""
SELECT canonical_news_id, ticker, published_at_utc, publication_session,
 {embedding} AS openai_embedding_b64, stock_state, horizon_codes, return_targets
FROM {table} FINAL
WHERE dataset_version = {q(config.dataset_version)}
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
 AND (published_at_utc, ticker, canonical_news_id) >
     (toDateTime64({q(timestamp)}, 9, 'UTC'), {q(ticker)}, {q(canonical_id)})
ORDER BY published_at_utc, ticker, canonical_news_id
LIMIT {int(config.query_batch_articles)}
SETTINGS max_threads={config.max_threads_per_query}, max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


def reaction_rows_sql(config: LoaderConfig, start: dt.date, end: dt.date) -> str:
    horizons = ",".join(q(value) for value in config.horizons)
    table = f"{qi(config.news_database)}.{qi(config.reaction_table)}"
    return f"""
SELECT canonical_news_id, ticker, published_at_utc, horizon_code,
 available_at_utc, reaction_session_date
FROM {table} FINAL
WHERE label_version = {q(config.label_version)}
 AND applicable = 1
 AND horizon_code IN ({horizons})
 AND published_at_utc >= toDateTime64({q(start.isoformat())}, 9, 'UTC')
 AND published_at_utc < toDateTime64({q(end.isoformat())}, 9, 'UTC')
ORDER BY published_at_utc, ticker, canonical_news_id, horizon_code
FORMAT JSONEachRow
"""


def calendar_sql(config: LoaderConfig) -> str:
    table = f"{qi(config.news_database)}.{qi(config.reaction_calendar_table)}"
    return f"""
SELECT calendar_date
FROM {table} FINAL
WHERE calendar_version = {q(config.reaction_calendar_version)}
 AND calendar_date >= toDate({q(config.train_start)}) - 14
 AND calendar_date < toDate({q(config.validation_end_exclusive)}) + 14
ORDER BY calendar_date
FORMAT TSV
"""


def parse_json_each_row(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def source_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
) -> Iterator[dict[str, Any]]:
    cursor = ("1970-01-01", "", "")
    while True:
        rows = parse_json_each_row(client.execute(source_page_sql(config, start, end, cursor)))
        if not rows:
            return
        yield from rows
        last = rows[-1]
        cursor = (
            str(last["published_at_utc"]),
            str(last["ticker"]),
            str(last["canonical_news_id"]),
        )
        if len(rows) < config.query_batch_articles:
            return


@dataclass(slots=True)
class ReactionAvailability:
    available_at_by_horizon: dict[str, dt.datetime]
    reaction_session_date: dt.date


def load_reactions(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
) -> dict[tuple[str, str, str], ReactionAvailability]:
    grouped: dict[tuple[str, str, str], ReactionAvailability] = {}
    for row in parse_json_each_row(client.execute(reaction_rows_sql(config, start, end))):
        key = (
            str(row["canonical_news_id"]),
            str(row["ticker"]),
            str(row["published_at_utc"]),
        )
        session_date = dt.date.fromisoformat(str(row["reaction_session_date"])[:10])
        item = grouped.get(key)
        if item is None:
            item = ReactionAvailability({}, session_date)
            grouped[key] = item
        elif item.reaction_session_date != session_date:
            raise RuntimeError(f"Reaction session drift for {key}: {item.reaction_session_date} vs {session_date}.")
        horizon = str(row["horizon_code"])
        available_at = parse_published_at_utc(row["available_at_utc"])
        previous = item.available_at_by_horizon.get(horizon)
        if previous is not None and previous != available_at:
            raise RuntimeError(f"Reaction availability drift for {key} / {horizon}.")
        item.available_at_by_horizon[horizon] = available_at
    return grouped


@dataclass(slots=True)
class HistoryRecord:
    row_index: int
    canonical_news_id: str
    ticker: str
    published_at_utc: dt.datetime
    published_at_text: str
    publication_session: str
    reaction_session_index: int
    horizon_codes: tuple[str, ...]
    return_targets: np.ndarray
    available_at_by_horizon: Mapping[str, dt.datetime]


def decode_embedding(row: Mapping[str, Any], expected_dim: int) -> np.ndarray:
    raw = base64.b64decode(str(row.get("openai_embedding_b64") or ""), validate=True)
    expected_bytes = expected_dim * np.dtype("<f4").itemsize
    if len(raw) != expected_bytes:
        raise ValueError(
            f"Embedding for {row.get('canonical_news_id')} has {len(raw)} bytes; "
            f"expected {expected_bytes}."
        )
    result = np.frombuffer(raw, dtype="<f4")
    if result.shape != (expected_dim,) or not np.isfinite(result).all():
        raise ValueError(f"Invalid OpenAI embedding for {row.get('canonical_news_id')}.")
    return result


def decode_targets(
    row: Mapping[str, Any],
    config: LoaderConfig,
) -> tuple[np.ndarray, np.ndarray, tuple[str, ...], np.ndarray]:
    values = np.zeros((len(config.horizons), 3), dtype=np.float32)
    mask = np.zeros(len(config.horizons), dtype=np.bool_)
    source_codes = tuple(str(value) for value in row.get("horizon_codes", ()))
    source_values = np.asarray(row.get("return_targets", ()), dtype=np.float32)
    if source_values.shape != (len(source_codes), 3):
        raise ValueError(
            f"Target shape mismatch for {row.get('canonical_news_id')}: "
            f"{source_values.shape} versus {len(source_codes)} codes."
        )
    target_index = {value: index for index, value in enumerate(config.horizons)}
    for code, target in zip(source_codes, source_values):
        index = target_index.get(code)
        if index is None:
            continue
        values[index] = target
        mask[index] = bool(np.isfinite(target).all() and (target >= -1.0).all())
    return values, mask, source_codes, source_values


def key_for(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["canonical_news_id"]),
        str(row["ticker"]),
        str(row["published_at_utc"]),
    )


def build_representation_sha256(
    config: LoaderConfig,
    *,
    source_representation_sha256: str,
    source_rows_count: int,
) -> str:
    payload = {
        "model_version": MODEL_VERSION,
        "prepared_dataset_version": config.prepared_dataset_version,
        "source_dataset": f"{config.dataset_database}.{config.dataset_table}",
        "source_dataset_version": config.dataset_version,
        "source_representation_sha256": source_representation_sha256,
        "source_rows": source_rows_count,
        "embedding_version": config.embedding_version,
        "reaction_source": f"{config.news_database}.{config.reaction_table}",
        "label_version": config.label_version,
        "reaction_calendar_source": (
            f"{config.news_database}.{config.reaction_calendar_table}"
        ),
        "reaction_calendar_version": config.reaction_calendar_version,
        "horizons": list(config.horizons),
        "source_range": [config.train_start, config.validation_end_exclusive],
        "context": context_contract(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def audit_source(client: ClickHouseHttpClient, config: LoaderConfig) -> dict[str, Any]:
    fields = client.execute(source_audit_sql(config)).strip().split("\t")
    if len(fields) < 9:
        raise RuntimeError(f"Unexpected V8 source audit response: {fields}.")
    result = {
        "rows": int(fields[0]),
        "unique_rows": int(fields[1]),
        "min_published_at_utc": fields[2],
        "max_published_at_utc": fields[3],
        "representation_names": int(fields[4]),
        "representation_versions": int(fields[5]),
        "representation_name": fields[6],
        "representation_sha256": fields[7],
        "invalid_rows": int(fields[8]),
    }
    if (
        result["rows"] <= 0
        or result["rows"] != result["unique_rows"]
        or result["representation_names"] != 1
        or result["representation_versions"] != 1
        or result["representation_name"] != config.representation_name
        or result["invalid_rows"]
    ):
        raise RuntimeError(f"V15 source audit failed: {result}.")
    return result


def load_calendar_index(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
) -> dict[dt.date, int]:
    dates = [
        dt.date.fromisoformat(line.strip()[:10])
        for line in client.execute(calendar_sql(config)).splitlines()
        if line.strip()
    ]
    if not dates or dates != sorted(set(dates)):
        raise RuntimeError("Reaction calendar is empty, duplicated, or unordered.")
    return {value: index for index, value in enumerate(dates)}


def clear_known_outputs(root: Path) -> None:
    for filename in (*ARRAY_FILES.values(), MANIFEST_FILE, BUILD_STATE_FILE):
        path = root / filename
        if path.exists():
            path.unlink()


def _flush(arrays: Mapping[str, np.ndarray]) -> None:
    for array in arrays.values():
        flush = getattr(array, "flush", None)
        if flush is not None:
            flush()


def _timestamp_us(value: dt.datetime) -> int:
    return int(round(value.timestamp() * 1_000_000.0))


def _decode_bytes(value: Any) -> str:
    return bytes(value).rstrip(b"\x00").decode("utf-8")


def rebuild_history(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    arrays: Mapping[str, np.ndarray],
    *,
    row_offset: int,
    resume_at: dt.date,
    session_index: Mapping[dt.date, int],
) -> dict[str, deque[HistoryRecord]]:
    history: dict[str, deque[HistoryRecord]] = defaultdict(deque)
    if row_offset <= 0:
        return history
    start = resume_at - dt.timedelta(days=config.context_lookback_days)
    reactions = load_reactions(client, config, start, resume_at)
    published_us = np.asarray(arrays["published_at_us"][:row_offset])
    lower = int(np.searchsorted(published_us, _timestamp_us(parse_published_at_utc(start.isoformat())), side="left"))
    indices_by_key = {
        (
            _decode_bytes(arrays["canonical_news_id"][index]),
            _decode_bytes(arrays["ticker"][index]),
            _decode_bytes(arrays["published_at_utc"][index]),
        ): index
        for index in range(lower, row_offset)
    }
    for row in source_rows(client, config, start, resume_at):
        key = key_for(row)
        index = indices_by_key.get(key)
        reaction = reactions.get(key)
        if index is None or reaction is None:
            raise RuntimeError(f"Cannot reconstruct V15 resume history for {key}.")
        _, _, source_codes, source_values = decode_targets(row, config)
        session_position = session_index.get(reaction.reaction_session_date)
        if session_position is None:
            raise RuntimeError(f"Missing calendar index for {reaction.reaction_session_date}.")
        record = HistoryRecord(
            row_index=index,
            canonical_news_id=key[0],
            ticker=key[1],
            published_at_utc=parse_published_at_utc(key[2]),
            published_at_text=key[2],
            publication_session=str(row["publication_session"]),
            reaction_session_index=session_position,
            horizon_codes=source_codes,
            return_targets=source_values,
            available_at_by_horizon=reaction.available_at_by_horizon,
        )
        history[record.ticker].append(record)
    return history


def populate(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    arrays: Mapping[str, np.ndarray],
    *,
    source_audit: Mapping[str, Any],
    representation_sha256: str,
    state: dict[str, Any],
    session_index: Mapping[dt.date, int],
) -> dict[str, Any]:
    ranges = month_ranges(config.train_start, config.validation_end_exclusive)
    next_month = str(state.get("next_month_start") or config.train_start)
    row_offset = int(state.get("row_offset") or 0)
    start_index = next(
        (index for index, (start, _) in enumerate(ranges) if start.isoformat() >= next_month),
        len(ranges),
    )
    history = rebuild_history(
        client,
        config,
        arrays,
        row_offset=row_offset,
        resume_at=ranges[start_index][0] if start_index < len(ranges) else dt.date.fromisoformat(config.validation_end_exclusive),
        session_index=session_index,
    )
    started = time.perf_counter()
    context_rows = int(state.get("context_rows") or 0)
    context_slots = int(state.get("context_slots") or 0)
    causal_reaction_slots = int(state.get("causal_reaction_slots") or 0)
    for month_index in range(start_index, len(ranges)):
        month_start, month_end = ranges[month_index]
        reactions = load_reactions(client, config, month_start, month_end)
        month_rows = 0
        for row in source_rows(client, config, month_start, month_end):
            if row_offset >= int(source_audit["rows"]):
                raise RuntimeError("V15 source produced more rows than its audited count.")
            key = key_for(row)
            published = parse_published_at_utc(key[2])
            reaction = reactions.get(key)
            if reaction is None:
                raise RuntimeError(f"Missing reaction availability metadata for {key}.")
            missing_availability = set(str(value) for value in row["horizon_codes"]) - set(
                reaction.available_at_by_horizon
            )
            if missing_availability:
                raise RuntimeError(
                    f"Missing horizon availability for {key}: {sorted(missing_availability)}."
                )
            current_session_index = session_index.get(reaction.reaction_session_date)
            if current_session_index is None:
                raise RuntimeError(f"Missing reaction calendar date {reaction.reaction_session_date}.")
            ticker_history = history[key[1]]
            cutoff = published - dt.timedelta(days=config.context_lookback_days)
            while ticker_history and ticker_history[0].published_at_utc < cutoff:
                ticker_history.popleft()
            eligible = [
                item
                for item in ticker_history
                if cutoff <= item.published_at_utc < published
            ][-config.context_size :]
            values, label_mask, source_codes, source_values = decode_targets(row, config)
            arrays["openai_embedding"][row_offset] = decode_embedding(
                row, config.openai_embedding_dim
            )
            state_values = np.asarray(row.get("stock_state", ()), dtype=np.float32)
            if (
                state_values.shape != (config.stock_state_dim,)
                or not np.isfinite(state_values).all()
            ):
                raise ValueError(f"Invalid stock state for {key}: {state_values.shape}.")
            arrays["stock_state"][row_offset] = state_values
            arrays["time_features"][row_offset] = np.asarray(
                encode_time_features(key[2], row["publication_session"]),
                dtype=np.float32,
            )
            arrays["return_targets"][row_offset] = values
            arrays["label_mask"][row_offset] = label_mask
            arrays["canonical_news_id"][row_offset] = key[0].encode("utf-8")
            arrays["ticker"][row_offset] = key[1].encode("utf-8")
            arrays["published_at_utc"][row_offset] = key[2].encode("utf-8")
            arrays["published_at_us"][row_offset] = _timestamp_us(published)
            arrays["publication_session"][row_offset] = str(row["publication_session"]).encode(
                "utf-8"
            )
            for slot, prior in enumerate(eligible):
                metadata = normalized_context_metadata(
                    prior_published_at_utc=prior.published_at_utc,
                    current_published_at_utc=published,
                    prior_publication_session=prior.publication_session,
                    current_publication_session=str(row["publication_session"]),
                    prior_reaction_session_index=prior.reaction_session_index,
                    current_reaction_session_index=current_session_index,
                )
                feature, reaction_mask = build_context_feature(
                    prior_returns=prior.return_targets,
                    prior_horizon_codes=prior.horizon_codes,
                    available_at_by_horizon=prior.available_at_by_horizon,
                    current_published_at_utc=published,
                    metadata=metadata,
                )
                arrays["context_indices"][row_offset, slot] = prior.row_index
                arrays["context_features"][row_offset, slot] = feature
                arrays["context_mask"][row_offset, slot] = True
                causal_reaction_slots += int(reaction_mask.sum())
            if eligible:
                context_rows += 1
                context_slots += len(eligible)
            record = HistoryRecord(
                row_index=row_offset,
                canonical_news_id=key[0],
                ticker=key[1],
                published_at_utc=published,
                published_at_text=key[2],
                publication_session=str(row["publication_session"]),
                reaction_session_index=current_session_index,
                horizon_codes=source_codes,
                return_targets=source_values,
                available_at_by_horizon=reaction.available_at_by_horizon,
            )
            ticker_history.append(record)
            row_offset += 1
            month_rows += 1
        _flush(arrays)
        next_month_start = month_end.isoformat()
        state = {
            "status": "building",
            "dataset_version": config.prepared_dataset_version,
            "representation_sha256": representation_sha256,
            "source_rows": int(source_audit["rows"]),
            "row_offset": row_offset,
            "next_month_start": next_month_start,
            "context_rows": context_rows,
            "context_slots": context_slots,
            "causal_reaction_slots": causal_reaction_slots,
        }
        write_json_atomic(config.prepared_dataset_root / BUILD_STATE_FILE, state)
        elapsed = time.perf_counter() - started
        completed = month_index - start_index + 1
        remaining = len(ranges) - month_index - 1
        eta = elapsed / max(1, completed) * remaining
        print(
            f"[{month_index + 1}/{len(ranges)}] {month_start:%Y-%m} COMPLETED "
            f"rows={month_rows:,} total={row_offset:,} context={context_rows:,} "
            f"elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
            flush=True,
        )
    if row_offset != int(source_audit["rows"]):
        raise RuntimeError(
            f"V15 prepared row count mismatch: wrote {row_offset}, expected {source_audit['rows']}."
        )
    return state


def final_audit(
    config: LoaderConfig,
    arrays: Mapping[str, np.ndarray],
    *,
    source_audit: Mapping[str, Any],
    representation_sha256: str,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    rows = int(source_audit["rows"])
    published = np.asarray(arrays["published_at_us"])
    if published.shape != (rows,) or np.any(published[1:] < published[:-1]):
        raise RuntimeError("V15 prepared timestamps are not globally chronological.")
    context_indices = np.asarray(arrays["context_indices"])
    context_mask = np.asarray(arrays["context_mask"])
    row_ids = np.arange(rows, dtype=np.int64)[:, None]
    if np.any(context_mask & (context_indices < 0)):
        raise RuntimeError("V15 context mask references padding indices.")
    if np.any(context_mask & (context_indices.astype(np.int64) >= row_ids)):
        raise RuntimeError("V15 context contains a current or future row index.")
    if np.any((~context_mask) & (context_indices != -1)):
        raise RuntimeError("V15 padded context slots must use index -1.")
    context_features = np.asarray(arrays["context_features"])
    if not np.isfinite(context_features).all():
        raise RuntimeError("V15 context features contain non-finite values.")
    train_boundary = _timestamp_us(parse_published_at_utc(config.train_end_exclusive))
    validation_start = _timestamp_us(parse_published_at_utc(config.validation_start))
    validation_end = _timestamp_us(parse_published_at_utc(config.validation_end_exclusive))
    train_rows = int(np.count_nonzero((published >= _timestamp_us(parse_published_at_utc(config.train_start))) & (published < train_boundary)))
    validation_rows = int(np.count_nonzero((published >= validation_start) & (published < validation_end)))
    result = {
        "status": "complete",
        "model_version": MODEL_VERSION,
        "dataset_version": config.prepared_dataset_version,
        "rows": rows,
        "train_rows": train_rows,
        "validation_rows": validation_rows,
        "context_rows": int(np.count_nonzero(context_mask.any(axis=1))),
        "context_slots": int(np.count_nonzero(context_mask)),
        "causal_reaction_slots": int(state.get("causal_reaction_slots") or 0),
        "min_published_at_utc": source_audit["min_published_at_utc"],
        "max_published_at_utc": source_audit["max_published_at_utc"],
        "source_dataset": f"{config.dataset_database}.{config.dataset_table}",
        "source_dataset_version": config.dataset_version,
        "source_representation_sha256": source_audit["representation_sha256"],
        "representation_sha256": representation_sha256,
        "label_source": f"{config.news_database}.{config.reaction_table}",
        "label_version": config.label_version,
        "context_contract": context_contract(),
        "loader": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in to_dict(config).items()
        },
        "array_files": ARRAY_FILES,
    }
    if train_rows <= 0 or validation_rows <= 0:
        raise RuntimeError(f"V15 split audit failed: {result}.")
    return result


def build_parser() -> argparse.ArgumentParser:
    defaults = LoaderConfig()
    parser = argparse.ArgumentParser(
        description="Build V15 indexed causal prior-news context without duplicating OpenAI embeddings."
    )
    parser.add_argument("--prepared-root", default=str(defaults.prepared_dataset_root))
    parser.add_argument("--query-batch-articles", type=int, default=defaults.query_batch_articles)
    parser.add_argument("--max-threads-per-query", type=int, default=defaults.max_threads_per_query)
    parser.add_argument("--max-memory-usage", default=defaults.max_memory_usage)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    config = LoaderConfig(
        prepared_dataset_root=Path(args.prepared_root),
        query_batch_articles=max(1, args.query_batch_articles),
        max_threads_per_query=max(1, args.max_threads_per_query),
        max_memory_usage=args.max_memory_usage,
    )
    if not args.execute:
        print(
            "PREFLIGHT ONLY | add --execute to build "
            f"{config.prepared_dataset_root}",
            flush=True,
        )
        return 0
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    source_audit = audit_source(client, config)
    representation_sha256 = build_representation_sha256(
        config,
        source_representation_sha256=str(source_audit["representation_sha256"]),
        source_rows_count=int(source_audit["rows"]),
    )
    manifest_path = config.prepared_dataset_root / MANIFEST_FILE
    if manifest_path.exists() and not args.restart:
        existing = load_json(manifest_path)
        if (
            existing.get("status") == "complete"
            and existing.get("representation_sha256") == representation_sha256
        ):
            print(
                f"ALREADY COMPLETE | rows={existing.get('rows'):,} "
                f"representation={representation_sha256}",
                flush=True,
            )
            return 0
        if not (
            existing.get("status") == "building"
            and existing.get("representation_sha256") == representation_sha256
            and int(existing.get("rows") or 0) == int(source_audit["rows"])
        ):
            raise RuntimeError(
                f"Prepared manifest exists but does not match this build: {existing}. "
                "Use --restart only after confirming the previous build is disposable."
            )
    if args.restart:
        clear_known_outputs(config.prepared_dataset_root)
    state_path = config.prepared_dataset_root / BUILD_STATE_FILE
    if state_path.exists():
        state = load_json(state_path)
        if (
            state.get("dataset_version") != config.prepared_dataset_version
            or state.get("representation_sha256") != representation_sha256
            or int(state.get("source_rows") or 0) != int(source_audit["rows"])
        ):
            raise RuntimeError(
                f"Cannot resume V15 because build state drifted: {state}. Use --restart."
            )
        arrays, _ = open_arrays(config, mode="r+", require_complete=False)
        print(
            f"RESUME | rows={int(state.get('row_offset') or 0):,} "
            f"next={state.get('next_month_start')}",
            flush=True,
        )
    else:
        arrays = create_arrays(config, int(source_audit["rows"]))
        state = {
            "status": "building",
            "dataset_version": config.prepared_dataset_version,
            "representation_sha256": representation_sha256,
            "source_rows": int(source_audit["rows"]),
            "row_offset": 0,
            "next_month_start": config.train_start,
            "context_rows": 0,
            "context_slots": 0,
            "causal_reaction_slots": 0,
        }
        write_json_atomic(
            manifest_path,
            {
                "status": "building",
                "dataset_version": config.prepared_dataset_version,
                "representation_sha256": representation_sha256,
                "rows": int(source_audit["rows"]),
            },
        )
        write_json_atomic(state_path, state)
    session_index = load_calendar_index(client, config)
    state = populate(
        client,
        config,
        arrays,
        source_audit=source_audit,
        representation_sha256=representation_sha256,
        state=state,
        session_index=session_index,
    )
    manifest = final_audit(
        config,
        arrays,
        source_audit=source_audit,
        representation_sha256=representation_sha256,
        state=state,
    )
    _flush(arrays)
    write_json_atomic(manifest_path, manifest)
    if state_path.exists():
        state_path.unlink()
    print(
        f"COMPLETED | rows={manifest['rows']:,} context_rows={manifest['context_rows']:,} "
        f"context_slots={manifest['context_slots']:,} "
        f"representation={representation_sha256}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
