from __future__ import annotations

import unittest

from src.trading_runtime.taxonomy import (
    ClockContract,
    EvaluationMode,
    IndicatorDefinition,
    IndicatorType,
    InputBasis,
    PublicationCadence,
    SignalDefinition,
    SignalDomain,
    StrategyTaxonomy,
    UpdateTrigger,
    taxonomy_catalog_payload,
)


class TradingTaxonomyTests(unittest.TestCase):
    def test_interval_publication_requires_an_explicit_positive_cadence(self) -> None:
        with self.assertRaisesRegex(ValueError, "publication_interval_ms"):
            ClockContract(
                input_basis=InputBasis.EVENT_NATIVE,
                evaluation_mode=EvaluationMode.DEVELOPING,
                update_trigger=UpdateTrigger.MARKET_EVENT,
                publication_cadence=PublicationCadence.INTERVAL,
            )

        clock = ClockContract(
            input_basis=InputBasis.EVENT_NATIVE,
            calculation_window="100ms",
            evaluation_mode=EvaluationMode.DEVELOPING,
            update_trigger=UpdateTrigger.MARKET_EVENT,
            publication_cadence=PublicationCadence.INTERVAL,
            publication_interval_ms=100,
        )
        self.assertEqual(clock.publication_interval_ms, 100)

    def test_qmd_is_an_indicator_type_and_not_a_signal_domain(self) -> None:
        catalog = taxonomy_catalog_payload()
        self.assertIn("qmd", catalog["indicator_types"])
        self.assertNotIn("qmd", catalog["signal_domains"])
        self.assertEqual(catalog["signal_domains"], ["market", "news", "sec", "model"])

    def test_every_signal_definition_requires_a_rankable_score(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a score"):
            SignalDefinition(
                signal_id="market.vwap_reclaim",
                label="VWAP reclaim",
                domain=SignalDomain.MARKET,
                producer="qmd",
                clock=ClockContract(
                    input_basis=InputBasis.BAR_DERIVED,
                    calculation_window="10s",
                    evaluation_mode=EvaluationMode.CLOSED_ONLY,
                    update_trigger=UpdateTrigger.BAR_CLOSE,
                    publication_cadence=PublicationCadence.BAR_CLOSE,
                ),
                score_required=False,
            )

    def test_strategy_contract_keeps_inputs_separate_from_presentation(self) -> None:
        taxonomy = StrategyTaxonomy.from_payload(
            {
                "indicators": [{"key": "qmd.flow_imbalance"}],
                "signals": [{"key": "market.vwap_reclaim"}],
                "allow_developing_inputs": True,
                "evaluation_trigger": "signal_event",
                "presentation": {"show_holds": True, "label": "Continuation"},
            }
        )

        self.assertEqual(taxonomy.indicators[0].key, "qmd.flow_imbalance")
        self.assertEqual(taxonomy.signals[0].key, "market.vwap_reclaim")
        self.assertTrue(taxonomy.presentation.show_holds)
        self.assertEqual(taxonomy.presentation.label, "Continuation")

    def test_indicator_definition_declares_type_producer_outputs_and_clock(self) -> None:
        definition = IndicatorDefinition(
            indicator_id="qmd.flow_imbalance",
            label="Flow imbalance",
            indicator_type=IndicatorType.QMD,
            producer="qmd",
            outputs=("flow_imbalance",),
            clock=ClockContract(
                input_basis=InputBasis.EVENT_NATIVE,
                calculation_window="100ms",
                evaluation_mode=EvaluationMode.DEVELOPING,
                update_trigger=UpdateTrigger.MARKET_EVENT,
                publication_cadence=PublicationCadence.INTERVAL,
                publication_interval_ms=100,
            ),
        )

        payload = definition.payload()
        self.assertEqual(payload["indicator_type"], "qmd")
        self.assertEqual(payload["clock"]["input_basis"], "event_native")


if __name__ == "__main__":
    unittest.main()
