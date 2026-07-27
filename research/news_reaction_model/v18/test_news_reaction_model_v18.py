from __future__ import annotations

import unittest
import datetime as dt
from collections import defaultdict
from unittest.mock import patch

import numpy as np
import torch

from research.news_reaction_model.v18.config import ModelConfig
from research.news_reaction_model.v18.config import LoaderConfig
from research.news_reaction_model.v18.episode_contract import (
    ArticleSignals,
    NodeRole,
    RootFamily,
    classify_article,
    context_static_features,
    current_episode_features,
)
from research.news_reaction_model.v18.model import NewsReactionModelV18
from research.news_reaction_model.v15.stock_state import STOCK_STATE_NAMES, signed_log
from research.news_reaction_model.v17.prepare_targets import (
    CancellationController,
    IntervalAggregate,
    IntervalRequest,
    interval_aggregate_sql,
)
from research.news_reaction_model.v18.prepare_data import (
    Article,
    anchor_session_days,
    audit_anchor_storage_contract,
    audit_target_interval_contract,
    calendar_sessions,
    consume_article,
    enforce_exact_root_contract,
    exact_anchor_price,
    planning_anchor_prices,
    raw_metrics_from_aggregate,
    process_target_unit,
    TargetProgress,
    TargetWorkUnit,
    target_work_units,
    timestamp_us,
)
from research.news_reaction_model.v18.targets import (
    Direction,
    Flow,
    Path,
    TargetThresholds,
    classify,
)


class EpisodeContractTest(unittest.TestCase):
    def signals(
        self,
        *,
        title: str,
        relevance: str = "ticker_related",
        families: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
    ) -> ArticleSignals:
        return ArticleSignals(
            title=title,
            author="author",
            channels=(),
            tags=tags,
            semantic_families=families,
            relevance_class=relevance,
            text_hash=title,
            has_body=True,
        )

    def test_company_regulatory_editorial_and_analyst_are_root_eligible(self) -> None:
        cases = (
            (self.signals(title="Company update", relevance="company_specific"), RootFamily.COMPANY),
            (self.signals(title="FDA approval", families=("regulatory_clinical",)), RootFamily.REGULATORY),
            (self.signals(title="Long-form sector view"), RootFamily.EDITORIAL),
            (self.signals(title="Analyst upgrades stock"), RootFamily.ANALYST),
        )
        for signals, family in cases:
            with self.subTest(family=family):
                result = classify_article(signals)
                self.assertTrue(result.root_eligible)
                self.assertEqual(result.root_family, family)

    def test_reactive_article_cannot_start_episode(self) -> None:
        result = classify_article(
            self.signals(
                title="Why It Is Moving In Premarket",
                relevance="company_specific",
                tags=("movers",),
            )
        )
        self.assertTrue(result.reactive)
        self.assertFalse(result.root_eligible)
        self.assertEqual(result.role, NodeRole.REACTIVE)

    def test_broad_company_family_alone_does_not_merge_events(self) -> None:
        from research.news_reaction_model.v18.episode_contract import related_material_update

        first = self.signals(
            title="Company raises annual revenue guidance",
            relevance="company_specific",
            families=("guidance",),
        )
        second = self.signals(
            title="Company announces unrelated acquisition",
            relevance="company_specific",
            families=("mergers_acquisitions",),
        )
        self.assertFalse(
            related_material_update(
                second,
                first,
                current_family=RootFamily.COMPANY,
                previous_family=RootFamily.COMPANY,
            )
        )

    def test_feature_dimensions_are_stable(self) -> None:
        current = current_episode_features(
            role=NodeRole.ROOT,
            root_family=RootFamily.COMPANY,
            node_position=0,
            root_age_sessions=0,
            minutes_since_material=0,
            same_session_as_root=True,
            unembedded_nodes_before=0,
        )
        static = context_static_features(
            role=NodeRole.MATERIAL_UPDATE,
            gap_minutes=10,
            root_age_sessions=0,
            node_distance=1,
            same_publication_session=True,
            intervening_unembedded_count=0,
        )
        self.assertEqual(current.shape, (15,))
        self.assertEqual(static.shape, (10,))

    def test_root_filter_followup_price_and_multi_ticker_censor(self) -> None:
        sessions = [
            dt.date(2026, 7, 10),
            dt.date(2026, 7, 13),
            dt.date(2026, 7, 14),
            dt.date(2026, 7, 15),
        ]
        config = LoaderConfig(root_max_price=20.0)
        active = {}
        planned = []
        counts = defaultdict(int)

        def article(
            minute: int,
            *,
            news_id: str,
            price: float | None,
            source: int | None,
            ticker_count: int = 1,
            title: str = "Company reports material update",
        ) -> Article:
            published = dt.datetime(
                2026, 7, 10, 13, minute, tzinfo=dt.timezone.utc
            )
            signals = self.signals(
                title=title,
                relevance="company_specific",
                families=("guidance",),
            )
            classification = classify_article(signals)
            return Article(
                news_id,
                "TEST",
                published,
                published.isoformat(),
                ticker_count,
                signals,
                classification,
                source,
                price,
                "premarket",
            )

        root = article(0, news_id="root", price=10.0, source=3)
        consume_article(
            root,
            active_by_ticker=active,
            planned=planned,
            counts=counts,
            config=config,
            sessions=sessions,
        )
        censor = article(
            5, news_id="macro", price=None, source=None, ticker_count=3
        )
        consume_article(
            censor,
            active_by_ticker=active,
            planned=planned,
            counts=counts,
            config=config,
            sessions=sessions,
        )
        followup = article(
            10,
            news_id="follow",
            price=25.0,
            source=4,
            title="Company raises guidance with new details",
        )
        consume_article(
            followup,
            active_by_ticker=active,
            planned=planned,
            counts=counts,
            config=config,
            sessions=sessions,
        )
        self.assertEqual(len(planned), 2)
        self.assertEqual(planned[0].role, NodeRole.ROOT)
        self.assertEqual(planned[1].role, NodeRole.MATERIAL_UPDATE)
        self.assertEqual(planned[0].target_end, censor.published)
        self.assertEqual(planned[1].episode_id, planned[0].episode_id)


