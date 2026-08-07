from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REGISTRY_PATH = Path(__file__).with_name("concept_registry.json")


# Ordered broad semantic families used only to prepare migration review
# candidates from the legacy free-form concept vocabulary. They deliberately
# resolve to existing coarse V1 leaves; a heuristic result still requires
# manual certification and is never production authority by itself.
_HEURISTIC_FAMILIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:price_target|target_price)"), "analyst.price_target_action"),
    (re.compile(r"(?:rating|upgrade|downgrade|initiated_coverage|coverage_initiated)"), "analyst.rating_action"),
    (re.compile(r"(?:short_seller|short_thesis|bearish_thesis)"), "analyst.short_thesis"),
    (re.compile(r"^analyst|estimate_revision|channel_check"), "analyst.issuer_assessment"),
    (re.compile(r"(?:reverse_split|delist|listing|share_class|public_conversion|spac|ipo|stock_split)"), "listing.market_structure"),
    (re.compile(r"(?:repurchase|buyback|dividend|capital_return|shareholder_returns)"), "capital.return"),
    (re.compile(r"(?:deleverag|debt_reduction|pay_down_debt)"), "capital.deleveraging"),
    (re.compile(r"(?:offering|financing|capital_rais|capital_raised|private_placement|convertible_note|senior_note|preferred_equity|share_issuance|warrant_exercise|resale_registration)"), "capital.financing"),
    (re.compile(r"(?:capital_structure|equity_subordination|share_conversion|warrants_issued)"), "capital.structure"),
    (re.compile(r"(?:acquisition|takeover|m_and_a|merger|tender_offer|acquirer)"), "corporate_transaction.acquisition"),
    (re.compile(r"(?:asset_sale|divestiture|disposal|portfolio_sale|noncore_asset)"), "corporate_transaction.asset_sale"),
    (re.compile(r"(?:strategic_alternative|strategic_review|sale_exploration|sale_process)"), "strategy.strategic_alternatives"),
    (re.compile(r"(?:phase\d|phase_\d|clinical|preclinical|endpoint|efficacy|trial|study_result|immune_response|toxicity)"), "clinical.trial_result"),
    (re.compile(r"(?:fda|nda_|pdufa|ind_clearance|regulatory_submission|boxed_warning|advisory_committee|expanded_use_submission)"), "clinical.regulatory_milestone"),
    (re.compile(r"(?:cyber|data_breach|ransomware)"), "technology.cybersecurity_incident"),
    (re.compile(r"(?:patent|lawsuit|legal|litigation|subpoena|verdict|fraud|damages|investigation|settlement|liability)"), "legal.proceeding"),
    (re.compile(r"(?:bankrupt|solvency|going_concern|covenant|credit_risk|high_leverage|balance_sheet_risk)"), "credit.solvency"),
    (re.compile(r"(?:internal_control|material_weakness|control_deficien)"), "financial.internal_control"),
    (re.compile(r"(?:restatement|annual_report_filed|filing_delay|accounting_restatement)"), "earnings.restatement"),
    (re.compile(r"(?:guidance|outlook|billings_growth_expected|same_store_sales_growth_expected|deliveries_expected|costs_increase_expected)"), "guidance.issued"),
    (re.compile(r"(?:earnings|quarterly_profit|quarterly_loss|net_income|net_loss|operating_income|ebitda|gross_profit|comparable_sales|same_store_sales|sales_growth|bookings_growth|revenue)"), "earnings.performance"),
    (re.compile(r"(?:margin|pricing_pressure|cost_pressure|commodity_cost|tariff_headwind|tax_headwind)"), "financial.margin"),
    (re.compile(r"(?:cash_flow)"), "financial.cash_flow"),
    (re.compile(r"(?:liquidity|cash_balance|working_capital)"), "financial.liquidity"),
    (re.compile(r"(?:impairment|writeoff|write_down|financial_charge|loss_exposure|catastrophe_losses)"), "financial.loss_exposure"),
    (re.compile(r"(?:interest_rate|interest_cost|interest_expense)"), "financial.interest_rate"),
    (re.compile(r"(?:credit_quality|delinquen|charge_off|default_rate)"), "financial.credit_quality"),
    (re.compile(r"(?:return_on_equity|return_on_assets|roce|financial_metric|fundamentals|profitability|sales_|revenue_|expenses_)"), "financial.operating_performance"),
    (re.compile(r"(?:insider|institutional_purchase|position_added|position_closed|position_opened|position_reduced|profit_taken|portfolio_purchase|stake_)"), "ownership.position_change"),
    (re.compile(r"(?:short_interest|short_percent|days_to_cover)"), "market.short_interest_observed"),
    (re.compile(r"(?:options_activity|options_sentiment)"), "market.options_activity"),
    (re.compile(r"(?:technical|momentum|golden_cross|death_cross|overbought|downtrend|long_term_trend)"), "market.technical_analysis"),
    (re.compile(r"(?:market_reaction|observed_gain|observed_loss|52_week|premarket_mover|sector_mover|relative_performance|stock_move)"), "market.price_move_observed"),
    (re.compile(r"(?:limited_float|share_overhang|lockup_expiration|secondary_shareholder_sale|market_supply)"), "market.context"),
    (re.compile(r"(?:contract|order|award|services_agreement|system_purchase|commercial_commitment)"), "commercial.contract"),
    (re.compile(r"(?:partnership|collaboration|license|licensing|distribution|supply_agreement|marketing_agreement|joint_venture|mou)"), "commercial.partnership"),
    (re.compile(r"(?:competition|competitive|market_share|commercial_performance)"), "commercial.competitive_position"),
    (re.compile(r"(?:demand|bookings|preorders|pipeline|sales_potential|capacity_sold_out)"), "commercial.demand_condition"),
    (re.compile(r"(?:product|commercial_availability|commercialization|technology_commercialization|intellectual_property_extension)"), "product.milestone"),
    (re.compile(r"(?:workforce|layoff|employee|hiring)"), "operations.workforce"),
    (re.compile(r"(?:capacity_expansion|manufacturing_expansion|store_openings|network_expansion|infrastructure_expansion)"), "operations.capacity_change"),
    (re.compile(r"(?:cost_saving|cost_reduction|efficien|expenses_increase)"), "operations.cost_efficiency"),
    (re.compile(r"(?:operations|business_update|supply_chain|manufacturing|store_closure|restructur|commercial_expansion|regional_|international_expansion)"), "operations.business_update"),
    (re.compile(r"(?:management|leadership|executive|board_change|auditor_departure)"), "governance.management_change"),
    (re.compile(r"(?:activist|shareholder_vote|annual_meeting|governance_proposal)"), "governance.shareholder_vote"),
    (re.compile(r"(?:valuation|investment_opportunity|upside|downside|multiple)"), "strategy.valuation_assessment"),
    (re.compile(r"(?:portfolio|investment_thesis)"), "strategy.portfolio_assessment"),
    (re.compile(r"(?:strategy|growth|expansion|transformation|priority|focus)"), "strategy.operational_priority"),
    (re.compile(r"(?:government_funding|grant_awarded|subsidy|policy_)"), "regulatory.action"),
    (re.compile(r"(?:conference|presentation|shareholder_letter|corporate_communication)"), "corporate.communication_event"),
)


