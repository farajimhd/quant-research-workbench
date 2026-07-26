from __future__ import annotations

import base64
import datetime as dt
import gc
import json
import struct
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import torch

from research.news_reaction_model import v16
from research.news_reaction_model.v16.config import (
    ExperimentConfig,
    LoaderConfig,
    ModelConfig,
    TrainConfig,
    to_dict,
)
from research.news_reaction_model.v16.context import (
    CONTEXT_FEATURE_DIM,
    CONTEXT_LOOKBACK_DAYS,
    CONTEXT_SIZE,
    build_context_feature,
    context_contract,
    normalized_context_metadata,
)
from research.news_reaction_model.v16.data import (
    PreparedNewsReactionDataset,
    audit_prepared_dataset,
    deterministic_buffered_batches,
    make_dummy_batch,
    rows_to_batch,
)
from research.news_reaction_model.v16.evaluate import anchor_price_sql, midpoint_proxy_pnl
from research.news_reaction_model.v16.inference import LiveFeatureEncoder
from research.news_reaction_model.v16.losses import compute_loss
from research.news_reaction_model.v16.model import NewsReactionModelV16
from research.news_reaction_model.v16.market_context import (
    CURRENT_MARKET_RETURN_INDICES,
    CURRENT_MARKET_FEATURE_DIM,
    MARKET_NEWS_RETURN_INDICES,
    MARKET_RETURN_LIMIT,
    MARKET_WINDOW_NAMES,
    encode_market_return,
)
from research.news_reaction_model.v16.market_data import (
    DayMarketCache,
    DayMarketData,
    MINUTE_US,
    parse_daily_volume_rows,
    parse_minute_bar_rows,
)
from research.news_reaction_model.v16.prepared import (
    ARRAY_FILES,
    LEGACY_MARKET_ARRAY_FILES,
    close_arrays,
    create_arrays,
    expected_dtypes,
    expected_shapes,
    migrate_legacy_market_return_arrays,
    write_json_atomic,
)
from research.news_reaction_model.v16.prepare_data import (
    HistoryRecord,
    _observed_market_reactions,
    build_representation_sha256,
    decode_targets,
    month_ranges,
    select_market_history,
    source_page_sql,
)
from research.news_reaction_model.v16.train import validate_config


def tiny_configs(root: Path | None = None) -> tuple[LoaderConfig, ModelConfig]:
    loader = LoaderConfig(
        prepared_dataset_root=root or Path("unused"),
        openai_embedding_dim=8,
        stock_state_dim=4,
        batch_size=2,
        query_batch_articles=2,
        shuffle_buffer_articles=4,
    )
    model = ModelConfig(
        openai_embedding_dim=8,
        stock_state_dim=4,
        context_size=CONTEXT_SIZE,
        context_feature_dim=CONTEXT_FEATURE_DIM,
        attention_heads=2,
        d_model=8,
        hidden_dim=8,
        layers=1,
        dropout=0.0,
    )
    return loader, model