class TargetContractTest(unittest.TestCase):
    def test_exchange_calendar_excludes_non_session_dates(self) -> None:
        class Client:
            query = ""

            def execute(self, query: str) -> str:
                self.query = query
                return "2026-07-10\n2026-07-13\n"

        client = Client()
        sessions = calendar_sessions(client, LoaderConfig())
        self.assertEqual(
            sessions,
            [dt.date(2026, 7, 10), dt.date(2026, 7, 13)],
        )
        self.assertIn("AND is_session = 1", client.query)

    def test_v15_anchor_is_planning_only_and_exact_anchor_is_strictly_prior(self) -> None:
        stock_state = np.zeros((1, len(STOCK_STATE_NAMES)), dtype=np.float32)
        stock_state[0, STOCK_STATE_NAMES.index("anchor_price")] = signed_log(10.0)
        stock_state[0, STOCK_STATE_NAMES.index("anchor_present")] = 1.0
        identity = ("news", "TEST", 1_000_000)
        planning = planning_anchor_prices(
            {"stock_state": stock_state},
            {identity: 0},
        )
        self.assertAlmostEqual(planning[identity], 10.0, places=5)

        events = np.asarray(
            [
                [800_000, 1, 9.8, 100, 9.7, 9.8, 1, 1],
                [900_000, 2, 10.1, 100, 10.0, 10.1, 1, 1],
                # A high/low-only print cannot become the last-price anchor.
                [950_000, 3, 12.0, 100, 11.9, 12.0, 0, 1],
                # A trade at the publication timestamp is not causal input.
                [1_000_000, 4, 11.0, 100, 10.9, 11.0, 1, 1],
            ],
            dtype=np.float64,
        )
        exact = exact_anchor_price(
            [events],
            published=dt.datetime.fromtimestamp(1.0, dt.timezone.utc),
        )
        self.assertEqual(exact, 10.1)

    def test_anchor_sessions_include_only_current_and_immediate_predecessor(self) -> None:
        sessions = (
            dt.date(2026, 7, 10),
            dt.date(2026, 7, 13),
            dt.date(2026, 7, 14),
        )
        self.assertEqual(
            anchor_session_days(sessions, dt.date(2026, 7, 13)),
            (dt.date(2026, 7, 10), dt.date(2026, 7, 13)),
        )
        self.assertEqual(
            anchor_session_days(sessions, dt.date(2026, 7, 12)),
            (dt.date(2026, 7, 10),),
        )

    def test_exact_root_rejection_removes_the_complete_episode(self) -> None:
        arrays = {
            "node_role": np.asarray(
                [int(NodeRole.ROOT), int(NodeRole.MATERIAL_UPDATE)], dtype=np.int8
            ),
            "anchor_price": np.asarray([20.5, 21.0], dtype=np.float64),
            "episode_id": np.asarray([b"episode", b"episode"], dtype="S64"),
            "target_mask": np.asarray([True, True], dtype=np.bool_),
            "raw_metrics": np.ones((2, 14), dtype=np.float32),
        }
        result = enforce_exact_root_contract(arrays, root_max_price=20.0)
        self.assertEqual(result["exact_root_rejected_episodes"], 1)
        self.assertEqual(result["exact_root_rejected_rows"], 2)
        self.assertFalse(arrays["target_mask"].any())
        self.assertTrue(np.isnan(arrays["anchor_price"]).all())

    def test_interval_aggregation_is_bounded_and_preserves_target_metrics(self) -> None:
        friday = dt.date(2026, 7, 10)
        published = dt.datetime(2026, 7, 13, 13, 30, tzinfo=dt.timezone.utc)
        end = published + dt.timedelta(minutes=5)
        request = IntervalRequest(
            row_index=7,
            ticker="TEST",
            anchor_start_us=timestamp_us(
                dt.datetime(2026, 7, 10, 8, tzinfo=dt.timezone.utc)
            ),
            start_us=timestamp_us(published),
            end_us=timestamp_us(end),
        )
        sql = interval_aggregate_sql(LoaderConfig(), [request])
        self.assertIn("FROM VALUES(", sql)
        self.assertIn("e.sip_timestamp_us>=i.anchor_start_us", sql)
        self.assertIn("e.sip_timestamp_us<i.end_us", sql)
        self.assertIn("GROUP BY i.row_index,i.start_us,i.end_us", sql)
        self.assertIn("arrayFold(", sql)
        self.assertNotIn("FORMAT JSONEachRow", sql)

        cross_year = IntervalRequest(
            row_index=8,
            ticker="TEST",
            anchor_start_us=timestamp_us(
                dt.datetime(2025, 12, 31, 9, tzinfo=dt.timezone.utc)
            ),
            start_us=timestamp_us(
                dt.datetime(2025, 12, 31, 15, tzinfo=dt.timezone.utc)
            ),
            end_us=timestamp_us(
                dt.datetime(2026, 1, 2, 18, tzinfo=dt.timezone.utc)
            ),
        )
        cross_year_sql = interval_aggregate_sql(LoaderConfig(), [cross_year])
        self.assertIn("`events_2025`", cross_year_sql)
        self.assertIn("`events_2026`", cross_year_sql)

        aggregate = IntervalAggregate(
            row_index=7,
            anchor_price=9.9,
            high_price=10.2,
            high_timestamp_us=timestamp_us(
                published + dt.timedelta(seconds=2)
            ),
            low_price=10.0,
            low_timestamp_us=timestamp_us(
                published + dt.timedelta(seconds=1)
            ),
            terminal_price=10.1,
            vwap_price=10.1,
            peak_to_trough_return=-0.01,
            trough_to_peak_return=0.02,
            buy_notional=2_000,
            sell_notional=1_000,
            unknown_notional=0,
            observation_count=3,
        )
        raw, valid, anchor = raw_metrics_from_aggregate(
            request, aggregate
        )
        self.assertTrue(valid)
        self.assertEqual(anchor, 9.9)
        self.assertAlmostEqual(float(raw[0]), 9.9, places=5)
        self.assertAlmostEqual(float(raw[1]), 10.2 / 9.9 - 1, places=6)
        self.assertAlmostEqual(float(raw[2]), 10.0 / 9.9 - 1, places=6)
        self.assertAlmostEqual(float(raw[3]), 10.1 / 9.9 - 1, places=6)
        self.assertEqual(friday, dt.date(2026, 7, 10))

    def test_target_units_are_session_bounded_and_split_aware(self) -> None:
        friday = dt.date(2026, 7, 10)
        monday = dt.date(2026, 7, 13)
        tuesday = dt.date(2026, 7, 14)
        start = dt.datetime(2026, 7, 13, 13, 30, tzinfo=dt.timezone.utc)
        arrays = {
            "source_index": np.asarray([0, 1, 2], dtype=np.int32),
            "target_start_us": np.asarray(
                [timestamp_us(start), timestamp_us(start), timestamp_us(start)]
            ),
            "target_end_us": np.asarray(
                [
                    timestamp_us(start + dt.timedelta(minutes=5)),
                    timestamp_us(start + dt.timedelta(minutes=10)),
                    timestamp_us(start + dt.timedelta(minutes=15)),
                ]
            ),
        }
        v15 = {"ticker": np.asarray([b"AAA", b"BBB", b"CCC"], dtype="S8")}
        units, rejected = target_work_units(
            arrays,
            v15,
            [friday, monday, tuesday],
            {"CCC": frozenset({monday})},
            max_intervals=1,
            max_tickers=1,
            max_session_weight=2,
        )
        self.assertEqual(rejected, {2})
        self.assertEqual(len(units), 2)
        self.assertTrue(all(unit.anchor_day == friday for unit in units))
        self.assertTrue(all(len(unit.requests) == 1 for unit in units))
        self.assertTrue(
            all(
                request.anchor_start_us
                < request.start_us
                < request.end_us
                for unit in units
                for request in unit.requests
            )
        )

    def test_same_timestamp_follow_up_remains_masked_causal_context(self) -> None:
        result = audit_target_interval_contract(
            np.asarray([100, 200, 300], dtype=np.int64),
            np.asarray([100, 250, 350], dtype=np.int64),
            np.asarray([False, True, False], dtype=np.bool_),
        )
        self.assertEqual(result["empty_censored_intervals"], 1)
        self.assertEqual(result["positive_intervals"], 2)
        self.assertEqual(result["masked_positive_intervals"], 1)

    def test_empty_or_reversed_supervised_intervals_fail_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "supervised empty"):
            audit_target_interval_contract(
                np.asarray([100], dtype=np.int64),
                np.asarray([100], dtype=np.int64),
                np.asarray([True], dtype=np.bool_),
            )
        with self.assertRaisesRegex(RuntimeError, "reversed"):
            audit_target_interval_contract(
                np.asarray([100], dtype=np.int64),
                np.asarray([99], dtype=np.int64),
                np.asarray([False], dtype=np.bool_),
            )

    def test_anchor_audit_uses_the_declared_float32_storage_contract(self) -> None:
        exact = np.asarray([9.9, 281.05, np.nan], dtype=np.float64)
        stored = exact.astype(np.float32)
        mask = np.asarray([True, True, False], dtype=np.bool_)
        result = audit_anchor_storage_contract(exact, stored, mask)
        self.assertEqual(result["populated_anchors"], 2)
        self.assertGreater(result["maximum_float32_quantization_delta"], 1e-5)

        corrupted = stored.copy()
        corrupted[1] = np.nextafter(corrupted[1], np.float32(np.inf))
        with self.assertRaisesRegex(RuntimeError, "do not exactly encode"):
            audit_anchor_storage_contract(exact, corrupted, mask)

    def test_memory_limited_unit_is_split_without_changing_durable_identity(self) -> None:
        requests = tuple(
            IntervalRequest(index, f"T{index}", 800, 1000, 2000)
            for index in range(4)
        )
        unit = TargetWorkUnit(11, dt.date(2026, 7, 10), requests)

        def aggregate(_client, _config, chunk, cancellation=None):
            if len(chunk) > 1:
                raise RuntimeError("MEMORY_LIMIT_EXCEEDED")
            request = chunk[0]
            return {
                request.row_index: IntervalAggregate(
                    request.row_index,
                    10,
                    11,
                    1100,
                    9,
                    1200,
                    10.5,
                    10.2,
                    -0.1,
                    0.2,
                    60,
                    30,
                    10,
                    4,
                )
            }

        with patch(
            "research.news_reaction_model.v18.prepare_data.interval_aggregates",
            side_effect=aggregate,
        ):
            unit_index, rows, returned, queries = process_target_unit(
                config=LoaderConfig(workers=1),
                unit=unit,
                planning_anchors={index: 10.0 for index in range(4)},
                cancellation=CancellationController(),
                progress=TargetProgress(),
            )
        self.assertEqual(unit_index, 11)
        self.assertEqual(len(rows), 4)
        self.assertEqual(returned, 4)
        self.assertEqual(queries, 7)
        self.assertTrue(all(row[2] for row in rows))

    def test_classification_preserves_dominant_excursion_and_actual_returns(self) -> None:
        metrics = np.asarray(
            [10.0, 0.08, -0.02, 0.05, 0.2, 0.8, 0.03, -0.02, 0.08, 0.7, 0.2, 0.1, 50, 60],
            dtype=np.float32,
        )
        direction, path, flow, regression = classify(
            metrics, TargetThresholds(0.01)
        )
        self.assertEqual(direction, Direction.UPSIDE)
        self.assertEqual(path, Path.SUSTAINED)
        self.assertEqual(flow, Flow.DEMAND_DOMINANT)
        np.testing.assert_allclose(regression, [8.0, -2.0, 5.0])


