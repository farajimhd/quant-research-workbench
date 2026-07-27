from __future__ import annotations

import argparse
import bisect
import datetime as dt
import hashlib
import html
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

import matplotlib
import numpy as np
import torch

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v15.stock_state import STOCK_STATE_NAMES
from research.news_reaction_model.v15.time_features import TIME_FEATURE_NAMES
from research.news_reaction_model.v17.prepare_targets import event_rows_for_tickers
from research.news_reaction_model.v18.episode_contract import (
    CONTEXT_STATIC_NAMES,
    CONTEXT_TARGET_NAMES,
    CURRENT_EPISODE_FEATURE_NAMES,
    ROLE_NAMES,
    ROOT_FAMILY_NAMES,
)
from research.news_reaction_model.v18.config import (
    LoaderConfig as SourceLoaderConfig,
)
from research.news_reaction_model.v18.prepare_data import calendar_sessions, q, qi
from research.news_reaction_model.v18.targets import (
    FLOW_NAMES,
    PATH_NAMES,
    RAW_METRIC_NAMES,
)
from research.news_reaction_model.v20.evaluate import _decode
from research.news_reaction_model.v21.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v21.data import PreparedEpisodeDataset
from research.news_reaction_model.v21.losses import signed_opportunity_torch
from research.news_reaction_model.v21.model import NewsReactionModelV21
from research.news_reaction_model.v21.targets import (
    DIRECTION_NAMES,
    TrainingStatistics,
)


UTC = dt.timezone.utc
EASTERN = ZoneInfo("America/New_York")
AUDIT_VERSION = "news_reaction_v21_episode_error_audit_v1"
ERROR_NAMES = (
    "false_upside",
    "false_downside",
    "missed_upside",
    "missed_downside",
    "reversed_upside",
    "reversed_downside",
)


@dataclass(frozen=True, slots=True)
class Prediction:
    row_index: int
    episode_id: str
    canonical_news_id: str
    ticker: str
    published_at_utc: str
    root_family: str
    role: str
    actual: int
    predicted: int
    probabilities: tuple[float, float, float]
    expected_return_pct: float
    expected_upside_pct: float
    expected_downside_pct: float
    signed_opportunity_pct: float

    @property
    def confidence(self) -> float:
        return max(self.probabilities)

    @property
    def error_type(self) -> str:
        return classify_error(self.predicted, self.actual)


