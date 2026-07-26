from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
import signal
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v15.prepared import close_arrays as close_v15_arrays
from research.news_reaction_model.v15.prepared import open_arrays as open_v15_arrays
from research.news_reaction_model.v17.prepare_targets import (
    CancellationController,
    EMPTY_EVENT_ROWS,
    event_rows_for_tickers,
    load_split_dates,
    session_days_between,
    summarize_events,
)
from research.news_reaction_model.v18 import (
    DATASET_VERSION,
    EPISODE_CONTRACT_VERSION,
    MODEL_VERSION,
    TARGET_VERSION,
)
from research.news_reaction_model.v18.config import LoaderConfig
from research.news_reaction_model.v18.episode_contract import (
    CONTEXT_SIZE,
    ArticleSignals,
    Classification,
    NodeRole,
    RootFamily,
    classify_article,
    context_static_features,
    current_episode_features,
    episode_id,
    related_material_update,
)
from research.news_reaction_model.v18.prepared import (
    ARRAY_FILES,
    BUILD_STATE_FILE,
    MANIFEST_FILE,
    THRESHOLDS_FILE,
    close_arrays,
    create_arrays,
    expected_dtypes,
    expected_shapes,
    write_json_atomic,
)
from research.news_reaction_model.v18.targets import (
    RAW_METRIC_NAMES,
    TargetThresholds,
    classify,
    fit_thresholds,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
UTC = dt.timezone.utc
EASTERN = ZoneInfo("America/New_York")


def q(value: Any) -> str:
    return sql_string(str(value))


def qi(value: str) -> str:
    return quote_ident(value)


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def timestamp_us(value: dt.datetime) -> int:
    return int(round(value.timestamp() * 1_000_000))


def decode(value: Any) -> str:
    return bytes(value).rstrip(b"\x00").decode("utf-8")


def month_ranges(start: str, end_exclusive: str) -> list[tuple[dt.date, dt.date]]:
    lower = dt.date.fromisoformat(start)
    upper = dt.date.fromisoformat(end_exclusive)
    cursor = lower.replace(day=1)
    result: list[tuple[dt.date, dt.date]] = []
    while cursor < upper:
        following = (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        result.append((max(cursor, lower), min(following, upper)))
        cursor = following
    return result


def parse_json_each_row(text: str) -> Iterator[dict[str, Any]]:
    for line in text.splitlines():
        if line.strip():
            yield json.loads(line)


def calendar_sessions(client: ClickHouseHttpClient, config: LoaderConfig) -> list[dt.date]:
    text = client.execute(f"""
SELECT calendar_date
FROM {qi(config.news_database)}.{qi(config.reaction_calendar_table)} FINAL
WHERE calendar_version = {q(config.reaction_calendar_version)}
  AND calendar_date >= toDate({q(config.train_start)}) - 7
  AND calendar_date < toDate({q(config.validation_end_exclusive)}) + 14
ORDER BY calendar_date
FORMAT TabSeparatedRaw
""")
    values = [dt.date.fromisoformat(line.strip()) for line in text.splitlines() if line.strip()]
    if not values or values != sorted(set(values)):
        raise RuntimeError("V18 exchange calendar is empty, duplicated, or unordered.")
    return values


def extended_close(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(20, 0), EASTERN).astimezone(UTC)


def expiry_after_material(
    published: dt.datetime,
    sessions: Sequence[dt.date],
    count: int,
) -> dt.datetime:
    if count <= 0:
        raise ValueError("Episode inactivity sessions must be positive.")
    local_day = published.astimezone(EASTERN).date()
    first_subsequent = bisect.bisect_right(sessions, local_day)
    index = first_subsequent + count - 1
    if index >= len(sessions):
        raise RuntimeError(f"Exchange calendar does not cover V18 expiry after {local_day}.")
    return extended_close(sessions[index])


def session_position(value: dt.datetime, sessions: Sequence[dt.date]) -> int:
    local_day = value.astimezone(EASTERN).date()
    return bisect.bisect_left(sessions, local_day)


def source_identity_index(
    arrays: Mapping[str, np.ndarray],
) -> dict[tuple[str, str, int], int]:
    result: dict[tuple[str, str, int], int] = {}
    for index in range(int(arrays["published_at_us"].shape[0])):
        key = (
            decode(arrays["canonical_news_id"][index]),
            decode(arrays["ticker"][index]),
            int(arrays["published_at_us"][index]),
        )
        if key in result:
            raise RuntimeError(f"V15 contains duplicate identity {key}.")
        result[key] = index
    return result


def anchor_prices(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
) -> dict[tuple[str, str, int], float]:
    text = client.execute(f"""
SELECT
  canonical_news_id,
  ticker,
  toUnixTimestamp64Micro(published_at_utc),
  arrayFirst(x -> isFinite(x) AND x > 0, anchor_prices)
FROM {qi(config.certified_database)}.{qi(config.certified_table)} FINAL
WHERE dataset_version = {q(config.certified_version)}
  AND published_at_utc >= toDateTime64({q(config.train_start)}, 9, 'UTC')
  AND published_at_utc < toDateTime64({q(config.validation_end_exclusive)}, 9, 'UTC')
  AND arrayExists(x -> isFinite(x) AND x > 0, anchor_prices)
ORDER BY published_at_utc, ticker, canonical_news_id
FORMAT TabSeparatedRaw
""")
    result: dict[tuple[str, str, int], float] = {}
    for line in text.splitlines():
        if not line:
            continue
        news_id, ticker, published_us, price = line.split("\t")
        key = (news_id, ticker, int(published_us))
        value = float(price)
        if key in result and not math.isclose(result[key], value, rel_tol=0, abs_tol=1e-9):
            raise RuntimeError(f"Conflicting certified anchors for {key}.")
        result[key] = value
    return result


def metadata_sql(
    config: LoaderConfig,
    start: dt.date,
    end: dt.date,
) -> str:
    return f"""
WITH
links AS
(
  SELECT
    canonical_news_id,
    ticker,
    published_at_utc,
    max(ticker_count) AS ticker_count
  FROM {qi(config.news_database)}.{qi(config.ticker_link_table)} FINAL
  WHERE published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
    AND published_at_utc < toDateTime64({q(end)}, 9, 'UTC')
  GROUP BY canonical_news_id, ticker, published_at_utc
),
relevance AS
(
  SELECT
    canonical_news_id,
    ticker,
    published_at_utc,
    any(relevance_class) AS relevance_class
  FROM {qi(config.news_database)}.{qi(config.relevance_table)} FINAL
  WHERE relevance_version = {q(config.relevance_version)}
    AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
    AND published_at_utc < toDateTime64({q(end)}, 9, 'UTC')
  GROUP BY canonical_news_id, ticker, published_at_utc
),
semantic AS
(
  SELECT
    canonical_news_id,
    ticker,
    published_at_utc,
    arraySort(groupUniqArray(family)) AS semantic_families
  FROM {qi(config.news_database)}.{qi(config.semantic_table)} FINAL
  WHERE extraction_version = {q(config.semantic_version)}
    AND published_at_utc >= toDateTime64({q(start)}, 9, 'UTC')
    AND published_at_utc < toDateTime64({q(end)}, 9, 'UTC')
  GROUP BY canonical_news_id, ticker, published_at_utc
)
SELECT
  l.canonical_news_id AS canonical_news_id,
  l.ticker AS ticker,
  toString(l.published_at_utc) AS published_at_utc,
  toUnixTimestamp64Micro(l.published_at_utc) AS published_at_us,
  l.ticker_count,
  n.title,
  n.author,
  n.channels,
  n.provider_tags,
  ifNull(s.semantic_families, []) AS semantic_families,
  ifNull(r.relevance_class, 'ticker_related') AS relevance_class,
  n.text_hash,
  n.has_body
FROM links AS l
INNER JOIN {qi(config.news_database)}.{qi(config.normalized_news_table)} AS n FINAL
  ON n.canonical_news_id = l.canonical_news_id
 AND n.published_at_utc = l.published_at_utc
LEFT JOIN relevance AS r
  ON r.canonical_news_id = l.canonical_news_id
 AND r.ticker = l.ticker
 AND r.published_at_utc = l.published_at_utc
LEFT JOIN semantic AS s
  ON s.canonical_news_id = l.canonical_news_id
 AND s.ticker = l.ticker
 AND s.published_at_utc = l.published_at_utc
ORDER BY l.published_at_utc, l.ticker, l.canonical_news_id
SETTINGS max_threads={int(config.max_threads_per_query)},
         max_memory_usage={q(config.max_memory_usage)}
FORMAT JSONEachRow
"""


@dataclass(slots=True)
class Article:
    news_id: str
    ticker: str
    published: dt.datetime
    published_text: str
    ticker_count: int
    signals: ArticleSignals
    classification: Classification
    source_index: int | None
    anchor_price: float | None
    publication_session: str


@dataclass(slots=True)
class PlannedRow:
    source_index: int
    episode_id: str
    role: NodeRole
    root_family: RootFamily
    node_position: int
    current_features: np.ndarray
    context_source_indices: list[int]
    context_row_indices: list[int]
    context_static: list[np.ndarray]
    target_start: dt.datetime
    target_end: dt.datetime
    anchor_price: float
    ticker: str


@dataclass(slots=True)
class ActiveEpisode:
    episode_id: str
    ticker: str
    root_family: RootFamily
    root_published: dt.datetime
    root_publication_session: str
    root_session_position: int
    expiry: dt.datetime
    last_material: dt.datetime
    last_material_signals: ArticleSignals
    last_material_family: RootFamily
    duplicate_keys: set[str]
    selected_rows: list[int]
    selected_articles: list[Article]
    node_position: int = 0
    unembedded_nodes: int = 0
    last_selected_row: int | None = None


def article_from_row(
    row: Mapping[str, Any],
    *,
    source_indices: Mapping[tuple[str, str, int], int],
    anchors: Mapping[tuple[str, str, int], float],
    v15: Mapping[str, np.ndarray],
) -> Article:
    published = parse_utc(str(row["published_at_utc"]))
    published_us = int(row["published_at_us"])
    key = (str(row["canonical_news_id"]), str(row["ticker"]), published_us)
    signals = ArticleSignals(
        title=str(row.get("title") or ""),
        author=str(row.get("author") or ""),
        channels=tuple(str(value) for value in row.get("channels") or ()),
        tags=tuple(str(value) for value in row.get("provider_tags") or ()),
        semantic_families=tuple(str(value) for value in row.get("semantic_families") or ()),
        relevance_class=str(row.get("relevance_class") or "ticker_related"),
        text_hash=str(row.get("text_hash") or ""),
        has_body=bool(row.get("has_body")),
    )
    source_index = source_indices.get(key)
    publication_session = (
        decode(v15["publication_session"][source_index])
        if source_index is not None
        else "closed"
    )
    return Article(
        news_id=key[0],
        ticker=key[1],
        published=published,
        published_text=str(row["published_at_utc"]),
        ticker_count=int(row["ticker_count"]),
        signals=signals,
        classification=classify_article(signals),
        source_index=source_index,
        anchor_price=anchors.get(key),
        publication_session=publication_session,
    )


def _can_start(article: Article, config: LoaderConfig) -> bool:
    return bool(
        article.ticker_count == 1
        and article.classification.root_eligible
        and article.source_index is not None
        and article.anchor_price is not None
        and 0 < article.anchor_price < config.root_max_price
    )


def _start_episode(
    article: Article,
    *,
    config: LoaderConfig,
    sessions: Sequence[dt.date],
) -> ActiveEpisode:
    return ActiveEpisode(
        episode_id=episode_id(article.ticker, article.news_id, article.published_text),
        ticker=article.ticker,
        root_family=article.classification.root_family,
        root_published=article.published,
        root_publication_session=article.publication_session,
        root_session_position=session_position(article.published, sessions),
        expiry=expiry_after_material(
            article.published, sessions, config.episode_inactivity_sessions
        ),
        last_material=article.published,
        last_material_signals=article.signals,
        last_material_family=article.classification.root_family,
        duplicate_keys={article.classification.duplicate_key},
        selected_rows=[],
        selected_articles=[],
    )


def _append_modeled_row(
    article: Article,
    active: ActiveEpisode,
    planned: list[PlannedRow],
    *,
    role: NodeRole,
    sessions: Sequence[dt.date],
) -> bool:
    if article.source_index is None or article.anchor_price is None or article.anchor_price <= 0:
        active.unembedded_nodes += 1
        return False
    current_row = len(planned)
    context_row_indices = active.selected_rows[-CONTEXT_SIZE:]
    context_articles = active.selected_articles[-CONTEXT_SIZE:]
    context_source_indices = [item.source_index for item in context_articles]
    if any(value is None for value in context_source_indices):
        raise AssertionError("Modeled context article lacks a V15 source index.")
    static: list[np.ndarray] = []
    for prior_position, prior in zip(context_row_indices, context_articles, strict=True):
        static.append(
            context_static_features(
                role=planned[prior_position].role,
                gap_minutes=(article.published - prior.published).total_seconds() / 60.0,
                root_age_sessions=max(
                    0,
                    session_position(prior.published, sessions)
                    - active.root_session_position,
                ),
                node_distance=active.node_position - planned[prior_position].node_position,
                same_publication_session=(
                    prior.publication_session == article.publication_session
                    and prior.published.astimezone(EASTERN).date()
                    == article.published.astimezone(EASTERN).date()
                ),
                intervening_unembedded_count=active.unembedded_nodes,
            )
        )
    planned.append(
        PlannedRow(
            source_index=article.source_index,
            episode_id=active.episode_id,
            role=role,
            root_family=active.root_family,
            node_position=active.node_position,
            current_features=current_episode_features(
                role=role,
                root_family=active.root_family,
                node_position=active.node_position,
                root_age_sessions=max(
                    0,
                    session_position(article.published, sessions)
                    - active.root_session_position,
                ),
                minutes_since_material=(
                    article.published - active.last_material
                ).total_seconds()
                / 60.0,
                same_session_as_root=(
                    article.published.astimezone(EASTERN).date()
                    == active.root_published.astimezone(EASTERN).date()
                    and article.publication_session
                    == active.root_publication_session
                ),
                unembedded_nodes_before=active.unembedded_nodes,
            ),
            context_source_indices=[int(value) for value in context_source_indices],
            context_row_indices=list(context_row_indices),
            context_static=static,
            target_start=article.published,
            target_end=active.expiry,
            anchor_price=float(article.anchor_price),
            ticker=article.ticker,
        )
    )
    active.selected_rows.append(current_row)
    active.selected_articles.append(article)
    active.last_selected_row = current_row
    active.unembedded_nodes = 0
    return True


def consume_article(
    article: Article,
    *,
    active_by_ticker: dict[str, ActiveEpisode],
    planned: list[PlannedRow],
    counts: defaultdict[str, int],
    config: LoaderConfig,
    sessions: Sequence[dt.date],
) -> None:
    """Apply one chronologically ordered ticker link to the episode state."""
    counts["ticker_links"] += 1
    active = active_by_ticker.get(article.ticker)
    if active is not None and active.last_selected_row is not None:
        planned[active.last_selected_row].target_end = min(
            planned[active.last_selected_row].target_end,
            article.published,
        )
    if active is not None and article.published >= active.expiry:
        active_by_ticker.pop(article.ticker, None)
        active = None
    if article.ticker_count != 1:
        counts["multi_ticker_censors"] += 1
        return
    counts["single_ticker_articles"] += 1
    if active is None:
        if not _can_start(article, config):
            counts["orphan_or_ineligible"] += 1
            return
        active = _start_episode(article, config=config, sessions=sessions)
        active_by_ticker[article.ticker] = active
        role = NodeRole.ROOT
        counts["episodes"] += 1
    else:
        duplicate = article.classification.duplicate_key in active.duplicate_keys
        if duplicate:
            role = NodeRole.DUPLICATE
            counts["duplicate_nodes"] += 1
        elif (
            article.classification.root_family is RootFamily.ANALYST
            and active.root_family is not RootFamily.ANALYST
            and (article.published - active.last_material) <= dt.timedelta(hours=6)
        ):
            role = NodeRole.ANALYSIS
            counts["analysis_nodes"] += 1
        elif article.classification.material:
            related = related_material_update(
                article.signals,
                active.last_material_signals,
                current_family=article.classification.root_family,
                previous_family=active.last_material_family,
            )
            if not related:
                active_by_ticker.pop(article.ticker, None)
                if not _can_start(article, config):
                    counts["independent_ineligible_roots"] += 1
                    return
                active = _start_episode(article, config=config, sessions=sessions)
                active_by_ticker[article.ticker] = active
                role = NodeRole.ROOT
                counts["episodes"] += 1
            else:
                role = NodeRole.MATERIAL_UPDATE
                active.last_material = article.published
                active.last_material_signals = article.signals
                active.last_material_family = article.classification.root_family
                active.expiry = expiry_after_material(
                    article.published, sessions, config.episode_inactivity_sessions
                )
                counts["material_updates"] += 1
        else:
            role = article.classification.role
            counts["reactive_nodes"] += 1
    active.node_position += int(role is not NodeRole.ROOT)
    active.duplicate_keys.add(article.classification.duplicate_key)
    modeled = _append_modeled_row(
        article, active, planned, role=role, sessions=sessions
    )
    if modeled:
        counts["modeled_rows"] += 1
    else:
        counts["episode_nodes_without_v15_inputs"] += 1


def plan_episodes(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    v15: Mapping[str, np.ndarray],
    source_indices: Mapping[tuple[str, str, int], int],
    anchors: Mapping[tuple[str, str, int], float],
    sessions: Sequence[dt.date],
) -> tuple[list[PlannedRow], dict[str, int]]:
    active_by_ticker: dict[str, ActiveEpisode] = {}
    planned: list[PlannedRow] = []
    counts: defaultdict[str, int] = defaultdict(int)
    started = time.perf_counter()
    ranges = month_ranges(config.train_start, config.validation_end_exclusive)
    for month_index, (start, end) in enumerate(ranges):
        month_rows = 0
        text = client.execute(metadata_sql(config, start, end))
        for raw in parse_json_each_row(text):
            article = article_from_row(
                raw,
                source_indices=source_indices,
                anchors=anchors,
                v15=v15,
            )
            month_rows += 1
            consume_article(
                article,
                active_by_ticker=active_by_ticker,
                planned=planned,
                counts=counts,
                config=config,
                sessions=sessions,
            )
        elapsed = time.perf_counter() - started
        remaining = len(ranges) - month_index - 1
        eta = elapsed / max(month_index + 1, 1) * remaining
        print(
            f"PLAN {month_index + 1}/{len(ranges)} {start:%Y-%m} "
            f"links={month_rows:,} rows={len(planned):,} episodes={counts['episodes']:,} "
            f"elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
            flush=True,
        )
    for active in active_by_ticker.values():
        if active.last_selected_row is not None:
            planned[active.last_selected_row].target_end = min(
                planned[active.last_selected_row].target_end,
                active.expiry,
            )
    return planned, dict(counts)


def write_plan(
    config: LoaderConfig,
    planned: Sequence[PlannedRow],
) -> dict[str, np.ndarray]:
    arrays = create_arrays(config, len(planned))
    for index, row in enumerate(planned):
        arrays["source_index"][index] = row.source_index
        arrays["episode_id"][index] = row.episode_id.encode("ascii")
        arrays["node_role"][index] = int(row.role)
        arrays["root_family"][index] = int(row.root_family)
        arrays["node_position"][index] = row.node_position
        arrays["current_episode_features"][index] = row.current_features
        arrays["target_start_us"][index] = timestamp_us(row.target_start)
        arrays["target_end_us"][index] = timestamp_us(row.target_end)
        arrays["anchor_price"][index] = row.anchor_price
        for slot, (source_index, context_row, static) in enumerate(
            zip(
                row.context_source_indices,
                row.context_row_indices,
                row.context_static,
                strict=True,
            )
        ):
            arrays["context_source_indices"][index, slot] = source_index
            arrays["context_row_indices"][index, slot] = context_row
            arrays["context_static"][index, slot] = static
            arrays["context_mask"][index, slot] = True
    flush_arrays(arrays)
    return arrays


def flush_arrays(arrays: Mapping[str, np.ndarray]) -> None:
    for array in arrays.values():
        flush = getattr(array, "flush", None)
        if flush is not None:
            flush()


def datetime_from_us(value: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(value / 1_000_000, UTC)


def process_interval_batch(
    *,
    config: LoaderConfig,
    v15: Mapping[str, np.ndarray],
    arrays: Mapping[str, np.ndarray],
    sessions: list[dt.date],
    split_dates: Mapping[str, frozenset[dt.date]],
    items: list[tuple[str, list[int]]],
    cancellation: CancellationController,
) -> tuple[list[tuple[int, np.ndarray, bool]], int, int]:
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    required_by_day: dict[dt.date, set[str]] = defaultdict(set)
    work_by_end_day: dict[
        dt.date,
        list[tuple[int, str, tuple[dt.date, ...], dt.datetime, dt.datetime, float]],
    ] = defaultdict(list)
    results = {
        index: (np.full(len(RAW_METRIC_NAMES), np.nan, np.float32), False)
        for _ticker, indices in items
        for index in indices
    }
    for ticker, indices in items:
        split_days = split_dates.get(ticker, ())
        for index in indices:
            start = datetime_from_us(int(arrays["target_start_us"][index]))
            end = datetime_from_us(int(arrays["target_end_us"][index]))
            if end <= start:
                continue
            start_day = start.astimezone(EASTERN).date()
            end_day = end.astimezone(EASTERN).date()
            if any(start_day <= value <= end_day for value in split_days):
                continue
            selected_days = session_days_between(sessions, start_day, end_day)
            if not selected_days:
                continue
            for day in selected_days:
                required_by_day[day].add(ticker)
            work_by_end_day[selected_days[-1]].append(
                (
                    index,
                    ticker,
                    selected_days,
                    start,
                    end,
                    float(arrays["anchor_price"][index]),
                )
            )
    cache: OrderedDict[dt.date, dict[str, np.ndarray]] = OrderedDict()
    queries = 0
    event_rows = 0
    absolute_caches: dict[str, dict[tuple[int, int], dict[str, float]]] = {
        ticker: {} for ticker, _indices in items
    }
    for day in sorted(required_by_day):
        cancellation.raise_if_requested()
        requested = sorted(required_by_day[day])
        loaded = event_rows_for_tickers(
            client, config, requested, day, cancellation=cancellation
        )
        queries += 1
        event_rows += sum(int(value.shape[0]) for value in loaded.values())
        cache[day] = loaded
        for index, ticker, selected_days, start, end, anchor in work_by_end_day.get(day, ()):
            missing = [value for value in selected_days if value not in cache]
            if missing:
                raise RuntimeError(f"V18 event cache lost required sessions: {missing}.")
            raw16, valid = summarize_events(
                [cache[value].get(ticker, EMPTY_EVENT_ROWS) for value in selected_days],
                start=start,
                end=end,
                anchor_price=anchor,
                minimum_observations=3,
                absolute_cache=absolute_caches[ticker],
            )
            if valid:
                raw14 = np.delete(raw16, [12, 13]).astype(np.float32, copy=False)
                if raw14.shape != (len(RAW_METRIC_NAMES),) or not np.isfinite(raw14).all():
                    raise RuntimeError(f"V18 target metrics are invalid for row {index}.")
                results[index] = (raw14, True)
        while len(cache) > config.episode_inactivity_sessions + 2:
            cache.popitem(last=False)
    return (
        [(index, *results[index]) for _ticker, indices in items for index in indices],
        queries,
        event_rows,
    )


def stable_batches(
    v15: Mapping[str, np.ndarray],
    arrays: Mapping[str, np.ndarray],
    tickers_per_query: int,
) -> list[list[tuple[str, list[int]]]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for row_index, source_index in enumerate(np.asarray(arrays["source_index"])):
        grouped[decode(v15["ticker"][int(source_index)])].append(row_index)
    items = sorted(grouped.items())
    return [
        items[offset : offset + tickers_per_query]
        for offset in range(0, len(items), tickers_per_query)
    ]


def build_targets(
    config: LoaderConfig,
    v15: Mapping[str, np.ndarray],
    arrays: Mapping[str, np.ndarray],
    sessions: list[dt.date],
    split_dates: Mapping[str, frozenset[dt.date]],
    state: dict[str, Any],
) -> dict[str, Any]:
    batches = stable_batches(v15, arrays, config.tickers_per_query)
    completed = {int(value) for value in state.get("completed_target_batches", ())}
    pending = [(index, batch) for index, batch in enumerate(batches) if index not in completed]
    cancellation = CancellationController()
    previous_handler = signal.getsignal(signal.SIGINT)

    def handle_interrupt(_signum: int, _frame: Any) -> None:
        if not cancellation.requested:
            print("INTERRUPT requested; cancelling V18 queries and joining workers...", flush=True)
            cancellation.request()
            cancellation.cancel_active_queries(
                ClickHouseHttpClient(
                    default_clickhouse_url(),
                    default_clickhouse_user(),
                    default_clickhouse_password(),
                )
            )

    signal.signal(signal.SIGINT, handle_interrupt)
    started = time.perf_counter()
    queries = int(state.get("target_queries") or 0)
    events = int(state.get("target_event_rows") or 0)
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, config.workers),
            thread_name_prefix="v18-target",
        ) as executor:
            futures = {
                executor.submit(
                    process_interval_batch,
                    config=config,
                    v15=v15,
                    arrays=arrays,
                    sessions=sessions,
                    split_dates=split_dates,
                    items=batch,
                    cancellation=cancellation,
                ): batch_index
                for batch_index, batch in pending
            }
            for future in concurrent.futures.as_completed(futures):
                batch_index = futures[future]
                if cancellation.requested:
                    break
                rows, batch_queries, batch_events = future.result()
                for row_index, raw, valid in rows:
                    arrays["raw_metrics"][row_index] = raw
                    arrays["target_mask"][row_index] = valid
                completed.add(batch_index)
                queries += batch_queries
                events += batch_events
                flush_arrays(arrays)
                state = {
                    **state,
                    "status": "target_building",
                    "completed_target_batches": sorted(completed),
                    "target_queries": queries,
                    "target_event_rows": events,
                }
                write_json_atomic(config.prepared_dataset_root / BUILD_STATE_FILE, state)
                elapsed = time.perf_counter() - started
                rate = len(completed) / max(elapsed, 1e-9)
                eta = (len(batches) - len(completed)) / max(rate, 1e-9)
                print(
                    f"TARGET {len(completed)}/{len(batches)} "
                    f"queries={queries:,} events={events:,} "
                    f"valid={int(np.count_nonzero(arrays['target_mask'])):,} "
                    f"elapsed={elapsed / 60:.1f}m eta={eta / 60:.1f}m",
                    flush=True,
                )
        if cancellation.requested:
            raise KeyboardInterrupt
    finally:
        signal.signal(signal.SIGINT, previous_handler)
    return state


def classify_targets(
    config: LoaderConfig,
    v15: Mapping[str, np.ndarray],
    arrays: Mapping[str, np.ndarray],
) -> TargetThresholds:
    source_indices = np.asarray(arrays["source_index"], dtype=np.int64)
    published = np.asarray(v15["published_at_us"][source_indices], dtype=np.int64)
    boundary = timestamp_us(parse_utc(config.train_end_exclusive))
    train = published < boundary
    thresholds = fit_thresholds(
        np.asarray(arrays["raw_metrics"]),
        np.asarray(arrays["target_mask"]) & train,
    )
    for index in np.flatnonzero(np.asarray(arrays["target_mask"])):
        direction, path, flow, regression = classify(
            arrays["raw_metrics"][index], thresholds
        )
        arrays["direction"][index] = int(direction)
        arrays["path"][index] = int(path)
        arrays["flow"][index] = int(flow)
        arrays["regression_targets"][index] = regression
    flush_arrays(arrays)
    write_json_atomic(
        config.prepared_dataset_root / THRESHOLDS_FILE, thresholds.as_dict()
    )
    return thresholds


def representation_hash(
    config: LoaderConfig,
    *,
    v15_manifest: Mapping[str, Any],
    rows: int,
) -> str:
    payload = {
        "dataset_version": DATASET_VERSION,
        "episode_contract": EPISODE_CONTRACT_VERSION,
        "target_version": TARGET_VERSION,
        "v15_representation_sha256": v15_manifest["representation_sha256"],
        "rows": rows,
        "root_max_price": config.root_max_price,
        "episode_inactivity_sessions": config.episode_inactivity_sessions,
        "context_size": config.context_size,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def audit(
    config: LoaderConfig,
    v15: Mapping[str, np.ndarray],
    arrays: Mapping[str, np.ndarray],
    v15_manifest: Mapping[str, Any],
    plan_counts: Mapping[str, int],
    thresholds: TargetThresholds,
) -> dict[str, Any]:
    rows = int(arrays["source_index"].shape[0])
    shapes, dtypes = expected_shapes(rows), expected_dtypes()
    for name, array in arrays.items():
        if array.shape != shapes[name] or array.dtype != dtypes[name]:
            raise RuntimeError(f"V18 {name} shape/dtype audit failed.")
    source = np.asarray(arrays["source_index"], dtype=np.int64)
    if np.any(source < 0) or np.any(source >= int(v15_manifest["rows"])):
        raise RuntimeError("V18 source indices are out of bounds.")
    context_rows = np.asarray(arrays["context_row_indices"], dtype=np.int64)
    context_sources = np.asarray(arrays["context_source_indices"], dtype=np.int64)
    context_mask = np.asarray(arrays["context_mask"], dtype=np.bool_)
    current = np.arange(rows, dtype=np.int64)[:, None]
    if np.any(context_mask & ((context_rows < 0) | (context_rows >= current))):
        raise RuntimeError("V18 context contains padding, current, or future rows.")
    if np.any((~context_mask) & ((context_rows != -1) | (context_sources != -1))):
        raise RuntimeError("V18 context padding contract failed.")
    if not np.isfinite(np.asarray(arrays["current_episode_features"])).all():
        raise RuntimeError("V18 current episode features contain non-finite values.")
    if not np.isfinite(np.asarray(arrays["context_static"])).all():
        raise RuntimeError("V18 context static features contain non-finite values.")
    starts = np.asarray(arrays["target_start_us"])
    ends = np.asarray(arrays["target_end_us"])
    if np.any(ends <= starts):
        raise RuntimeError("V18 contains an empty or reversed target interval.")
    mask = np.asarray(arrays["target_mask"], dtype=np.bool_)
    raw = np.asarray(arrays["raw_metrics"])
    if not np.isfinite(raw[mask]).all() or np.any(raw[mask, 0] <= 0):
        raise RuntimeError("V18 populated targets are invalid.")
    if np.any(arrays["direction"][~mask] != -1) or np.any(
        arrays["path"][~mask] != -1
    ) or np.any(arrays["flow"][~mask] != -1):
        raise RuntimeError("V18 masked targets contain classes.")
    if not np.isfinite(arrays["regression_targets"][mask]).all():
        raise RuntimeError("V18 populated regression targets are invalid.")
    published = np.asarray(v15["published_at_us"][source], dtype=np.int64)
    train_boundary = timestamp_us(parse_utc(config.train_end_exclusive))
    validation_start = timestamp_us(parse_utc(config.validation_start))
    validation_end = timestamp_us(parse_utc(config.validation_end_exclusive))
    train_rows = int(np.count_nonzero(published < train_boundary))
    validation_rows = int(
        np.count_nonzero((published >= validation_start) & (published < validation_end))
    )
    result = {
        "status": "complete",
        "model_version": MODEL_VERSION,
        "dataset_version": DATASET_VERSION,
        "episode_contract_version": EPISODE_CONTRACT_VERSION,
        "target_version": TARGET_VERSION,
        "rows": rows,
        "train_rows": train_rows,
        "validation_rows": validation_rows,
        "valid_targets": int(mask.sum()),
        "episodes": len(set(decode(value) for value in arrays["episode_id"])),
        "plan_counts": dict(plan_counts),
        "thresholds": thresholds.as_dict(),
        "v15_rows": int(v15_manifest["rows"]),
        "v15_representation_sha256": v15_manifest["representation_sha256"],
        "representation_sha256": representation_hash(
            config, v15_manifest=v15_manifest, rows=rows
        ),
        "array_files": ARRAY_FILES,
        "loader": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars_from_slots(config).items()
        },
    }
    if train_rows <= 0 or validation_rows <= 0 or int(mask.sum()) <= 0:
        raise RuntimeError(f"V18 split/target audit failed: {result}.")
    return result


def vars_from_slots(value: Any) -> dict[str, Any]:
    return {
        field.name: getattr(value, field.name)
        for field in value.__dataclass_fields__.values()
    }


def clear_outputs(config: LoaderConfig) -> None:
    for filename in (*ARRAY_FILES.values(), MANIFEST_FILE, BUILD_STATE_FILE, THRESHOLDS_FILE):
        path = config.prepared_dataset_root / filename
        if path.exists():
            path.unlink()


def build_parser() -> argparse.ArgumentParser:
    defaults = LoaderConfig()
    parser = argparse.ArgumentParser(
        description="Build V18 single-ticker episodes and exact interval targets."
    )
    parser.add_argument("--prepared-root", default=str(defaults.prepared_dataset_root))
    parser.add_argument("--v15-root", default=str(defaults.v15_prepared_root))
    parser.add_argument("--workers", type=int, default=defaults.workers)
    parser.add_argument("--tickers-per-query", type=int, default=defaults.tickers_per_query)
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
        v15_prepared_root=Path(args.v15_root),
        workers=max(1, args.workers),
        tickers_per_query=max(1, args.tickers_per_query),
        max_threads_per_query=max(1, args.max_threads_per_query),
        max_memory_usage=args.max_memory_usage,
    )
    print(
        f"V18 BUILD | workers={config.workers} tickers/query={config.tickers_per_query} "
        f"ClickHouse threads/query={config.max_threads_per_query}",
        flush=True,
    )
    if not args.execute:
        print(
            "PREFLIGHT ONLY | add --execute after V15 and certified-target authorities "
            "are complete.",
            flush=True,
        )
        return 0
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    if args.restart:
        clear_outputs(config)
    manifest_path = config.prepared_dataset_root / MANIFEST_FILE
    if manifest_path.exists() and not args.restart:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("status") == "complete":
            print(
                f"ALREADY COMPLETE | rows={existing['rows']:,} "
                f"valid={existing['valid_targets']:,}",
                flush=True,
            )
            return 0
    v15, v15_manifest = open_v15_arrays(config.v15_config())
    arrays: dict[str, np.ndarray] | None = None
    try:
        sessions = calendar_sessions(client, config)
        state_path = config.prepared_dataset_root / BUILD_STATE_FILE
        if state_path.exists() and not args.restart:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            rows = int(state["rows"])
            arrays = {
                name: np.load(
                    config.prepared_dataset_root / filename,
                    mmap_mode="r+",
                    allow_pickle=False,
                )
                for name, filename in ARRAY_FILES.items()
            }
            if any(arrays[name].shape != expected_shapes(rows)[name] for name in arrays):
                raise RuntimeError("V18 resumable arrays do not match build state.")
            plan_counts = dict(state.get("plan_counts") or {})
            print(
                f"RESUME | rows={rows:,} target_batches="
                f"{len(state.get('completed_target_batches', ())):,}",
                flush=True,
            )
        else:
            indices = source_identity_index(v15)
            anchors = anchor_prices(client, config)
            certified_v15_rows = len(set(indices) & set(anchors))
            missing_anchor_authority = len(indices) - certified_v15_rows
            if certified_v15_rows < int(0.95 * len(indices)):
                raise RuntimeError(
                    "Certified target authority does not cover at least 95% of V15 "
                    f"identities ({certified_v15_rows:,}/{len(indices):,}). Complete "
                    "research.news_reaction_model.certified_targets_v1.run_build first; "
                    "V18 will not build against a partial authority."
                )
            print(
                f"AUTHORITIES | V15={len(indices):,} certified_anchors={len(anchors):,} "
                f"V15_without_anchor={missing_anchor_authority:,}",
                flush=True,
            )
            planned, plan_counts = plan_episodes(
                client, config, v15, indices, anchors, sessions
            )
            if not planned:
                raise RuntimeError("V18 episode planning produced no modeled rows.")
            arrays = write_plan(config, planned)
            state = {
                "status": "planned",
                "dataset_version": DATASET_VERSION,
                "rows": len(planned),
                "plan_counts": plan_counts,
                "completed_target_batches": [],
                "target_queries": 0,
                "target_event_rows": 0,
            }
            write_json_atomic(state_path, state)
            write_json_atomic(
                manifest_path,
                {
                    "status": "building",
                    "dataset_version": DATASET_VERSION,
                    "rows": len(planned),
                },
            )
        split_dates = load_split_dates(client, config)
        state = build_targets(config, v15, arrays, sessions, split_dates, state)
        thresholds = classify_targets(config, v15, arrays)
        manifest = audit(
            config,
            v15,
            arrays,
            v15_manifest,
            plan_counts,
            thresholds,
        )
        write_json_atomic(manifest_path, manifest)
        if state_path.exists():
            state_path.unlink()
        print(
            f"COMPLETED | rows={manifest['rows']:,} episodes={manifest['episodes']:,} "
            f"valid_targets={manifest['valid_targets']:,} "
            f"representation={manifest['representation_sha256']}",
            flush=True,
        )
        return 0
    except KeyboardInterrupt:
        print("V18 build interrupted safely; completed target batches are resumable.", flush=True)
        return 130
    finally:
        if arrays is not None:
            close_arrays(arrays)
        close_v15_arrays(v15)


if __name__ == "__main__":
    raise SystemExit(main())