@dataclass(frozen=True, slots=True)
class ConceptLeaf:
    concept_id: str
    parent: str
    definition: str
    aliases: tuple[str, ...]


class ConceptRegistry:
    def __init__(self, version: str, fallback_leaf: str, leaves: Iterable[ConceptLeaf]) -> None:
        self.version = version
        self.fallback_leaf = fallback_leaf
        self._leaves = {leaf.concept_id: leaf for leaf in leaves}
        self._aliases: dict[str, str] = {}
        for leaf in self._leaves.values():
            self._aliases[_key(leaf.concept_id)] = leaf.concept_id
            for alias in leaf.aliases:
                self._aliases[_key(alias)] = leaf.concept_id
        if fallback_leaf not in self._leaves:
            raise ValueError(f"Fallback leaf is not registered: {fallback_leaf}")

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "ConceptRegistry":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            str(payload["registry_version"]),
            str(payload["fallback_leaf"]),
            (
                ConceptLeaf(
                    concept_id=str(row["id"]),
                    parent=str(row["parent"]),
                    definition=str(row["definition"]),
                    aliases=tuple(str(value) for value in row.get("aliases", [])),
                )
                for row in payload["leaves"]
            ),
        )

    def resolve(self, legacy_concept: str) -> tuple[str, str]:
        key = _key(legacy_concept)
        exact = self._aliases.get(key)
        if exact:
            return exact, "exact_alias"
        for pattern, leaf in _HEURISTIC_FAMILIES:
            if pattern.search(key):
                return leaf, "heuristic"
        tokens = set(key.split("_"))
        candidates = (
            ({"price", "move", "shares", "stock", "rally", "decline"}, "market.price_move_observed"),
            ({"volume", "trading", "attention"}, "market.volume_move_observed"),
            ({"short", "interest", "float", "cover"}, "market.short_interest_observed"),
            ({"analyst", "rating", "upgrade", "downgrade"}, "analyst.rating_action"),
            ({"target", "price"}, "analyst.price_target_action"),
            ({"earnings", "eps", "revenue", "quarter"}, "earnings.performance"),
            ({"guidance", "outlook", "forecast"}, "guidance.issued"),
            ({"asset", "sale", "divestiture", "disposal"}, "corporate_transaction.asset_sale"),
            ({"acquisition", "merger", "takeover", "transaction"}, "corporate_transaction.acquisition"),
            ({"offering", "financing", "placement", "debt", "dilution"}, "capital.financing"),
            ({"buyback", "repurchase", "dividend"}, "capital.return"),
            ({"regulatory", "sec", "halt", "exchange"}, "regulatory.action"),
            ({"lawsuit", "litigation", "investigation", "settlement", "bribery"}, "legal.proceeding"),
            ({"fda", "clinical", "trial", "drug"}, "clinical.regulatory_milestone"),
            ({"contract", "order", "award", "customer"}, "commercial.contract"),
            ({"product", "launch", "recall"}, "product.milestone"),
            ({"executive", "management", "board", "ceo", "cfo"}, "governance.management_change"),
            ({"operations", "business", "restructuring", "layoff", "facility"}, "operations.business_update"),
            ({"listing", "delisting", "compliance", "split", "ipo"}, "listing.market_structure"),
            ({"partnership", "collaboration", "licensing", "distribution"}, "commercial.partnership"),
            ({"margin"}, "financial.margin"),
            ({"ownership", "insider", "stake"}, "ownership.position_change"),
            ({"liquidity", "cash"}, "financial.liquidity"),
            ({"index", "inclusion", "removal"}, "index.membership"),
        )
        scored = [(len(tokens & clues), leaf) for clues, leaf in candidates]
        score, leaf = max(scored, default=(0, self.fallback_leaf))
        return (leaf, "heuristic") if score else (self.fallback_leaf, "fallback")

    def contains(self, concept_id: str) -> bool:
        return concept_id in self._leaves


def _key(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.lower())).strip("_")