class NewsReactionModelV16Tests(unittest.TestCase):
    def test_source_page_aliases_certified_target_arrays(self) -> None:
        sql = source_page_sql(
            LoaderConfig(),
            dt.date(2026, 7, 10),
            dt.date(2026, 7, 11),
            ("1970-01-01 00:00:00.000000000", "", ""),
        )
        self.assertIn("c.horizon_codes AS horizon_codes", sql)
        self.assertIn("c.return_targets AS return_targets", sql)

    @staticmethod
    def _market_rows() -> list[dict[str, object]]:
        base = int(
            dt.datetime(2026, 7, 14, 13, 30, tzinfo=dt.timezone.utc).timestamp()
            * 1_000_000
        )
        rows: list[dict[str, object]] = []
        for ticker, prices, volumes in (
            ("AAPL", (100.0, 101.0, 102.0), (100.0, 200.0, 300.0)),
            ("MSFT", (200.0, 198.0, 197.0), (500.0, 500.0, 500.0)),
        ):
            for index, (price, volume) in enumerate(zip(prices, volumes), start=1):
                rows.append(
                    {
                        "ticker": ticker,
                        "minute_end_us": base + index * MINUTE_US,
                        "open": price,
                        "high": price + 0.5,
                        "low": price - 0.5,
                        "close": price,
                        "volume": volume,
                        "dollar_volume": volume * price,
                        "trade_count": 10,
                        "quote_count": 20,
                    }
                )
        return rows

    def test_json_checkpoint_config_restores_runtime_types(self) -> None:
        config = ExperimentConfig(
            loader=LoaderConfig(
                representation_artifact_root=Path("representation"),
                prepared_dataset_root=Path("prepared"),
            ),
            model=ModelConfig(),
            train=TrainConfig(output_root=Path("output")),
        )
        serialized = json.loads(json.dumps(to_dict(config), default=str))
        loader = LoaderConfig(**serialized["loader"])
        model = ModelConfig(**serialized["model"])
        train = TrainConfig(**serialized["train"])

        self.assertIsInstance(loader.representation_artifact_root, Path)
        self.assertIsInstance(loader.prepared_dataset_root, Path)
        self.assertEqual(loader.prepared_dataset_root / "manifest.json", Path("prepared/manifest.json"))
        self.assertIsInstance(loader.horizons, tuple)
        self.assertIsInstance(model.horizons, tuple)
        self.assertIsInstance(train.output_root, Path)

    def test_anchor_query_does_not_shadow_datetime_with_string_alias(self) -> None:
        sql = anchor_price_sql(LoaderConfig(), "2026-01-01", "2027-01-01")
        self.assertIn("toString(published_at_utc) AS published_at_utc_text", sql)
        self.assertNotIn("toString(published_at_utc) AS published_at_utc,", sql)

    def test_version_preserves_v12_task_and_adds_fixed_context_contract(self) -> None:
        loader = LoaderConfig()
        self.assertEqual(v16.MODEL_VERSION, "v16")
        self.assertEqual(
            v16.HORIZONS,
            (
                "1m",
                "5m",
                "10m",
                "30m",
                "1h",
                "2h",
                "3h",
                "premarket_close",
                "regular_close",
                "extended_close",
            ),
        )
        self.assertEqual(loader.context_size, 4)
        self.assertEqual(loader.context_lookback_days, 7)
        self.assertEqual(loader.context_feature_dim, 49)
        self.assertEqual(
            context_contract()["strict_predecessor"],
            "prior published_at_utc must be strictly less than current published_at_utc",
        )

    def test_context_exposes_only_reactions_available_before_current_news(self) -> None:
        current = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.timezone.utc)
        metadata = normalized_context_metadata(
            prior_published_at_utc=current - dt.timedelta(hours=2),
            current_published_at_utc=current,
            prior_publication_session="premarket",
            current_publication_session="regular",
            prior_reaction_session_index=10,
            current_reaction_session_index=10,
        )
        values = np.asarray(
            [[0.01, 0.02, -0.01], [0.03, 0.04, -0.02]],
            dtype=np.float32,
        )
        feature, available = build_context_feature(
            prior_returns=values,
            prior_horizon_codes=("1m", "5m"),
            available_at_by_horizon={
                "1m": current - dt.timedelta(seconds=1),
                "5m": current,
            },
            current_published_at_utc=current,
            metadata=metadata,
        )
        self.assertEqual(feature.shape, (CONTEXT_FEATURE_DIM,))
        self.assertTrue(available[0])
        self.assertFalse(available[1])
        np.testing.assert_allclose(feature[:3], values[0])
        np.testing.assert_allclose(feature[3:6], 0.0)

    def test_same_timestamp_is_not_a_valid_predecessor(self) -> None:
        current = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.timezone.utc)
        with self.assertRaisesRegex(ValueError, "strictly earlier"):
            normalized_context_metadata(
                prior_published_at_utc=current,
                current_published_at_utc=current,
                prior_publication_session="regular",
                current_publication_session="regular",
                prior_reaction_session_index=10,
                current_reaction_session_index=10,
            )

    def test_market_history_is_latest_fixed_n_and_excludes_equal_timestamps(self) -> None:
        current = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.timezone.utc)
        history = deque(
            [
                SimpleNamespace(
                    published_at_utc=current - dt.timedelta(minutes=offset),
                    reaction_session_index=10,
                )
                for offset in (4, 3, 2, 1)
            ]
            + [
                SimpleNamespace(
                    published_at_utc=current,
                    reaction_session_index=10,
                )
            ]
        )
        selected = select_market_history(
            history,
            current_published=current,
            current_session_index=10,
            size=2,
            max_session_distance=3,
        )
        self.assertEqual(
            [item.published_at_utc for item in selected],
            [current - dt.timedelta(minutes=2), current - dt.timedelta(minutes=1)],
        )

    def test_model_uses_context_but_cold_start_is_stable(self) -> None:
        loader, model_config = tiny_configs()
        torch.manual_seed(3)
        model = NewsReactionModelV16(model_config).eval()
        batch = make_dummy_batch(2, loader)
        cold = {key: value.clone() for key, value in batch.x.items()}
        cold["prior_context_mask"].zero_()
        cold["prior_openai_embeddings"].zero_()
        cold["prior_context_features"].zero_()
        with torch.no_grad():
            first = model(cold)
            second = model(cold)
            contextual = model(batch.x)
        self.assertTrue(torch.equal(first.article_embedding, second.article_embedding))
        self.assertFalse(torch.equal(first.article_embedding[1], contextual.article_embedding[1]))
        self.assertTrue(torch.equal(first.article_embedding[0], contextual.article_embedding[0]))

    def test_market_features_use_only_completed_pre_news_minutes(self) -> None:
        day = DayMarketData(
            dt.date(2026, 7, 14),
            self._market_rows(),
            {"AAPL": 1_000.0, "MSFT": 5_000.0},
        )
        base = int(
            dt.datetime(2026, 7, 14, 13, 30, tzinfo=dt.timezone.utc).timestamp()
            * 1_000_000
        )
        # At 09:32:30 ET only bars ending at 09:31 and 09:32 are complete.
        published = base + 2 * MINUTE_US + 30_000_000
        features = day.current_features("AAPL", published)
        self.assertEqual(features.shape, (CURRENT_MARKET_FEATURE_DIM,))
        one_minute = day.window(
            "AAPL",
            end_us=published // MINUTE_US * MINUTE_US,
            seconds=60,
        )
        self.assertAlmostEqual(one_minute["terminal_return"], 0.0)
        session = day.window(
            "AAPL",
            end_us=published // MINUTE_US * MINUTE_US,
            seconds=None,
        )
        self.assertAlmostEqual(session["terminal_return"], 0.01)
        self.assertGreater(features[-12], 0.5)

    def test_market_return_encoding_is_local_and_bounds_extreme_prints(self) -> None:
        self.assertAlmostEqual(encode_market_return(0.01), np.log1p(0.01))
        self.assertAlmostEqual(encode_market_return(-0.01), -np.log1p(0.01))
        self.assertEqual(encode_market_return(645_799.0), MARKET_RETURN_LIMIT)
        self.assertEqual(encode_market_return(np.inf), MARKET_RETURN_LIMIT)
        with self.assertRaisesRegex(ValueError, "NaN"):
            encode_market_return(np.nan)

    def test_legacy_market_arrays_migrate_without_rebuilding_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            loader, _ = tiny_configs(Path(temporary))
            rows = 3
            shapes = expected_shapes(loader, rows)
            dtypes = expected_dtypes()
            raw_value = 645_799.0
            for name, filename in LEGACY_MARKET_ARRAY_FILES.items():
                array = np.lib.format.open_memmap(
                    Path(temporary) / filename,
                    mode="w+",
                    dtype=dtypes[name],
                    shape=shapes[name],
                )
                array.fill(0)
                indices = (
                    MARKET_NEWS_RETURN_INDICES
                    if name == "market_context_features"
                    else CURRENT_MARKET_RETURN_INDICES
                )
                array[..., indices[0]] = (
                    np.inf if dtypes[name].itemsize == 2 else raw_value
                )
                array[..., indices[1]] = np.inf
                array.flush()
                del array

            migrate_legacy_market_return_arrays(loader, rows, chunk_rows=2)

            for name, old_filename in LEGACY_MARKET_ARRAY_FILES.items():
                self.assertFalse((Path(temporary) / old_filename).exists())
                migrated = np.load(
                    Path(temporary) / ARRAY_FILES[name],
                    mmap_mode="r",
                    allow_pickle=False,
                )
                indices = (
                    MARKET_NEWS_RETURN_INDICES
                    if name == "market_context_features"
                    else CURRENT_MARKET_RETURN_INDICES
                )
                self.assertTrue(np.isfinite(migrated).all())
                self.assertTrue(
                    np.all(
                        np.asarray(migrated[..., indices[0]], dtype=np.float32)
                        == MARKET_RETURN_LIMIT
                    )
                )
                self.assertTrue(
                    np.all(
                        np.asarray(migrated[..., indices[1]], dtype=np.float32)
                        == MARKET_RETURN_LIMIT
                    )
                )
                mmap = getattr(migrated, "_mmap", None)
                if mmap is not None:
                    mmap.close()
                del migrated

    def test_market_tsv_decoder_preserves_typed_ordered_rows(self) -> None:
        rows = parse_minute_bar_rows(
            "AAPL\t100\t10\t12\t9\t11\t50\t550\t3\t4\n"
            "MSFT\t100\t20\t21\t19\t20.5\t70\t1435\t5\t6\n"
        )
        self.assertEqual(rows[0], ("AAPL", 100, 10.0, 12.0, 9.0, 11.0, 50.0, 550.0, 3, 4))
        self.assertEqual(
            parse_daily_volume_rows("AAPL\t1000\nMSFT\t0\n"),
            {"AAPL": 1000.0},
        )
        day = DayMarketData(
            dt.date(2026, 7, 14),
            rows,
            {"AAPL": 1_000.0},
            rows_chronological=True,
        )
        self.assertEqual(day.tickers, ("AAPL", "MSFT"))

    def test_market_cache_prefetches_days_concurrently_with_bounded_queue(self) -> None:
        active = 0
        maximum_active = 0
        lock = threading.Lock()

        def fake_load(_client: object, _config: object, session_date: dt.date) -> DayMarketData:
            nonlocal active, maximum_active
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            return DayMarketData(session_date, [])

        dates = [dt.date(2026, 7, day) for day in (13, 14, 15, 16)]
        with patch(
            "research.news_reaction_model.v16.market_data.load_day_market_data",
            side_effect=fake_load,
        ):
            with DayMarketCache(
                object(),
                SimpleNamespace(),
                max_sessions=3,
                prefetch_workers=2,
            ) as cache:
                cache.prefetch(dates)
                self.assertLessEqual(len(cache._pending), 2)
                loaded = [cache.get(value).session_date for value in dates]
        self.assertEqual(loaded, dates)
        self.assertGreaterEqual(maximum_active, 2)

    def test_completed_prior_reactions_are_cached_without_freezing_same_day_asof(self) -> None:
        class FakeDay:
            def __init__(self) -> None:
                self.calls: list[int | None] = []

            def post_news_window(self, *_: object, horizon_seconds: int | None, **__: object) -> dict[str, object]:
                self.calls.append(horizon_seconds)
                return {
                    "available": True,
                    "terminal_return": float(len(self.calls)),
                }

        day = FakeDay()
        cache = SimpleNamespace(get=lambda _: day)
        published = dt.datetime(2026, 7, 14, 13, 30, tzinfo=dt.timezone.utc)
        prior = HistoryRecord(
            row_index=1,
            canonical_news_id="n1",
            ticker="AAPL",
            published_at_utc=published,
            published_at_text=str(published),
            publication_session="regular",
            reaction_session_date=dt.date(2026, 7, 14),
            reaction_session_index=1,
            horizon_codes=("1m", "5m", "10m", "30m"),
            return_targets=np.zeros((4, 3), dtype=np.float32),
            available_at_by_horizon={
                name: published + dt.timedelta(seconds=seconds)
                for name, seconds in zip(
                    ("1m", "5m", "10m", "30m"),
                    (60, 300, 600, 1800),
                )
            },
            pre_market_features=np.zeros(CURRENT_MARKET_FEATURE_DIM, dtype=np.float32),
        )
        same_day_first = published + dt.timedelta(hours=1)
        same_day_second = published + dt.timedelta(hours=2)
        _observed_market_reactions(
            prior,
            current_published=same_day_first,
            market_days=cache,
        )
        _observed_market_reactions(
            prior,
            current_published=same_day_second,
            market_days=cache,
        )
        self.assertEqual(day.calls.count(None), 2)
        self.assertEqual(len([value for value in day.calls if value is not None]), 4)

        later_session = published + dt.timedelta(days=1)
        _observed_market_reactions(
            prior,
            current_published=later_session,
            market_days=cache,
        )
        _observed_market_reactions(
            prior,
            current_published=later_session + dt.timedelta(hours=1),
            market_days=cache,
        )
        self.assertEqual(day.calls.count(None), 3)

    def test_prior_observation_masks_incomplete_horizon_and_excludes_news_minute(self) -> None:
        day = DayMarketData(dt.date(2026, 7, 14), self._market_rows())
        base = int(
            dt.datetime(2026, 7, 14, 13, 30, tzinfo=dt.timezone.utc).timestamp()
            * 1_000_000
        )
        incomplete = day.post_news_window(
            "AAPL",
            published_us=base + 30_000_000,
            observed_through_us=base + 4 * MINUTE_US,
            horizon_seconds=300,
        )
        self.assertFalse(incomplete["available"])
        complete = day.post_news_window(
            "AAPL",
            published_us=base,
            observed_through_us=base + 3 * MINUTE_US,
            horizon_seconds=60,
        )
        self.assertTrue(complete["available"])
        self.assertAlmostEqual(complete["terminal_return"], 0.0)

    def test_market_attention_changes_only_rows_with_market_tokens(self) -> None:
        loader, model_config = tiny_configs()
        torch.manual_seed(19)
        model = NewsReactionModelV16(model_config).eval()
        batch = make_dummy_batch(2, loader)
        no_prior = {key: value.clone() for key, value in batch.x.items()}
        no_prior["prior_context_mask"].zero_()
        no_prior["prior_openai_embeddings"].zero_()
        no_prior["prior_context_features"].zero_()
        cold = {key: value.clone() for key, value in no_prior.items()}
        cold["market_context_mask"].zero_()
        cold["market_leader_mask"].zero_()
        cold["market_context_openai_embeddings"].zero_()
        cold["market_context_features"].zero_()
        cold["market_leader_features"].zero_()
        with torch.no_grad():
            baseline = model(cold)
            contextual = model(no_prior)
        self.assertTrue(
            torch.equal(baseline.article_embedding[0], contextual.article_embedding[0])
        )
        self.assertFalse(
            torch.equal(baseline.article_embedding[1], contextual.article_embedding[1])
        )

    def test_forward_and_loss_cover_all_v12_horizons(self) -> None:
        loader, model_config = tiny_configs()
        batch = make_dummy_batch(4, loader)
        output = NewsReactionModelV16(model_config)(batch.x)
        self.assertEqual(set(output.logits), set(v16.HORIZONS))
        self.assertTrue(all(value.shape == (4, 3) for value in output.logits.values()))
        self.assertTrue(torch.isfinite(compute_loss(output, batch).loss))

    def test_rows_and_live_encoder_accept_explicit_fixed_context(self) -> None:
        loader, _ = tiny_configs()
        vector = [float(index) for index in range(loader.openai_embedding_dim)]
        prior_embeddings = np.zeros(
            (loader.context_size, loader.openai_embedding_dim), dtype=np.float32
        )
        prior_features = np.zeros(
            (loader.context_size, loader.context_feature_dim), dtype=np.float32
        )
        prior_embeddings[0] = 1.0
        prior_features[0, -1] = 1.0
        row = {
            "source_id": "n1",
            "ticker": "AAPL",
            "published_at_utc": "2026-07-14 14:00:00",
            "publication_session": "regular",
            "openai_embedding_b64": base64.b64encode(
                struct.pack("<8f", *vector)
            ).decode("ascii"),
            "stock_state": [0.1] * loader.stock_state_dim,
            "prior_openai_embeddings": prior_embeddings,
            "prior_context_features": prior_features,
            "prior_context_mask": [True, False, False, False],
        }
        batch = rows_to_batch([row], loader)
        self.assertEqual(batch.x["prior_context_mask"].tolist(), [[True, False, False, False]])
        encoded = LiveFeatureEncoder(loader).encode([row], device=torch.device("cpu"))
        self.assertEqual(set(encoded), set(batch.x))

    def test_prepared_arrays_preserve_indices_and_deterministic_shuffle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            loader, _ = tiny_configs(root)
            rows = 6
            arrays = create_arrays(loader, rows)
            for index in range(rows):
                arrays["openai_embedding"][index, index % loader.openai_embedding_dim] = 1
                arrays["stock_state"][index] = 0.1
                arrays["time_features"][index] = 0.2
                arrays["return_targets"][index] = 0.001
                arrays["label_mask"][index] = True
                arrays["canonical_news_id"][index] = f"n{index}".encode()
                arrays["ticker"][index] = b"AAPL"
                timestamp = f"2025-01-0{index + 1} 12:00:00"
                arrays["published_at_utc"][index] = timestamp.encode()
                arrays["published_at_us"][index] = int(
                    dt.datetime(
                        2025, 1, index + 1, 12, tzinfo=dt.timezone.utc
                    ).timestamp()
                    * 1_000_000
                )
                arrays["publication_session"][index] = b"regular"
                if index:
                    arrays["context_indices"][index, 0] = index - 1
                    arrays["context_mask"][index, 0] = True
            for array in arrays.values():
                array.flush()
            write_json_atomic(
                root / "manifest.json",
                {
                    "status": "complete",
                    "dataset_version": loader.prepared_dataset_version,
                    "rows": rows,
                    "representation_sha256": "test",
                },
            )
            audit = audit_prepared_dataset(loader, "2025-01-01", "2025-02-01")
            self.assertEqual(audit["context_articles"], 5)
            dataset = PreparedNewsReactionDataset(
                loader, start="2025-01-01", end_exclusive="2025-02-01"
            )
            loaded = next(dataset.iter_batches())
            self.assertEqual(loaded.x["prior_context_mask"][1, 0].item(), True)
            first = [
                value
                for batch in deterministic_buffered_batches(
                    loader,
                    start="2025-01-01",
                    end_exclusive="2025-02-01",
                    epoch=1,
                    seed=17,
                )
                for value in batch.identity["canonical_news_id"]
            ]
            second = [
                value
                for batch in deterministic_buffered_batches(
                    loader,
                    start="2025-01-01",
                    end_exclusive="2025-02-01",
                    epoch=1,
                    seed=17,
                )
                for value in batch.identity["canonical_news_id"]
            ]
            self.assertEqual(first, second)
            self.assertCountEqual(first, [f"n{index}" for index in range(rows)])
            dataset.stop()
            close_arrays(arrays)
            del loaded, dataset
            gc.collect()

    def test_target_mapping_and_representation_hash_are_deterministic(self) -> None:
        loader, _ = tiny_configs()
        row = {
            "canonical_news_id": "n1",
            "horizon_codes": ["5m", "1m"],
            "return_targets": [[0.1, 0.2, -0.1], [0.01, 0.02, -0.01]],
        }
        values, mask, codes, source = decode_targets(row, loader)
        self.assertEqual(codes, ("5m", "1m"))
        self.assertTrue(mask[:2].all())
        np.testing.assert_allclose(values[0], source[1])
        first = build_representation_sha256(
            loader,
            source_representation_sha256="source",
            source_rows_count=6,
            certified_target_source_signature="targets",
        )
        second = build_representation_sha256(
            loader,
            source_representation_sha256="source",
            source_rows_count=6,
            certified_target_source_signature="targets",
        )
        self.assertEqual(first, second)

    def test_config_rejects_context_or_attention_drift(self) -> None:
        config = ExperimentConfig()
        validate_config(config)
        config.loader.context_size = CONTEXT_SIZE + 1
        with self.assertRaisesRegex(ValueError, "fixed"):
            validate_config(config)
        config = ExperimentConfig()
        config.model.attention_heads = 5
        with self.assertRaisesRegex(ValueError, "divisible"):
            validate_config(config)

    def test_month_ranges_cover_requested_period_once(self) -> None:
        self.assertEqual(
            month_ranges("2025-12-15", "2026-02-03"),
            [
                (dt.date(2025, 12, 15), dt.date(2026, 1, 1)),
                (dt.date(2026, 1, 1), dt.date(2026, 2, 1)),
                (dt.date(2026, 2, 1), dt.date(2026, 2, 3)),
            ],
        )

    def test_midpoint_proxy_remains_v12_evaluation_contract(self) -> None:
        midpoint, pnl = midpoint_proxy_pnl(
            np.array([1, -1, 0]),
            np.array([0.06, 0.02, 0.10]),
            np.array([-0.02, -0.08, -0.10]),
            np.array([100.0, 50.0, 20.0]),
        )
        np.testing.assert_allclose(midpoint, [0.02, -0.03, 0.0])
        np.testing.assert_allclose(pnl, [2.0, 1.5, 0.0])


if __name__ == "__main__":
    unittest.main()
