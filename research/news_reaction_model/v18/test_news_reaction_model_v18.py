from __future__ import annotations

import unittest
import datetime as dt
from collections import defaultdict

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
from research.news_reaction_model.v18.prepare_data import Article, consume_article
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
