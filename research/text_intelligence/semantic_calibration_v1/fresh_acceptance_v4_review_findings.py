from __future__ import annotations

from typing import Any


# These findings are the persisted result of reading every N1301-N1500 audit.
# They are review evidence and gold corrections, never inference exceptions.
V9_PASS_SAMPLE_IDS = frozenset({
    "N1314", "N1324", "N1326", "N1341", "N1355", "N1367",
    "N1388", "N1398", "N1408", "N1417", "N1420", "N1431",
    "N1435", "N1449", "N1454", "N1471", "N1480", "N1493",
})

GOLD_FINDINGS: dict[str, str] = {
    "N1305": "Mover recap contains eleven explicit issuer catalysts; zero issuer units was incomplete.",
    "N1311": "Premarket recap contains three explicit issuer catalysts; price-only Vodafone remains excluded.",
    "N1318": "Third-party commentary plus issuer results is editorial analysis, not direct analyst research.",
    "N1323": "Options-flow article contains issuer-specific contextual evidence even though it is not a causal trigger.",
    "N1327": "Benzinga reported the issuer metrics; issuer-direct provenance was unsupported.",
    "N1329": "A subsidiary IPO is both value realization and parent exposure reduction, so the parent direction is mixed.",
    "N1336": "A reported product article is not proven issuer-direct distribution.",
    "N1338": "Merger talks state no participant economics; both parties are neutral at this point.",
    "N1340": "The article explicitly identifies Reuters, so the source is editorial aggregation.",
    "N1351": "A maintained positive rating and a severe price-target cut form mixed analyst evidence.",
    "N1354": "The title explicitly identifies Verint and a convertible investment; identity-not-found was wrong.",
    "N1357": "A Benzinga debt headline is not the primary regulatory filing.",
    "N1358": "Named Ford, Honda and Toyota comparison subjects were omitted from issuer history.",
    "N1361": "A trading-venue alert is regulatory subject matter but not a primary regulatory document.",
    "N1366": "Seven movers explicitly identify same-day earnings context and require non-trigger history units.",
    "N1370": "Reported-earlier wording makes this a historical follow-up, not a new primary trigger.",
    "N1376": "Benzinga reported an FDA state change; the article itself is not an FDA primary document.",
    "N1386": "Options-flow article contains issuer-specific contextual evidence despite being non-triggering.",
    "N1391": "The article reports issuer letters and transaction analysis; it is editorial aggregation, and VRX evidence is mixed.",
    "N1392": "The copied issuer announcement is issuer-direct, not the bankruptcy court filing itself.",
    "N1395": "The venue-halt alert is reported by Benzinga, not a primary regulatory document.",
    "N1396": "The Reuters/CFO report is editorial aggregation, not issuer-direct distribution.",
    "N1399": "The Bloomberg-cited sale report is editorial aggregation.",
    "N1403": "A generated stocks-moving list is a mover recap, not a generic automated summary.",
    "N1405": "EnerNOC is one of the three explicit editorial theses and was omitted.",
    "N1426": "The Barron's report is editorial aggregation rather than original reporting.",
    "N1427": "A generated stocks-moving list is a mover recap, not a generic automated summary.",
    "N1436": "A generated stocks-moving list is a mover recap, not a generic automated summary.",
    "N1444": "Investor commentary reported from a social post is editorial analysis, not direct analyst research.",
    "N1445": "The article is reported editorial content; duplicate market symbols for one issuer must not create duplicate semantics.",
    "N1472": "A standardized earnings report is editorial aggregation, not issuer-direct distribution.",
    "N1479": "Post-results editorial analysis is issuer history, not an independent forecast/reaction trigger.",
    "N1492": "The tax-bill article is editorial reporting of regulation, not a primary regulatory document.",
    "N1495": "The Reuters macro article is editorial aggregation.",
    "N1496": "The Reuters macro article is editorial aggregation.",
    "N1498": "A Zacks analyst blog is contextual editorial analysis, not an independent issuer-event trigger.",
}

SOURCE_ISSUES: dict[str, str] = {
    "N1307": "External lane contains transport/anti-bot text unrelated to the article.",
    "N1328": "External lane contains unrelated stale FreightWaves page content.",
    "N1343": "External lane contains transport/anti-bot text unrelated to the article.",
    "N1381": "External lane contains a domain-for-sale/captcha response rather than article content.",
}

METADATA_ISSUES: dict[str, str] = {
    "N1354": "Provider supplied no ticker although the title names Verint unambiguously.",
    "N1445": "Provider/identity evidence exposes dual symbols for one issuer.",
    "N1473": "Article text contains an apparent issuer-action typo and requires explicit quality handling.",
    "N1483": "Provider uses US and Canadian symbols for the same issuer.",
}


def build_review_specs() -> list[dict[str, Any]]:
    """Return exactly 200 reviewer-authored decisions for persistence."""
    records: list[dict[str, Any]] = []
    for number in range(1301, 1501):
        sample_id = f"N{number}"
        gold_note = GOLD_FINDINGS.get(sample_id)
        source_note = SOURCE_ISSUES.get(sample_id)
        metadata_note = METADATA_ISSUES.get(sample_id)
        v9_pass = sample_id in V9_PASS_SAMPLE_IDS
        issue_codes: list[str] = []
        if gold_note:
            issue_codes.append("gold_reference_defect")
        if not v9_pass:
            issue_codes.append("v9_generic_rule_mismatch")
        if source_note:
            issue_codes.append("source_lane_quality_defect")
        if metadata_note:
            issue_codes.append("metadata_or_identity_defect")
        notes = [
            "Manual review covered original metadata, rendered text, every gold field, and every candidate-20 output."
        ]
        notes.extend(value for value in (gold_note, metadata_note, source_note) if value)
        if v9_pass:
            notes.append("Candidate 20 matched every evaluator-scored field.")
        else:
            notes.append("Candidate 20 requires a generic rule repair; no source ID or exact headline exception is authorized.")
        records.append({
            "sample_id": sample_id,
            "gold_status": "correction_required" if gold_note else "pass",
            "v9_status": "pass" if v9_pass else "fix_required",
            "metadata_status": "issue" if metadata_note else "pass",
            "source_status": "issue" if source_note else "pass",
            "issue_codes": issue_codes,
            "proposed_fix_families": [
                value for value, enabled in (
                    ("reviewed_gold_repair", bool(gold_note)),
                    ("generic_v9_semantic_authority", not v9_pass),
                    ("source_quality_authority", bool(source_note)),
                    ("point_in_time_identity_authority", bool(metadata_note)),
                ) if enabled
            ],
            "notes": " ".join(notes),
            "gold_corrections": {"review_finding": gold_note} if gold_note else {},
        })
    return records