@dataclass(frozen=True, slots=True)
class MinuteBar:
    timestamp_us: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create reproducible Markdown dossiers for stratified V21 "
            "false-positive and false-negative episodes."
        )
    )
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--prepared-root", type=Path, default=None)
    parser.add_argument("--v15-root", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    parser.add_argument("--samples", type=int, default=25)
    parser.add_argument(
        "--device", choices=("auto", "cpu", "cuda"), default="auto"
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def classify_error(predicted: int, actual: int) -> str:
    if predicted == actual:
        return "correct"
    if predicted == 1 and actual == 0:
        return "false_upside"
    if predicted == 2 and actual == 0:
        return "false_downside"
    if predicted == 0 and actual == 1:
        return "missed_upside"
    if predicted == 0 and actual == 2:
        return "missed_downside"
    if predicted == 1 and actual == 2:
        return "reversed_upside"
    if predicted == 2 and actual == 1:
        return "reversed_downside"
    raise ValueError(f"Invalid direction pair predicted={predicted}, actual={actual}.")


def _runtime_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable.")
    return torch.device(name)


def _checkpoint_loader(
    checkpoint: Mapping[str, Any],
    prepared_root: Path | None,
    v15_root: Path | None,
) -> LoaderConfig:
    loader = LoaderConfig(**checkpoint["loader_config"])
    if prepared_root is not None:
        loader.prepared_dataset_root = prepared_root
    if v15_root is not None:
        loader.v15_prepared_root = v15_root
    return loader


@torch.no_grad()
def collect_predictions(
    checkpoint_path: Path,
    *,
    prepared_root: Path | None,
    v15_root: Path | None,
    start: str,
    end_exclusive: str,
    device_name: str,
) -> tuple[
    list[Prediction],
    PreparedEpisodeDataset,
    NewsReactionModelV21,
    dict[str, Any],
]:
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if checkpoint.get("model_version") != "v21":
        raise RuntimeError(
            f"Expected V21 checkpoint, received {checkpoint.get('model_version')}."
        )
    loader = _checkpoint_loader(checkpoint, prepared_root, v15_root)
    statistics = TrainingStatistics.from_dict(checkpoint["training_statistics"])
    model = NewsReactionModelV21(
        ModelConfig(**checkpoint["model_config"]), statistics
    )
    device = _runtime_device(device_name)
    model.load_state_dict(checkpoint["model_state"])
    model.to(device).eval()
    dataset = PreparedEpisodeDataset(
        loader, start=start, end_exclusive=end_exclusive
    )
    predictions: list[Prediction] = []
    for batch in dataset.iter_batches():
        moved = batch.to(device)
        output = model(moved.x)
        probabilities = output.direction_probabilities.detach().cpu().numpy()
        predicted = probabilities.argmax(axis=1)
        opportunity = signed_opportunity_torch(batch).numpy()
        expected = output.expected_return.detach().cpu().numpy()
        expected_up = output.expected_up_return.detach().cpu().numpy()
        expected_down = output.expected_down_return.detach().cpu().numpy()
        for position, row_index in enumerate(batch.identity["prepared_row_index"]):
            row = int(row_index)
            predictions.append(
                Prediction(
                    row_index=row,
                    episode_id=str(batch.identity["episode_id"][position]),
                    canonical_news_id=str(
                        batch.identity["canonical_news_id"][position]
                    ),
                    ticker=str(batch.identity["ticker"][position]),
                    published_at_utc=str(
                        batch.identity["published_at_utc"][position]
                    ),
                    root_family=ROOT_FAMILY_NAMES[
                        int(dataset.arrays["root_family"][row])
                    ],
                    role=ROLE_NAMES[int(dataset.arrays["node_role"][row])],
                    actual=int(batch.direction[position]),
                    predicted=int(predicted[position]),
                    probabilities=tuple(
                        float(value) for value in probabilities[position]
                    ),
                    expected_return_pct=float(expected[position]),
                    expected_upside_pct=float(expected_up[position]),
                    expected_downside_pct=float(expected_down[position]),
                    signed_opportunity_pct=float(opportunity[position]),
                )
            )
    return predictions, dataset, model, checkpoint


def select_stratified_errors(
    predictions: Sequence[Prediction],
    *,
    count: int,
) -> list[Prediction]:
    requested = max(20, int(count))
    candidates = [
        row for row in predictions if row.error_type in ERROR_NAMES
    ]
    if len({row.episode_id for row in candidates}) < requested:
        raise RuntimeError(
            f"Only {len({row.episode_id for row in candidates})} erroneous "
            f"episodes are available; requested {requested}."
        )
    grouped: dict[tuple[str, str], list[Prediction]] = defaultdict(list)
    for row in candidates:
        grouped[(row.root_family, row.error_type)].append(row)
    for rows in grouped.values():
        rows.sort(
            key=lambda value: (
                -value.confidence,
                -abs(value.signed_opportunity_pct),
                value.published_at_utc,
                value.ticker,
            )
        )
    families = [
        name
        for name in ROOT_FAMILY_NAMES
        if any(key[0] == name for key in grouped)
    ]
    selected: list[Prediction] = []
    episodes: set[str] = set()
    offsets: Counter[tuple[str, str]] = Counter()
    while len(selected) < requested:
        progressed = False
        for family in families:
            for error in ERROR_NAMES:
                key = (family, error)
                rows = grouped.get(key, ())
                position = offsets[key]
                while position < len(rows) and rows[position].episode_id in episodes:
                    position += 1
                offsets[key] = position + 1
                if position >= len(rows):
                    continue
                row = rows[position]
                selected.append(row)
                episodes.add(row.episode_id)
                progressed = True
                if len(selected) >= requested:
                    break
            if len(selected) >= requested:
                break
        if not progressed:
            break
    if len(selected) < requested:
        remaining = sorted(
            (row for row in candidates if row.episode_id not in episodes),
            key=lambda value: (-value.confidence, value.root_family, value.error_type),
        )
        for row in remaining:
            selected.append(row)
            episodes.add(row.episode_id)
            if len(selected) >= requested:
                break
    selected.sort(
        key=lambda value: (
            value.root_family,
            value.error_type,
            -value.confidence,
        )
    )
    return selected


def _sql_news(
    loader: SourceLoaderConfig,
    canonical_news_ids: Sequence[str],
) -> str:
    values = ",".join(q(value) for value in sorted(set(canonical_news_ids)))
    return f"""
SELECT
  canonical_news_id,
  toString(published_at_utc) AS published_at_utc,
  title,
  teaser,
  normalized_full_text,
  author,
  url_domain,
  channels,
  provider_tags,
  tickers,
  content_quality_flags
FROM {qi(loader.news_database)}.{qi(loader.normalized_news_table)} FINAL
WHERE canonical_news_id IN ({values})
FORMAT JSONEachRow
"""


def fetch_news(
    client: ClickHouseHttpClient,
    loader: SourceLoaderConfig,
    canonical_news_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    text = client.execute(_sql_news(loader, canonical_news_ids))
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = str(row["canonical_news_id"])
        if key in result:
            raise RuntimeError(f"Duplicate normalized news row for {key}.")
        result[key] = row
    missing = sorted(set(canonical_news_ids) - result.keys())
    if missing:
        raise RuntimeError(f"Missing normalized news rows for {missing[:5]}.")
    return result


def _episode_rows(
    dataset: PreparedEpisodeDataset,
    episode_id: str,
) -> np.ndarray:
    rows = np.flatnonzero(
        np.asarray(dataset.arrays["episode_id"]) == episode_id.encode("ascii")
    ).astype(np.int64)
    source = np.asarray(dataset.arrays["source_index"][rows], dtype=np.int64)
    published = np.asarray(dataset.v15["published_at_us"][source], dtype=np.int64)
    return rows[np.argsort(published, kind="stable")]


@torch.no_grad()
def predict_rows(
    dataset: PreparedEpisodeDataset,
    model: NewsReactionModelV21,
    rows: np.ndarray,
) -> dict[int, Prediction]:
    from research.news_reaction_model.v18.data import batch_from_indices

    device = next(model.parameters()).device
    batch = batch_from_indices(dataset.v15, dataset.arrays, rows)
    moved = batch.to(device)
    output = model(moved.x)
    probabilities = output.direction_probabilities.detach().cpu().numpy()
    predicted = probabilities.argmax(axis=1)
    opportunity = signed_opportunity_torch(batch).numpy()
    expected = output.expected_return.detach().cpu().numpy()
    expected_up = output.expected_up_return.detach().cpu().numpy()
    expected_down = output.expected_down_return.detach().cpu().numpy()
    result: dict[int, Prediction] = {}
    for position, raw_row in enumerate(rows):
        row = int(raw_row)
        result[row] = Prediction(
            row_index=row,
            episode_id=str(batch.identity["episode_id"][position]),
            canonical_news_id=str(batch.identity["canonical_news_id"][position]),
            ticker=str(batch.identity["ticker"][position]),
            published_at_utc=str(batch.identity["published_at_utc"][position]),
            root_family=ROOT_FAMILY_NAMES[
                int(dataset.arrays["root_family"][row])
            ],
            role=ROLE_NAMES[int(dataset.arrays["node_role"][row])],
            actual=int(batch.direction[position]),
            predicted=int(predicted[position]),
            probabilities=tuple(float(value) for value in probabilities[position]),
            expected_return_pct=float(expected[position]),
            expected_upside_pct=float(expected_up[position]),
            expected_downside_pct=float(expected_down[position]),
            signed_opportunity_pct=float(opportunity[position]),
        )
    return result


def audit_session_days(
    sessions: Sequence[dt.date],
    published_at_utc: str,
) -> tuple[dt.date, dt.date, dt.date]:
    published = dt.datetime.fromisoformat(
        published_at_utc.replace("Z", "+00:00")
    )
    if published.tzinfo is None:
        published = published.replace(tzinfo=UTC)
    local_day = published.astimezone(EASTERN).date()
    position = bisect.bisect_left(sessions, local_day)
    if position >= len(sessions) - 2:
        raise RuntimeError(f"Calendar lacks three sessions after {local_day}.")
    values = sessions[position : position + 3]
    return values[0], values[1], values[2]


def generated_expiry_on_non_session(
    target_end: dt.datetime,
    sessions: Collection[dt.date],
) -> bool:
    """Identify V18's generated 20:00 ET expiry on a non-session date."""
    return (
        target_end.date() not in sessions
        and target_end.hour == 20
        and target_end.minute == 0
        and target_end.second == 0
    )


def minute_bars(events: np.ndarray) -> list[MinuteBar]:
    if not events.size:
        return []
    minute_ids = (events[:, 0].astype(np.int64) // 60_000_000).astype(np.int64)
    last_mask = events[:, 6] > 0.5
    extrema_mask = events[:, 7] > 0.5
    result: list[MinuteBar] = []
    for minute in np.unique(minute_ids[last_mask | extrema_mask]):
        last = events[(minute_ids == minute) & last_mask]
        extrema = events[(minute_ids == minute) & extrema_mask]
        if not last.size and not extrema.size:
            continue
        reference = last if last.size else extrema
        high_source = extrema[:, 2] if extrema.size else reference[:, 2]
        result.append(
            MinuteBar(
                timestamp_us=int(minute * 60_000_000),
                open=float(reference[0, 2]),
                high=float(np.max(high_source)),
                low=float(np.min(high_source)),
                close=float(reference[-1, 2]),
                volume=float(np.sum(last[:, 3])) if last.size else 0.0,
            )
        )
    return result


def fetch_episode_events(
    client: ClickHouseHttpClient,
    loader: SourceLoaderConfig,
    ticker: str,
    days: Sequence[dt.date],
    cache: dict[tuple[str, dt.date], np.ndarray],
) -> tuple[list[MinuteBar], list[dict[str, Any]]]:
    bars: list[MinuteBar] = []
    summaries: list[dict[str, Any]] = []
    for day in days:
        key = (ticker, day)
        if key not in cache:
            cache[key] = event_rows_for_tickers(
                client, loader, [ticker], day
            )[ticker]
        events = cache[key]
        day_bars = minute_bars(events)
        bars.extend(day_bars)
        summaries.append(
            {
                "session": day.isoformat(),
                "canonical_events": int(events.shape[0]),
                "update_last_events": int(np.count_nonzero(events[:, 6] > 0.5))
                if events.size
                else 0,
                "update_high_low_events": int(
                    np.count_nonzero(events[:, 7] > 0.5)
                )
                if events.size
                else 0,
                "minute_bars": len(day_bars),
                "open": day_bars[0].open if day_bars else math.nan,
                "high": max((value.high for value in day_bars), default=math.nan),
                "low": min((value.low for value in day_bars), default=math.nan),
                "close": day_bars[-1].close if day_bars else math.nan,
                "volume": sum(value.volume for value in day_bars),
            }
        )
    bars.sort(key=lambda value: value.timestamp_us)
    return bars, summaries


def _datetime_et(timestamp_us: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(timestamp_us / 1_000_000, UTC).astimezone(
        EASTERN
    )


def render_chart(
    path: Path,
    *,
    ticker: str,
    bars: Sequence[MinuteBar],
    episode_rows: np.ndarray,
    sampled_row: int,
    dataset: PreparedEpisodeDataset,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure, (price_axis, volume_axis) = plt.subplots(
        2,
        1,
        figsize=(15, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1], "hspace": 0.04},
    )
    figure.patch.set_facecolor("white")
    for axis in (price_axis, volume_axis):
        axis.set_facecolor("white")
    if bars:
        times = np.asarray(
            [mdates.date2num(_datetime_et(value.timestamp_us)) for value in bars]
        )
        width = 0.00045
        for x, bar in zip(times, bars, strict=True):
            color = "#00a884" if bar.close >= bar.open else "#ff3b5c"
            price_axis.vlines(x, bar.low, bar.high, color=color, linewidth=0.55)
            body_low = min(bar.open, bar.close)
            body_height = max(abs(bar.close - bar.open), 1e-6)
            price_axis.add_patch(
                plt.Rectangle(
                    (x - width / 2, body_low),
                    width,
                    body_height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=0.4,
                )
            )
            volume_axis.bar(
                x,
                bar.volume,
                width=width,
                color=color,
                alpha=0.45,
                linewidth=0,
            )
    sampled_start = int(dataset.arrays["target_start_us"][sampled_row])
    sampled_end = int(dataset.arrays["target_end_us"][sampled_row])
    anchor = float(dataset.arrays["anchor_price"][sampled_row])
    raw = np.asarray(dataset.arrays["raw_metrics"][sampled_row], dtype=np.float64)
    high_price = anchor * (
        1.0 + raw[RAW_METRIC_NAMES.index("high_return")]
    )
    low_price = anchor * (
        1.0 + raw[RAW_METRIC_NAMES.index("low_return")]
    )
    terminal_price = anchor * (
        1.0 + raw[RAW_METRIC_NAMES.index("terminal_return")]
    )
    duration = sampled_end - sampled_start
    high_us = sampled_start + int(
        duration * raw[RAW_METRIC_NAMES.index("high_time_fraction")]
    )
    low_us = sampled_start + int(
        duration * raw[RAW_METRIC_NAMES.index("low_time_fraction")]
    )
    price_axis.axhline(
        anchor, color="#6b7280", linestyle="--", linewidth=1, label="anchor"
    )
    price_axis.scatter(
        [mdates.date2num(_datetime_et(high_us))],
        [high_price],
        marker="^",
        color="#00a884",
        s=55,
        zorder=5,
        label="target high",
    )
    price_axis.scatter(
        [mdates.date2num(_datetime_et(low_us))],
        [low_price],
        marker="v",
        color="#ff3b5c",
        s=55,
        zorder=5,
        label="target low",
    )
    price_axis.scatter(
        [mdates.date2num(_datetime_et(sampled_end))],
        [terminal_price],
        marker="o",
        facecolor="white",
        edgecolor="#111827",
        s=45,
        zorder=5,
        label="target terminal at boundary",
    )
    source = np.asarray(
        dataset.arrays["source_index"][episode_rows], dtype=np.int64
    )
    published = np.asarray(
        dataset.v15["published_at_us"][source], dtype=np.int64
    )
    for position, (raw_row, timestamp_us) in enumerate(
        zip(episode_rows, published, strict=True)
    ):
        row = int(raw_row)
        color = "#7c3aed" if row == sampled_row else "#64748b"
        for axis in (price_axis, volume_axis):
            axis.axvline(
                mdates.date2num(_datetime_et(int(timestamp_us))),
                color=color,
                linewidth=1.0 if row == sampled_row else 0.7,
                alpha=0.9,
            )
        price_axis.annotate(
            f"N{position + 1}",
            (
                mdates.date2num(_datetime_et(int(timestamp_us))),
                price_axis.get_ylim()[1],
            ),
            xytext=(2, -14),
            textcoords="offset points",
            color=color,
            fontsize=8,
            va="top",
        )
    price_axis.axvspan(
        mdates.date2num(_datetime_et(sampled_start)),
        mdates.date2num(_datetime_et(sampled_end)),
        color="#7c3aed",
        alpha=0.05,
        label="sample target interval",
    )
    price_axis.set_title(
        f"{ticker} exact SIP event-derived 1-minute bars · three exchange sessions",
        loc="left",
        fontsize=12,
        fontweight="semibold",
    )
    price_axis.set_ylabel("Price")
    volume_axis.set_ylabel("Volume")
    volume_axis.set_xlabel("America/New_York")
    price_axis.grid(True, color="#e5e7eb", linewidth=0.55)
    volume_axis.grid(True, color="#e5e7eb", linewidth=0.55)
    price_axis.legend(loc="upper left", ncol=5, fontsize=8, frameon=False)
    volume_axis.xaxis.set_major_formatter(
        mdates.DateFormatter("%b %d\n%H:%M", tz=EASTERN)
    )
    figure.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(figure)


def _feature_rows(names: Sequence[str], values: np.ndarray) -> list[str]:
    return [
        f"| `{name}` | {float(value):.7g} |"
        for name, value in zip(names, values, strict=True)
    ]


def _embedding_summary(values: np.ndarray) -> dict[str, Any]:
    raw = np.asarray(values, dtype=np.float32)
    return {
        "dimensions": int(raw.size),
        "nonzero": int(np.count_nonzero(raw)),
        "l2_norm": float(np.linalg.norm(raw)),
        "mean": float(raw.mean()),
        "std": float(raw.std()),
        "minimum": float(raw.min()),
        "maximum": float(raw.max()),
        "sha256": hashlib.sha256(raw.tobytes()).hexdigest(),
    }


def _format_number(value: Any, digits: int = 4) -> str:
    number = float(value)
    return "—" if not math.isfinite(number) else f"{number:.{digits}f}"


def _escape_text(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _json_list(value: Any) -> str:
    return ", ".join(f"`{_escape_text(item)}`" for item in (value or ())) or "—"


def _news_label(row: Mapping[str, Any], ticker: str) -> dict[str, Any]:
    from src.backend.news_classification import classify_news

    result = classify_news(
        {
            **row,
            "text": row.get("normalized_full_text")
            or row.get("teaser")
            or "",
        },
        ticker_count=len(row.get("tickers") or [ticker]),
    )
    return {
        "kind": result.kind,
        "origin": result.origin,
        "scope": result.scope,
        "topics": list(result.topics),
    }


def render_dossier(
    path: Path,
    *,
    ordinal: int,
    sampled: Prediction,
    episode_rows: np.ndarray,
    episode_predictions: Mapping[int, Prediction],
    dataset: PreparedEpisodeDataset,
    news: Mapping[str, Mapping[str, Any]],
    chart_relative: str,
    session_summaries: Sequence[Mapping[str, Any]],
    checkpoint_path: Path,
    calendar_expiry_defect: bool,
) -> None:
    row = sampled.row_index
    source_index = int(dataset.arrays["source_index"][row])
    current_embedding = np.asarray(
        dataset.v15["openai_embedding"][source_index], dtype=np.float32
    )
    current_features = np.asarray(
        dataset.arrays["current_episode_features"][row], dtype=np.float32
    )
    time_features = np.asarray(
        dataset.v15["time_features"][source_index], dtype=np.float32
    )
    stock_state = np.asarray(
        dataset.v15["stock_state"][source_index], dtype=np.float32
    )
    raw = np.asarray(dataset.arrays["raw_metrics"][row], dtype=np.float64)
    regression = np.asarray(
        dataset.arrays["regression_targets"][row], dtype=np.float64
    )
    target_start = _datetime_et(int(dataset.arrays["target_start_us"][row]))
    target_end = _datetime_et(int(dataset.arrays["target_end_us"][row]))
    duration_hours = (
        int(dataset.arrays["target_end_us"][row])
        - int(dataset.arrays["target_start_us"][row])
    ) / 3_600_000_000
    anchor = float(dataset.arrays["anchor_price"][row])
    context_mask = np.asarray(
        dataset.arrays["context_mask"][row], dtype=np.bool_
    )
    context_rows = np.asarray(
        dataset.arrays["context_row_indices"][row], dtype=np.int64
    )
    context_static = np.asarray(
        dataset.arrays["context_static"][row], dtype=np.float32
    )
    probabilities = dict(
        zip(DIRECTION_NAMES, sampled.probabilities, strict=True)
    )
    lines = [
        f"# Episode error audit {ordinal:02d}: {sampled.ticker} · {sampled.root_family} · {sampled.error_type}",
        "",
        "> Generated from the V21 best-validation checkpoint. This is an audit "
        "artifact, not a trading recommendation.",
        "",
        "## Error summary",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Episode ID | `{sampled.episode_id}` |",
        f"| Sampled news ID | `{sampled.canonical_news_id}` |",
        f"| Ticker | `{sampled.ticker}` |",
        f"| Published | {sampled.published_at_utc} UTC |",
        f"| V18 root family / role | `{sampled.root_family}` / `{sampled.role}` |",
        f"| Error type | **{sampled.error_type.replace('_', ' ')}** |",
        f"| Actual direction | **{DIRECTION_NAMES[sampled.actual]}** |",
        f"| Predicted direction | **{DIRECTION_NAMES[sampled.predicted]}** |",
        f"| Predicted confidence | {sampled.confidence:.3%} |",
        f"| Direction probabilities | neutral {probabilities['neutral']:.3%}; upside {probabilities['upside']:.3%}; downside {probabilities['downside']:.3%} |",
        f"| Expected signed opportunity | {sampled.expected_return_pct:+.3f}% |",
        f"| Conditional expected upside | {sampled.expected_upside_pct:.3f}% |",
        f"| Conditional expected downside magnitude | {sampled.expected_downside_pct:.3f}% |",
        f"| Actual signed opportunity | {sampled.signed_opportunity_pct:+.3f}% |",
        f"| Checkpoint | `{checkpoint_path}` |",
        "",
        "## Exact-event price path",
        "",
        f"![{sampled.ticker} price path]({chart_relative})",
        "",
        "The plot fetches canonical SIP events for the publication exchange session "
        "(or next exchange session for a non-session publication) and the next "
        "two exchange sessions. Candles are derived from those events.",
        "",
        "| Session | Canonical events | Last updates | High/low updates | Bars | O | H | L | C | Volume |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in session_summaries:
        lines.append(
            f"| {summary['session']} | {summary['canonical_events']:,} | "
            f"{summary['update_last_events']:,} | "
            f"{summary['update_high_low_events']:,} | "
            f"{summary['minute_bars']:,} | {_format_number(summary['open'])} | "
            f"{_format_number(summary['high'])} | "
            f"{_format_number(summary['low'])} | "
            f"{_format_number(summary['close'])} | "
            f"{summary['volume']:,.0f} |"
        )
    lines.extend(
        [
            "",
            "## Extracted target",
            "",
            "| Field | Value |",
            "|---|---:|",
            f"| Target start ET | {target_start.isoformat()} |",
            f"| Target end ET | {target_end.isoformat()} |",
            "| Stored-target calendar integrity | "
            + (
                "**DEFECTIVE: generated expiry falls on a non-session date** |"
                if calendar_expiry_defect
                else "No non-session generated-expiry defect detected |"
            ),
            f"| Wall duration | {duration_hours:.3f} hours |",
            f"| Anchor price | {anchor:.6f} |",
            f"| Direction | {DIRECTION_NAMES[int(dataset.arrays['direction'][row])]} |",
            f"| Path | {PATH_NAMES[int(dataset.arrays['path'][row])]} |",
            f"| Flow | {FLOW_NAMES[int(dataset.arrays['flow'][row])]} |",
            f"| High return | {regression[0]:+.4f}% |",
            f"| Low return | {regression[1]:+.4f}% |",
            f"| Terminal return | {regression[2]:+.4f}% |",
            "",
            "<details><summary>All raw target metrics</summary>",
            "",
            "| Metric | Value |",
            "|---|---:|",
            *_feature_rows(RAW_METRIC_NAMES, raw),
            "",
            "</details>",
            "",
            "## Episode news timeline",
            "",
        ]
    )
    for position, raw_episode_row in enumerate(episode_rows):
        episode_row = int(raw_episode_row)
        prediction = episode_predictions[episode_row]
        article = news[prediction.canonical_news_id]
        label = _news_label(article, prediction.ticker)
        marker = " **← sampled error**" if episode_row == row else ""
        lines.extend(
            [
                f"### N{position + 1}. {_escape_text(article.get('title'))}{marker}",
                "",
                "| Field | Value |",
                "|---|---|",
                f"| News ID | `{prediction.canonical_news_id}` |",
                f"| Published | {_escape_text(article.get('published_at_utc'))} UTC |",
                f"| V18 role / family | `{prediction.role}` / `{prediction.root_family}` |",
                f"| Deterministic label | `{label['kind']}` · `{label['origin']}` · `{label['scope']}` |",
                f"| Topics | {_json_list(label['topics'])} |",
                f"| Channels | {_json_list(article.get('channels'))} |",
                f"| Provider tags | {_json_list(article.get('provider_tags'))} |",
                f"| Author / source | {_escape_text(article.get('author'))} / {_escape_text(article.get('url_domain'))} |",
                f"| Actual / predicted | **{DIRECTION_NAMES[prediction.actual]}** / **{DIRECTION_NAMES[prediction.predicted]}** ({prediction.confidence:.2%}) |",
                f"| Probabilities | N {prediction.probabilities[0]:.2%}; U {prediction.probabilities[1]:.2%}; D {prediction.probabilities[2]:.2%} |",
                "",
                f"**Teaser:** {_escape_text(article.get('teaser'))}",
                "",
                "<details><summary>Full normalized news text</summary>",
                "",
                _escape_text(article.get("normalized_full_text")),
                "",
                "</details>",
                "",
            ]
        )
    embedding = _embedding_summary(current_embedding)
    lines.extend(
        [
            "## Model inputs for the sampled row",
            "",
            "### OpenAI text embedding",
            "",
            "| Property | Value |",
            "|---|---:|",
            *[f"| {key} | `{value}` |" for key, value in embedding.items()],
            "",
            "### Time features",
            "",
            "| Feature | Value |",
            "|---|---:|",
            *_feature_rows(TIME_FEATURE_NAMES, time_features),
            "",
            "### Current episode features",
            "",
            "| Feature | Value |",
            "|---|---:|",
            *_feature_rows(CURRENT_EPISODE_FEATURE_NAMES, current_features),
            "",
            "<details><summary>Point-in-time stock-state inputs</summary>",
            "",
            "| Feature | Value |",
            "|---|---:|",
            *_feature_rows(STOCK_STATE_NAMES, stock_state),
            "",
            "</details>",
            "",
            "### Prior episode context supplied to V21",
            "",
        ]
    )
    if not context_mask.any():
        lines.extend(["No prior episode articles were supplied.", ""])
    else:
        for raw_slot in np.flatnonzero(context_mask):
            slot = int(raw_slot)
            context_row = int(context_rows[slot])
            context_source = int(dataset.arrays["source_index"][context_row])
            context_news_id = _decode(
                dataset.v15["canonical_news_id"][context_source]
            )
            static_values = context_static[slot]
            target = np.zeros(len(CONTEXT_TARGET_NAMES), dtype=np.float32)
            valid = bool(dataset.arrays["target_mask"][context_row])
            target[0] = float(valid)
            if valid:
                prior_raw = np.asarray(
                    dataset.arrays["raw_metrics"][context_row], dtype=np.float32
                )
                for index, name, scale in (
                    (1, "high_return", 100.0),
                    (2, "low_return", 100.0),
                    (3, "terminal_return", 100.0),
                    (4, "vwap_return", 100.0),
                    (5, "buy_notional_share", 1.0),
                    (6, "sell_notional_share", 1.0),
                ):
                    target[index] = (
                        prior_raw[RAW_METRIC_NAMES.index(name)] * scale
                    )
            title = news.get(context_news_id, {}).get("title", "")
            lines.extend(
                [
                    f"#### Context slot {slot + 1}: `{context_news_id}` · {_escape_text(title)}",
                    "",
                    "| Feature | Value |",
                    "|---|---:|",
                    *_feature_rows(CONTEXT_STATIC_NAMES, static_values),
                    *_feature_rows(CONTEXT_TARGET_NAMES, target),
                    "",
                ]
            )
    lines.extend(
        [
            "## Audit cautions",
            "",
            "- V18 direction is excursion-dominance, not terminal-return sign.",
            "- The target endpoint is future-dependent: next ticker-linked news or episode expiry.",
            "- Anchor and terminal trade timestamps are not retained in V18.",
            "- The chart covers three exchange sessions; the highlighted target interval may be shorter.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _slug(value: str) -> str:
    return "".join(
        character.lower() if character.isalnum() else "-" for character in value
    ).strip("-")


def write_index(
    path: Path,
    *,
    selected: Sequence[Prediction],
    dossier_names: Mapping[str, str],
    checkpoint: Path,
    defective_calendar_expiries: int,
) -> None:
    family_counts = Counter(row.root_family for row in selected)
    error_counts = Counter(row.error_type for row in selected)
    lines = [
        "# V21 episode error audit",
        "",
        f"- Audit version: `{AUDIT_VERSION}`",
        f"- Checkpoint: `{checkpoint}`",
        f"- Unique sampled episodes: **{len(selected)}**",
        "- Selection: highest-confidence errors, round-robin balanced across V18 root family and error type.",
        "- Event evidence: exact canonical SIP events, rendered as event-derived one-minute candles.",
        f"- Stored targets with a generated expiry on a non-session date: **{defective_calendar_expiries}**.",
        "",
        "## Target-integrity warning",
        "",
        (
            f"**{defective_calendar_expiries} sampled targets are known to be defective.** "
            "The completed V18 artifact was built from a calendar query that did not "
            "filter `is_session = 1`, so its two-session inactivity expiry could land "
            "on a weekend or holiday. Plots in this audit use the corrected exchange "
            "calendar; stored targets are shown unchanged so the defect remains auditable."
            if defective_calendar_expiries
            else "No sampled stored target ended at a generated expiry on a non-session date."
        ),
        "",
        "## Sample distribution",
        "",
        "### Root families",
        "",
        "| Family | Episodes |",
        "|---|---:|",
        *[f"| {name} | {family_counts[name]} |" for name in ROOT_FAMILY_NAMES],
        "",
        "### Error modes",
        "",
        "| Error | Episodes |",
        "|---|---:|",
        *[
            f"| {name.replace('_', ' ')} | {error_counts[name]} |"
            for name in ERROR_NAMES
        ],
        "",
        "## Dossiers",
        "",
        "| # | Family | Error | Ticker | Published UTC | Actual | Predicted | Confidence | File |",
        "|---:|---|---|---|---|---|---|---:|---|",
    ]
    for ordinal, row in enumerate(selected, start=1):
        filename = dossier_names[row.episode_id]
        lines.append(
            f"| {ordinal} | {row.root_family} | {row.error_type.replace('_', ' ')} "
            f"| `{row.ticker}` | {row.published_at_utc} | "
            f"{DIRECTION_NAMES[row.actual]} | {DIRECTION_NAMES[row.predicted]} | "
            f"{row.confidence:.2%} | [open]({filename}) |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            "These are deliberately difficult error cases, not a prevalence-weighted "
            "sample. They are suitable for qualitative target auditing and "
            "failure-mode discovery, not aggregate performance estimation.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env_files(discover_env_files(Path.cwd()))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions, dataset, model, checkpoint = collect_predictions(
        args.checkpoint,
        prepared_root=args.prepared_root,
        v15_root=args.v15_root,
        start=args.start,
        end_exclusive=args.end_exclusive,
        device_name=args.device,
    )
    selected = select_stratified_errors(predictions, count=args.samples)
    prediction_by_row = {row.row_index: row for row in predictions}
    episode_rows_by_id = {
        row.episode_id: _episode_rows(dataset, row.episode_id)
        for row in selected
    }
    all_episode_rows = np.unique(
        np.concatenate(list(episode_rows_by_id.values()))
    ).astype(np.int64)
    missing_predictions = np.asarray(
        [row for row in all_episode_rows if int(row) not in prediction_by_row],
        dtype=np.int64,
    )
    if missing_predictions.size:
        prediction_by_row.update(
            predict_rows(dataset, model, missing_predictions)
        )
    news_ids = [
        prediction_by_row[int(row)].canonical_news_id for row in all_episode_rows
    ]
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    source_loader = SourceLoaderConfig(
        prepared_dataset_root=dataset.config.prepared_dataset_root,
        v15_prepared_root=dataset.config.v15_prepared_root,
    )
    news = fetch_news(client, source_loader, news_ids)
    sessions = calendar_sessions(client, source_loader)
    session_set = frozenset(sessions)
    event_cache: dict[tuple[str, dt.date], np.ndarray] = {}
    dossier_names: dict[str, str] = {}
    audit_records: list[dict[str, Any]] = []
    try:
        for ordinal, sampled in enumerate(selected, start=1):
            episode_rows = episode_rows_by_id[sampled.episode_id]
            episode_predictions = {
                int(row): prediction_by_row[int(row)] for row in episode_rows
            }
            days = audit_session_days(sessions, sampled.published_at_utc)
            bars, summaries = fetch_episode_events(
                client,
                source_loader,
                sampled.ticker,
                days,
                event_cache,
            )
            target_end = _datetime_et(
                int(dataset.arrays["target_end_us"][sampled.row_index])
            )
            calendar_expiry_defect = generated_expiry_on_non_session(
                target_end,
                session_set,
            )
            stem = (
                f"{ordinal:02d}_{_slug(sampled.root_family)}_"
                f"{_slug(sampled.error_type)}_{_slug(sampled.ticker)}_"
                f"{_slug(sampled.episode_id[:12])}"
            )
            chart = output_dir / "plots" / f"{stem}.png"
            render_chart(
                chart,
                ticker=sampled.ticker,
                bars=bars,
                episode_rows=episode_rows,
                sampled_row=sampled.row_index,
                dataset=dataset,
            )
            dossier = output_dir / f"{stem}.md"
            render_dossier(
                dossier,
                ordinal=ordinal,
                sampled=sampled,
                episode_rows=episode_rows,
                episode_predictions=episode_predictions,
                dataset=dataset,
                news=news,
                chart_relative=f"plots/{chart.name}",
                session_summaries=summaries,
                checkpoint_path=args.checkpoint,
                calendar_expiry_defect=calendar_expiry_defect,
            )
            dossier_names[sampled.episode_id] = dossier.name
            audit_records.append(
                {
                    "ordinal": ordinal,
                    "episode_id": sampled.episode_id,
                    "sampled_row_index": sampled.row_index,
                    "canonical_news_id": sampled.canonical_news_id,
                    "ticker": sampled.ticker,
                    "published_at_utc": sampled.published_at_utc,
                    "root_family": sampled.root_family,
                    "role": sampled.role,
                    "error_type": sampled.error_type,
                    "actual": DIRECTION_NAMES[sampled.actual],
                    "predicted": DIRECTION_NAMES[sampled.predicted],
                    "confidence": sampled.confidence,
                    "probabilities": dict(
                        zip(DIRECTION_NAMES, sampled.probabilities, strict=True)
                    ),
                    "expected_return_pct": sampled.expected_return_pct,
                    "expected_upside_pct": sampled.expected_upside_pct,
                    "expected_downside_pct": sampled.expected_downside_pct,
                    "actual_signed_opportunity_pct": sampled.signed_opportunity_pct,
                    "episode_rows": [int(value) for value in episode_rows],
                    "event_sessions": [value.isoformat() for value in days],
                    "stored_target_calendar_expiry_defect": calendar_expiry_defect,
                    "dossier": dossier.name,
                    "chart": f"plots/{chart.name}",
                }
            )
            print(
                f"[{ordinal}/{len(selected)}] {sampled.root_family} "
                f"{sampled.error_type} {sampled.ticker} "
                f"confidence={sampled.confidence:.3f}",
                flush=True,
            )
        write_index(
            output_dir / "INDEX.md",
            selected=selected,
            dossier_names=dossier_names,
            checkpoint=args.checkpoint,
            defective_calendar_expiries=sum(
                bool(record["stored_target_calendar_expiry_defect"])
                for record in audit_records
            ),
        )
        (output_dir / "audit_manifest.json").write_text(
            json.dumps(
                {
                    "audit_version": AUDIT_VERSION,
                    "checkpoint": str(args.checkpoint),
                    "checkpoint_epoch": int(checkpoint["epoch"]),
                    "start": args.start,
                    "end_exclusive": args.end_exclusive,
                    "sample_count": len(selected),
                    "selection_contract": (
                        "unique episodes; highest confidence errors; "
                        "round-robin by root family and error type"
                    ),
                    "records": audit_records,
                },
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        dataset.stop()
    print(
        f"COMPLETED | dossiers={len(selected)} | output={output_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
