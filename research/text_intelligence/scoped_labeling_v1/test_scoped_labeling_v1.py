from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from research.text_intelligence.semantic_label_authority_v1.schema import (
    SemanticDocument,
)

from .news_extractor import analyze_news_scope, extract_news_units
from .news_identity import IssuerIdentity, NewsIssuerResolver
from .pipeline import classify_news_document, classify_sec_document
from .sec_extractor import extract_sec_units
from .persistence import (
    assert_certification,
    bounded_period_ranges,
    relationship_rows,
)
from .schema import SCOPED_LABELING_VERSION


class ScopedLabelingTests(unittest.TestCase):
    @staticmethod
    def issuer_resolver() -> NewsIssuerResolver:
        return NewsIssuerResolver(
            (
                IssuerIdentity(
                    ticker="EXMP",
                    issuer_id="issuer-example",
                    aliases=(
                        "Example Therapeutics, Inc.",
                        "Example Therapeutics",
                        "Example",
                    ),
                ),
                IssuerIdentity(
                    ticker="EXM.A",
                    issuer_id="issuer-example",
                    aliases=("Example Therapeutics Class A",),
                ),
                IssuerIdentity(
                    ticker="OTHR",
                    issuer_id="issuer-other",
                    aliases=("Other Corp",),
                ),
                IssuerIdentity(
                    ticker="AAPL",
                    issuer_id="issuer-apple",
                    aliases=("Apple Inc.", "Apple"),
                ),
                IssuerIdentity(
                    ticker="GS",
                    issuer_id="issuer-goldman",
                    aliases=("Goldman Sachs Group, Inc.", "Goldman Sachs"),
                ),
            )
        )

    def test_persistence_windows_are_bounded_and_exact(self) -> None:
        self.assertEqual(
            bounded_period_ranges("2026-07-01", "2026-07-12", 7),
            [
                ("2026-07-01", "2026-07-08"),
                ("2026-07-08", "2026-07-12"),
            ],
        )

    def test_persistence_requires_matching_clean_certification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "labeling_version": SCOPED_LABELING_VERSION,
                        "news_audits": 5,
                        "sec_audits": 5,
                        "review_attention": 0,
                        "missing_news_scope_cases": [],
                        "expected_outcome_failures": [],
                    }
                ),
                encoding="utf-8",
            )
            assert_certification(path)
            path.write_text("{}", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                assert_certification(path)

    def test_roundup_creates_ticker_specific_observations(self) -> None:
        text = """Title: 42 Stocks Moving in Wednesday's Pre-Market Session
Body:
- Cancer Genetics, Inc. (NASDAQ:CGIX) shares rose 200.7% to $17.35 in pre-market trading after reporting a $10 million private placement.
- Other Corp (NYSE:OTHR) shares fell 12.5% to $4.20 after lowering guidance.
"""
        units = extract_news_units(
            source_id="19582725",
            title="42 Stocks Moving in Wednesday's Pre-Market Session",
            text=text,
            tickers=("CGIX", "OTHR"),
        )
        self.assertEqual(len(units), 2)
        self.assertEqual(units[0].tickers, ("CGIX",))
        self.assertEqual(units[0].observed_reaction.direction, "up")
        self.assertEqual(units[0].observed_reaction.move_pct, 200.7)
        self.assertEqual(units[0].observed_reaction.resulting_price, 17.35)
        self.assertEqual(
            units[0].reported_catalyst,
            "reporting a $10 million private placement",
        )

    def test_roundup_context_can_never_be_reaction_target(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="news-roundup",
            timestamp="2026-07-28T12:00:00Z",
            title="50 Biggest Movers From Friday",
            text=(
                "Body:\n- Example Corp (NASDAQ:EXMP) shares rose 25% "
                "to $5.00 after receiving FDA approval."
            ),
            tickers=("EXMP",),
            metadata={"channels": ["Movers"], "author": "Benzinga"},
        )
        labels = classify_news_document(document)
        self.assertEqual(len(labels), 1)
        self.assertFalse(labels[0].forecast_trigger_eligible)
        self.assertFalse(labels[0].reaction_evaluation_eligible)
        self.assertTrue(labels[0].issuer_history_context_eligible)
        self.assertIn(
            "regulatory.fda_approval",
            {
                f"{item['family']}.{item['subtype']}"
                for item in labels[0].semantic["labels"]
            },
        )

    def test_multi_ticker_unscoped_prose_is_not_assigned(self) -> None:
        units = extract_news_units(
            source_id="multi",
            title="Technology shares move",
            text="Body: Several technology companies moved in active trading.",
            tickers=("AAAA", "BBBB"),
        )
        self.assertFalse(units)

    def test_single_ticker_article_is_one_document_unit(self) -> None:
        units = extract_news_units(
            source_id="single",
            title="Example raises guidance",
            text=(
                "Body: Example announced that it raised guidance.\n"
                "Revenue also increased year over year."
            ),
            tickers=("EXMP",),
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(len(units), 1)
        self.assertIn("Revenue also increased", units[0].text)
        self.assertEqual(units[0].role, "primary_or_editorial_document")

    def test_corporate_guidance_upgrade_is_not_an_analyst_action(self) -> None:
        units = extract_news_units(
            source_id="guidance-upgrade",
            title="Example Therapeutics upgrades guidance",
            text=(
                "Body: Example Therapeutics upgraded its revenue guidance "
                "after stronger demand."
            ),
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].role, "primary_or_editorial_document")

    def test_single_provider_link_does_not_hide_mixed_issuer_article(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="single-link-mixed",
            timestamp="2026-07-28T12:00:00Z",
            title="Example Therapeutics and Other Corp report updates",
            text=(
                "Body: Example Therapeutics announced that it raised guidance. "
                "Other Corp (NYSE:OTHR) announced a registered direct offering."
            ),
            tickers=("EXMP",),
            metadata={"author": "Editorial Desk"},
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual({label.ticker for label in labels}, {"EXMP", "OTHR"})
        self.assertTrue(all(label.forecast_trigger_eligible for label in labels))
        self.assertEqual(
            {label.unit_role for label in labels},
            {"issuer_event_document"},
        )

    def test_analyst_firm_is_not_treated_as_action_target(self) -> None:
        analysis = analyze_news_scope(
            source_id="analyst-target",
            title="Goldman Sachs upgrades Apple",
            text=(
                "Body: Goldman Sachs upgraded Apple Inc. to Buy and raised "
                "its price target to $250."
            ),
            tickers=("AAPL",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.resolved_subjects, ("AAPL",))
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")
        self.assertEqual(analysis.units[0].tickers, ("AAPL",))
        self.assertEqual(analysis.units[0].role, "analyst_opinion")

    def test_unresolved_company_like_passage_does_not_inherit_single_link(self) -> None:
        analysis = analyze_news_scope(
            source_id="unresolved-peer",
            title="Example Therapeutics reports an update",
            text=(
                "Body: Example Therapeutics raised guidance. "
                "Mystery Holdings Corp. announced a separate offering."
            ),
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(
            analysis.document_decision,
            "unresolved_issuer_passage_abstention",
        )
        self.assertEqual(len(analysis.units), 1)
        self.assertEqual(analysis.units[0].tickers, ("EXMP",))
        self.assertIn("Mystery Holdings", analysis.units[0].text)
        self.assertNotIn(
            "Mystery Holdings", analysis.units[0].semantic_text
        )
        unresolved = [
            passage
            for passage in analysis.passages
            if passage.decision == "abstained_unresolved_company_mention"
        ]
        self.assertEqual(len(unresolved), 1)

    def test_single_link_without_text_resolved_subject_abstains(self) -> None:
        analysis = analyze_news_scope(
            source_id="metadata-only",
            title="Quarterly update",
            text="Body: The company discussed general market conditions.",
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.document_decision, "abstained_no_resolved_issuer")
        self.assertFalse(analysis.units)

    def test_article_local_exchange_pair_resolves_historical_issuer_name(self) -> None:
        resolver = NewsIssuerResolver(())
        analysis = analyze_news_scope(
            source_id="historical-name",
            title="Salarius Pharmaceuticals reports FDA update",
            text=(
                "Body: Salarius Pharmaceuticals, Inc. (NASDAQ:SLRX) "
                "announced that the FDA removed its partial clinical hold."
            ),
            tickers=("SLRX",),
            timestamp="2023-05-09T12:02:54Z",
            issuer_resolver=resolver,
        )
        self.assertEqual(analysis.resolved_subjects, ("SLRX",))
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")

    def test_unresolved_counterparty_does_not_erase_known_issuer_event(self) -> None:
        analysis = analyze_news_scope(
            source_id="spac-termination",
            title="Pine Technology Acquisition Corp. terminates merger",
            text=(
                "Body: Pine Technology Acquisition Corp. "
                "(NASDAQ:PTOC, PTOCW, PTOCU) and The Tomorrow Companies Inc. "
                "agreed to terminate their merger agreement."
            ),
            tickers=("PTOC",),
            timestamp="2022-03-07T12:11:37Z",
            issuer_resolver=NewsIssuerResolver(()),
        )
        self.assertEqual(
            analysis.document_decision,
            "unresolved_issuer_passage_abstention",
        )
        self.assertEqual(len(analysis.units), 1)
        self.assertEqual(analysis.units[0].tickers, ("PTOC",))
        self.assertEqual(analysis.units[0].evidence_scope, "shared_ambiguous")
        self.assertTrue(any(
            passage.decision
            == "assigned_known_issuer_with_unresolved_counterparty"
            for passage in analysis.passages
        ))

    def test_external_enrichment_cannot_change_publication_time_subject(self) -> None:
        analysis = analyze_news_scope(
            source_id="external-enrichment",
            title="Example Therapeutics raises guidance",
            text=(
                "Title: Example Therapeutics raises guidance\n"
                "Source [provider_body:0] https://provider.test/article\n"
                "Example Therapeutics, Inc. (NASDAQ:EXMP) raised guidance.\n"
                "Source [external:1]\n"
                "Example Therapeutics collaborates with Other Corp and Apple Inc."
            ),
            tickers=("EXMP",),
            timestamp="2026-07-28T12:00:00Z",
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.resolved_subjects, ("EXMP",))
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")
        self.assertNotIn("Other Corp", analysis.units[0].text)
        self.assertNotIn("Apple Inc", analysis.units[0].text)
        self.assertTrue(any(
            passage.decision == "abstained_external_enrichment"
            for passage in analysis.passages
        ))

    def test_multi_issuer_acquisition_keeps_full_text_and_scopes_labels(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("ALC", "alcon", ("Alcon AG", "Alcon")),
            IssuerIdentity(
                "AERI",
                "aerie",
                ("Aerie Pharmaceuticals Inc", "Aerie Pharmaceuticals", "Aerie"),
            ),
        ))
        document = SemanticDocument(
            corpus="news",
            source_id="acquisition-analyst",
            timestamp="2022-08-23T19:26:34Z",
            title=(
                "Alcon May Struggle To Meet Margin Targets With This "
                "Latest Acquisition, Says This Analyst"
            ),
            text=(
                "Source [provider_body:0]\n"
                "Alcon AG (NYSE:ALC) agreed to acquire Aerie Pharmaceuticals "
                "Inc (NASDAQ:AERI) for $770 million.\n"
                "The deal could make it more difficult for ALC to reach its "
                "operating margin targets and be dilutive to operating margin.\n"
                "Needham downgraded AERI to Hold from Buy."
            ),
            tickers=("AERI", "ALC"),
            metadata={"author": "Benzinga Analyst Ratings"},
        )
        labels = classify_news_document(document, issuer_resolver=resolver)
        self.assertEqual({item.ticker for item in labels}, {"AERI", "ALC"})
        self.assertTrue(all(item.forecast_trigger_eligible for item in labels))
        self.assertEqual(
            {item.ticker: item.issuer_role for item in labels},
            {"ALC": "acquirer", "AERI": "target"},
        )
        self.assertEqual(
            len({item.publication_text_hash for item in labels}),
            1,
        )
        concepts = {
            item.ticker: set(item.classification["event_concepts"])
            for item in labels
        }
        self.assertIn("ma_transaction.acquisition", concepts["ALC"])
        self.assertIn("ma_transaction.acquisition", concepts["AERI"])
        self.assertIn("profitability.margin_pressure", concepts["ALC"])
        self.assertNotIn("profitability.margin_pressure", concepts["AERI"])
        self.assertIn("analyst_action.downgrade", concepts["AERI"])

    def test_unresolved_background_does_not_disable_resolved_event(
        self,
    ) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="scope-gate",
            timestamp="2026-07-14T12:00:00Z",
            title="Example Therapeutics Announces Positive Trial Results",
            text=(
                "Example Therapeutics, Inc. (NASDAQ:EXMP) announced positive "
                "Phase 3 trial results.\n"
                "Unresolved Biopharma Inc. will participate in the program."
            ),
            tickers=("EXMP",),
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertTrue(labels)
        self.assertTrue(all(
            label.forecast_trigger_eligible
            and label.reaction_evaluation_eligible
            for label in labels
        ))
        self.assertTrue(all(
            "event_scoped_eligibility_v3"
            in label.classification["quality_flags"]
            for label in labels
        ))

    def test_multiple_provider_symbols_for_one_issuer_remain_trigger_safe(
        self,
    ) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="same-issuer-symbols",
            timestamp="2026-07-14T12:00:00Z",
            title="Example Therapeutics Announces Positive Trial Results",
            text=(
                "Example Therapeutics, Inc. (NASDAQ:EXMP) announced that its "
                "Phase 3 trial met the primary endpoint."
            ),
            tickers=("EXMP", "EXM.A"),
        )
        labels = classify_news_document(
            document,
            issuer_resolver=self.issuer_resolver(),
        )
        analysis = analyze_news_scope(
            source_id=document.source_id,
            title=document.title,
            text=document.text,
            tickers=document.tickers,
            timestamp=document.timestamp,
            issuer_resolver=self.issuer_resolver(),
        )
        self.assertEqual(analysis.document_decision, "single_resolved_issuer")
        self.assertEqual(len(labels), 1)
        self.assertNotIn(
            "document_issuer_scope_not_trigger_safe",
            labels[0].classification["quality_flags"],
        )

    def test_multi_ticker_independent_events_are_each_trigger_eligible(self) -> None:
        document = SemanticDocument(
            corpus="news",
            source_id="multi-scoped",
            timestamp="2026-07-28T12:00:00Z",
            title="Two healthcare companies report updates",
            text=(
                "Example Corp (NASDAQ:EXMP) received FDA approval.\n"
                "Other Corp (NYSE:OTHR) announced a public offering."
            ),
            tickers=("EXMP", "OTHR"),
            metadata={"author": "Editorial Desk"},
        )
        labels = classify_news_document(document)
        self.assertEqual(len(labels), 2)
        self.assertTrue(all(item.forecast_trigger_eligible for item in labels))
        self.assertTrue(all(item.issuer_history_context_eligible for item in labels))

    def test_sec_extractor_ignores_signature_and_keeps_event(self) -> None:
        text = """ITEM 1.01
The registrant entered into a registered direct offering for $25 million.
SIGNATURES
Pursuant to the requirements of the Securities Exchange Act, the registrant signed this report.
"""
        units = extract_sec_units(
            source_id="sec-1",
            title="8-K",
            text=text,
            ticker="EXMP",
            metadata={"document_role": "primary_document"},
        )
        self.assertEqual(len(units), 1)
        self.assertIn("registered direct offering", units[0].text)
        self.assertNotIn("signed this report", units[0].text)

    def test_sec_labels_only_relevant_units(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="sec-2",
            timestamp="2026-07-28T12:00:00Z",
            title="Example 8-K EX-99.1",
            text=(
                "BUSINESS UPDATE\n"
                "The company announced a registered direct offering.\n"
                "FORWARD-LOOKING STATEMENTS\n"
                "These statements involve risks and uncertainties."
            ),
            tickers=("EXMP",),
            metadata={
                "form_type": "8-K",
                "document_type": "EX-99.1",
                "document_role": "press_release_exhibit",
                "text_kind": "press_release_exhibit",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        self.assertEqual(len(labels), 1)
        self.assertIn(
            "financing.registered_direct",
            labels[0].classification["event_concepts"],
        )

    def test_sec_event_concepts_cover_certified_missing_cases(self) -> None:
        cases = (
            (
                "The company reached settlement with Mylan.",
                "legal.settlement",
            ),
            (
                "The Company maintains an Employee Share Purchase Plan "
                "and desires to amend the Plan.",
                "management_governance.employee_share_purchase_plan_amendment",
            ),
            (
                "The company entered a securities purchase agreement "
                "for shares of preferred stock and purchase warrants.",
                "financing.preferred_stock_private_placement",
            ),
        )
        for index, (text, concept) in enumerate(cases):
            document = SemanticDocument(
                corpus="sec",
                source_id=f"sec-concept-{index}",
                timestamp="2026-07-28T12:00:00Z",
                title="Example 8-K exhibit",
                text=text,
                tickers=("EXMP",),
                metadata={
                    "form_type": "8-K",
                    "document_type": "EX-99.1",
                    "document_role": "press_release_exhibit",
                    "text_kind": "press_release_exhibit",
                },
            )
            labels = classify_sec_document(document)
            self.assertIn(
                concept,
                {
                    value
                    for label in labels
                    for value in label.classification["event_concepts"]
                },
            )

    def test_relationship_rows_normalize_graph_without_publication_text(self) -> None:
        resolver = NewsIssuerResolver((
            IssuerIdentity("ALC", "alcon", ("Alcon AG", "Alcon")),
            IssuerIdentity("AERI", "aerie", ("Aerie Pharmaceuticals",)),
        ))
        document = SemanticDocument(
            corpus="news",
            source_id="graph-acquisition",
            timestamp="2022-08-23T19:26:34Z",
            title="Alcon acquisition",
            text=(
                "Alcon AG (NYSE:ALC) agreed to acquire "
                "Aerie Pharmaceuticals (NASDAQ:AERI)."
            ),
            tickers=("ALC", "AERI"),
        )
        labels = classify_news_document(document, issuer_resolver=resolver)
        relations = [
            row for label in labels
            for row in relationship_rows(document, label, "test-run")
        ]
        self.assertTrue(any(
            row["relation_type"] == "affects_issuer"
            and row["relation_role"] == "acquirer"
            for row in relations
        ))
        self.assertTrue(any(
            row["relation_type"] == "affects_issuer"
            and row["relation_role"] == "target"
            for row in relations
        ))
        self.assertTrue(all("text" not in row for row in relations))

    def test_generic_purchase_order_disclosure_is_not_contract_award(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="sec-background",
            timestamp="2026-07-28T12:00:00Z",
            title="Annual report",
            text="Purchase orders are used in the ordinary course of business.",
            tickers=("EXMP",),
            metadata={
                "form_type": "10-K",
                "document_type": "10-K",
                "document_role": "primary_document",
                "text_kind": "primary_document",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        concepts = {
            concept
            for label in labels
            for concept in label.classification["event_concepts"]
        }
        self.assertNotIn("contract_order.award", concepts)

    def test_form_four_exercise_price_is_not_financing_event(self) -> None:
        document = SemanticDocument(
            corpus="sec",
            source_id="form-4",
            timestamp="2026-07-28T12:00:00Z",
            title="Example Form 4",
            text=(
                "Common Stock\nTransaction code M\n"
                "Warrant conversion or exercise price $2.50\n"
                "Performance Shares"
            ),
            tickers=("EXMP",),
            metadata={
                "form_type": "4",
                "document_type": "4",
                "document_role": "primary_document",
                "text_kind": "primary_document",
                "accepted_at_utc": "2026-07-28T12:00:00Z",
            },
        )
        labels = classify_sec_document(document)
        self.assertTrue(labels)
        self.assertTrue(all(
            item.classification["content_role"] == "ownership_transaction"
            for item in labels
        ))
        self.assertTrue(all(not item.forecast_trigger_eligible for item in labels))
        self.assertNotIn(
            "financing.warrant",
            {
                concept
                for item in labels
                for concept in item.classification["event_concepts"]
            },
        )


if __name__ == "__main__":
    unittest.main()
