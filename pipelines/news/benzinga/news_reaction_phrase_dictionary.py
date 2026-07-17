from __future__ import annotations

from dataclasses import dataclass


PHRASE_DICTIONARY_VERSION = "news_phrase_dictionary_v1"


@dataclass(frozen=True, slots=True)
class PhraseRule:
    phrase_id: str
    canonical_phrase: str
    family: str
    direction: int
    strength: float
    feature_role: str
    needles: tuple[str, ...]


def _rule(
    phrase_id: str,
    canonical_phrase: str,
    family: str,
    direction: int,
    strength: float,
    *needles: str,
    feature_role: str = "event_language",
) -> PhraseRule:
    return PhraseRule(
        phrase_id=phrase_id,
        canonical_phrase=canonical_phrase,
        family=family,
        direction=direction,
        strength=strength,
        feature_role=feature_role,
        needles=tuple(dict.fromkeys(value.strip().lower() for value in needles if value.strip())),
    )


# Presence is stored once per article and canonical phrase. Variants collapse here
# before persistence so repeated wording cannot overweight an article.
PHRASE_RULES: tuple[PhraseRule, ...] = (
    _rule("earnings_eps_beat", "EPS beats estimates", "earnings", 1, 0.75, "eps beat", "eps beats", "earnings per share beat", "earnings per share beats"),
    _rule("earnings_eps_miss", "EPS misses estimates", "earnings", -1, 0.75, "eps miss", "eps misses", "earnings per share miss", "earnings per share misses"),
    _rule("earnings_revenue_beat", "Revenue beats estimates", "earnings", 1, 0.7, "revenue beat", "revenue beats", "sales beat estimates", "sales beats estimates"),
    _rule("earnings_revenue_miss", "Revenue misses estimates", "earnings", -1, 0.7, "revenue miss", "revenue misses", "sales miss estimates", "sales misses estimates"),
    _rule("earnings_estimates_beat", "Results beat estimates", "earnings", 1, 0.65, "beats estimates", "beat estimates", "above consensus estimates", "topped estimates"),
    _rule("earnings_estimates_miss", "Results miss estimates", "earnings", -1, 0.65, "misses estimates", "missed estimates", "below consensus estimates", "fell short of estimates"),
    _rule("earnings_record_revenue", "Record revenue", "earnings", 1, 0.6, "record revenue", "record sales"),
    _rule("earnings_margin_expansion", "Margin expansion", "earnings", 1, 0.55, "margin expansion", "margins expanded", "gross margin increased"),
    _rule("earnings_margin_contraction", "Margin contraction", "earnings", -1, 0.55, "margin contraction", "margins contracted", "gross margin declined"),
    _rule("guidance_raise", "Raises guidance", "guidance", 1, 0.9, "raises guidance", "raised guidance", "raises outlook", "raised outlook", "boosts forecast", "increases forecast"),
    _rule("guidance_cut", "Cuts guidance", "guidance", -1, 0.9, "cuts guidance", "cut guidance", "lowers guidance", "lowered guidance", "cuts outlook", "lowered outlook", "reduces forecast"),
    _rule("guidance_reaffirm", "Reaffirms guidance", "guidance", 0, 0.35, "reaffirms guidance", "reaffirmed guidance", "maintains guidance", "reiterates outlook"),
    _rule("guidance_withdraw", "Withdraws guidance", "guidance", -1, 0.85, "withdraws guidance", "withdrew guidance", "suspends guidance", "no longer expects"),
    _rule("guidance_initiate", "Initiates guidance", "guidance", 0, 0.35, "initiates guidance", "introduced guidance", "provides initial outlook"),
    _rule("profit_warning", "Issues profit warning", "guidance", -1, 0.9, "profit warning", "warns on profit", "warns of lower profit"),
    _rule("dividend_increase", "Increases dividend", "capital_allocation", 1, 0.6, "raises dividend", "increases dividend", "dividend increase", "boosts dividend"),
    _rule("dividend_special", "Declares special dividend", "capital_allocation", 1, 0.55, "special dividend"),
    _rule("dividend_cut", "Cuts dividend", "capital_allocation", -1, 0.8, "cuts dividend", "dividend cut", "reduces dividend"),
    _rule("dividend_suspend", "Suspends dividend", "capital_allocation", -1, 0.9, "suspends dividend", "suspended dividend", "eliminates dividend"),
    _rule("buyback_authorize", "Authorizes share repurchase", "capital_allocation", 1, 0.55, "share repurchase authorization", "authorizes buyback", "stock buyback", "repurchase program"),
    _rule("offering_public", "Announces public offering", "financing", -1, 0.75, "public offering", "underwritten offering", "common stock offering"),
    _rule("offering_registered_direct", "Announces registered direct offering", "financing", -1, 0.8, "registered direct offering"),
    _rule("offering_atm", "Uses at-the-market offering", "financing", -1, 0.65, "at-the-market offering", "atm offering", "at the market offering"),
    _rule("offering_private_placement", "Announces private placement", "financing", -1, 0.55, "private placement"),
    _rule("financing_dilution", "Shareholder dilution", "financing", -1, 0.8, "shareholder dilution", "dilutive financing", "dilution to shareholders"),
    _rule("debt_refinance", "Refinances debt", "financing", 0, 0.35, "refinances debt", "debt refinancing", "refinancing agreement"),
    _rule("liquidity_raise", "Raises liquidity", "financing", 1, 0.4, "strengthens liquidity", "raises liquidity", "extends debt maturity"),
    _rule("merger_agreement", "Enters merger agreement", "mergers_acquisitions", 1, 0.7, "merger agreement", "definitive merger agreement", "agreed to merge"),
    _rule("acquisition_announce", "Announces acquisition", "mergers_acquisitions", 1, 0.6, "announces acquisition", "to acquire", "agreed to acquire", "acquisition of"),
    _rule("takeover_offer", "Receives takeover offer", "mergers_acquisitions", 1, 0.8, "takeover offer", "buyout offer", "acquisition proposal"),
    _rule("merger_terminate", "Terminates merger", "mergers_acquisitions", -1, 0.75, "terminates merger", "terminated merger", "merger terminated", "abandons acquisition"),
    _rule("contract_award", "Wins contract", "contracts_orders", 1, 0.65, "awarded a contract", "wins contract", "contract award", "selected for contract"),
    _rule("contract_renewal", "Renews contract", "contracts_orders", 1, 0.4, "contract renewal", "renews contract", "extended contract"),
    _rule("contract_cancel", "Contract cancelled", "contracts_orders", -1, 0.7, "contract cancellation", "contract cancelled", "contract canceled", "terminates contract"),
    _rule("backlog_growth", "Backlog increases", "contracts_orders", 1, 0.5, "record backlog", "backlog increased", "backlog growth"),
    _rule("product_launch", "Launches product", "products_commercial", 1, 0.35, "launches new product", "product launch", "commercial launch"),
    _rule("strategic_partnership", "Forms strategic partnership", "products_commercial", 1, 0.4, "strategic partnership", "collaboration agreement", "commercial partnership"),
    _rule("product_recall", "Recalls product", "products_commercial", -1, 0.8, "product recall", "recalls product", "voluntary recall"),
    _rule("product_discontinue", "Discontinues product", "products_commercial", -1, 0.5, "discontinues product", "product discontinuation", "stops production"),
    _rule("fda_approval", "Receives FDA approval", "regulatory_clinical", 1, 0.95, "fda approval", "fda approved", "approved by the fda"),
    _rule("fda_clearance", "Receives FDA clearance", "regulatory_clinical", 1, 0.75, "fda clearance", "cleared by the fda", "510(k) clearance"),
    _rule("fda_fast_track", "Receives FDA Fast Track", "regulatory_clinical", 1, 0.55, "fast track designation", "breakthrough therapy designation", "orphan drug designation"),
    _rule("clinical_endpoint_met", "Clinical trial meets endpoint", "regulatory_clinical", 1, 0.9, "met primary endpoint", "meets primary endpoint", "positive topline results", "positive top-line results"),
    _rule("clinical_endpoint_miss", "Clinical trial misses endpoint", "regulatory_clinical", -1, 0.95, "missed primary endpoint", "fails primary endpoint", "did not meet primary endpoint", "negative topline results"),
    _rule("clinical_hold", "Clinical hold", "regulatory_clinical", -1, 0.9, "clinical hold", "trial placed on hold"),
    _rule("fda_rejection", "FDA rejection", "regulatory_clinical", -1, 0.95, "complete response letter", "fda rejection", "fda declined approval", "not approved by the fda"),
    _rule("trial_adverse_event", "Serious adverse event", "regulatory_clinical", -1, 0.75, "serious adverse event", "safety concern", "dose limiting toxicity"),
    _rule("legal_investigation", "Government investigation", "legal_compliance", -1, 0.7, "government investigation", "regulatory investigation", "under investigation", "doj investigation", "sec investigation"),
    _rule("legal_subpoena", "Receives subpoena", "legal_compliance", -1, 0.65, "received a subpoena", "grand jury subpoena", "sec subpoena"),
    _rule("legal_lawsuit", "Faces lawsuit", "legal_compliance", -1, 0.45, "class action lawsuit", "securities lawsuit", "faces lawsuit", "sued by"),
    _rule("legal_settlement", "Reaches settlement", "legal_compliance", 0, 0.3, "reaches settlement", "settlement agreement", "agreed to settle"),
    _rule("legal_dismissal", "Lawsuit dismissed", "legal_compliance", 1, 0.5, "lawsuit dismissed", "case dismissed", "charges dismissed"),
    _rule("regulatory_charge", "Regulator files charges", "legal_compliance", -1, 0.8, "sec charges", "doj charges", "regulator charges", "charged with fraud"),
    _rule("management_ceo_appoint", "Appoints CEO", "management_governance", 0, 0.25, "appoints chief executive officer", "appoints new ceo", "named chief executive officer"),
    _rule("management_ceo_resign", "CEO resigns", "management_governance", -1, 0.55, "ceo resigns", "chief executive officer resigned", "ceo steps down"),
    _rule("operations_expansion", "Expands operations", "operations", 1, 0.4, "expands operations", "new manufacturing facility", "capacity expansion", "opens new facility"),
    _rule("operations_layoffs", "Announces layoffs", "operations", -1, 0.5, "announces layoffs", "workforce reduction", "cuts workforce", "job cuts"),
    _rule("operations_restructure", "Announces restructuring", "operations", -1, 0.4, "restructuring plan", "strategic restructuring", "restructuring charges"),
    _rule("operations_shutdown", "Shuts down operations", "operations", -1, 0.75, "plant shutdown", "facility closure", "shuts down operations", "production halt"),
    _rule("operations_disruption", "Operational disruption", "operations", -1, 0.65, "supply disruption", "production disruption", "cyberattack disrupted", "service outage"),
    _rule("credit_upgrade", "Credit rating upgraded", "credit_solvency", 1, 0.5, "credit rating upgrade", "rating upgraded by", "upgraded its credit rating"),
    _rule("credit_downgrade", "Credit rating downgraded", "credit_solvency", -1, 0.65, "credit rating downgrade", "rating downgraded by", "downgraded its credit rating"),
    _rule("debt_default", "Defaults on debt", "credit_solvency", -1, 1.0, "debt default", "defaults on debt", "missed debt payment", "event of default"),
    _rule("bankruptcy", "Bankruptcy filing", "credit_solvency", -1, 1.0, "files for bankruptcy", "filed for bankruptcy", "chapter 11 bankruptcy", "chapter 7 bankruptcy"),
    _rule("going_concern", "Going-concern warning", "credit_solvency", -1, 0.9, "going concern warning", "substantial doubt about its ability to continue", "going concern qualification"),
    _rule("analyst_upgrade", "Analyst upgrade", "analyst_action", 1, 0.6, "analyst upgrade", "upgraded to buy", "upgraded to outperform", "raises rating to"),
    _rule("analyst_downgrade", "Analyst downgrade", "analyst_action", -1, 0.6, "analyst downgrade", "downgraded to sell", "downgraded to underperform", "lowers rating to"),
    _rule("price_target_raise", "Raises price target", "analyst_action", 1, 0.45, "raises price target", "raised price target", "price target increased"),
    _rule("price_target_lower", "Lowers price target", "analyst_action", -1, 0.45, "lowers price target", "lowered price target", "price target reduced"),
    _rule("analyst_initiate_positive", "Initiates positive rating", "analyst_action", 1, 0.45, "initiates with buy", "initiates with outperform", "initiated at buy"),
    _rule("analyst_initiate_negative", "Initiates negative rating", "analyst_action", -1, 0.45, "initiates with sell", "initiates with underperform", "initiated at sell"),
    _rule("shares_rise", "Shares rise", "market_reaction", 1, 0.2, "shares rise", "shares rose", "stock rises", "stock rose", "shares jump", "shares surge", feature_role="observed_reaction"),
    _rule("shares_fall", "Shares fall", "market_reaction", -1, 0.2, "shares fall", "shares fell", "stock falls", "stock fell", "shares plunge", "shares tumble", feature_role="observed_reaction"),
    _rule("trading_halt", "Trading halt", "market_structure", 0, 0.4, "trading halt", "trading halted", "halted pending news"),
    _rule("trading_resume", "Trading resumes", "market_structure", 0, 0.3, "trading resumes", "trading resumed", "resumption of trading"),
    _rule("macro_rate_cut", "Central bank cuts rates", "macro", 1, 0.5, "interest rate cut", "cuts interest rates", "rate cut decision"),
    _rule("macro_rate_hike", "Central bank raises rates", "macro", -1, 0.5, "interest rate hike", "raises interest rates", "rate hike decision"),
    _rule("macro_inflation_hot", "Inflation above estimates", "macro", -1, 0.45, "inflation above estimates", "cpi above estimates", "hotter than expected inflation"),
    _rule("macro_inflation_cool", "Inflation below estimates", "macro", 1, 0.45, "inflation below estimates", "cpi below estimates", "cooler than expected inflation"),
    _rule("macro_jobs_beat", "Jobs data beats estimates", "macro", 1, 0.35, "jobs report beat", "payrolls above estimates", "job growth beat estimates"),
    _rule("macro_jobs_miss", "Jobs data misses estimates", "macro", -1, 0.35, "jobs report miss", "payrolls below estimates", "job growth missed estimates"),
)


def validate_phrase_rules(rules: tuple[PhraseRule, ...] = PHRASE_RULES) -> None:
    phrase_ids = [rule.phrase_id for rule in rules]
    if len(phrase_ids) != len(set(phrase_ids)):
        raise ValueError("phrase dictionary contains duplicate phrase_id values")
    needles = [needle for rule in rules for needle in rule.needles]
    if not needles or any(not needle for needle in needles):
        raise ValueError("every phrase rule must contain at least one non-empty needle")
    # Extraction batches these needles into groups of at most 255 because that
    # is the ClickHouse multiSearch limit.  The dictionary itself must not lose
    # legitimate variants merely to satisfy one function call's argument cap.
    if any(rule.direction not in {-1, 0, 1} for rule in rules):
        raise ValueError("phrase direction must be -1, 0, or 1")
    if any(not 0 <= rule.strength <= 1 for rule in rules):
        raise ValueError("phrase strength must be between 0 and 1")


validate_phrase_rules()