class ModelTest(unittest.TestCase):
    def test_forward_shapes_and_cold_start_are_finite(self) -> None:
        config = ModelConfig(
            openai_embedding_dim=16,
            stock_state_dim=8,
            time_feature_dim=11,
            current_episode_feature_dim=15,
            context_size=8,
            context_feature_dim=17,
            d_model=24,
            hidden_dim=24,
            layers=1,
            attention_heads=6,
        )
        model = NewsReactionModelV18(config)
        batch = 3
        output = model(
            {
                "openai_embedding": torch.randn(batch, 16),
                "stock_state": torch.randn(batch, 8),
                "time_features": torch.randn(batch, 11),
                "current_episode_features": torch.randn(batch, 15),
                "channel_mask": torch.ones(batch, 4, dtype=torch.bool),
                "prior_openai_embeddings": torch.zeros(batch, 8, 16),
                "prior_context_features": torch.zeros(batch, 8, 17),
                "prior_context_mask": torch.zeros(batch, 8, dtype=torch.bool),
            }
        )
        self.assertEqual(output.direction_logits.shape, (batch, 3))
        self.assertEqual(output.path_logits.shape, (batch, 6))
        self.assertEqual(output.flow_logits.shape, (batch, 3))
        self.assertEqual(output.regression.shape, (batch, 3))
        self.assertTrue(torch.isfinite(output.article_embedding).all())


if __name__ == "__main__":
    unittest.main()
