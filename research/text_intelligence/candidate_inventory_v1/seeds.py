from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SeedConcept:
    concept: str
    phrases: tuple[str, ...]
    corpora: tuple[str, ...] = ("news", "sec")


# These are high-value discovery seeds, not a final or exhaustive taxonomy.
# Corpus mining is expected to discover provider and filing-specific variants.
SEED_CONCEPTS: tuple[SeedConcept, ...] = (
    SeedConcept("registered_direct_offering", ("registered direct offering",)),
    SeedConcept("public_offering", ("underwritten public offering", "public offering")),
    SeedConcept("at_the_market_offering", ("at the market offering", "at-the-market offering", "sales agreement")),
    SeedConcept("shelf_registration", ("shelf registration", "universal shelf")),
    SeedConcept("warrant_issuance", ("purchase warrants", "warrant exercise", "exercise price")),
    SeedConcept("private_placement", ("private placement",)),
    SeedConcept("convertible_financing", ("convertible note", "convertible senior notes", "conversion price")),
    SeedConcept("debt_default", ("event of default", "payment default", "cross default")),
    SeedConcept("bankruptcy", ("chapter 11", "chapter 7", "bankruptcy protection")),
    SeedConcept("going_concern", ("substantial doubt", "going concern")),
    SeedConcept("reverse_split", ("reverse stock split", "reverse share split")),
    SeedConcept("forward_split", ("forward stock split", "stock split")),
    SeedConcept("listing_noncompliance", ("listing compliance", "minimum bid price", "delisting notice")),
    SeedConcept("guidance_raise", ("raises guidance", "raised guidance", "increases guidance")),
    SeedConcept("guidance_cut", ("cuts guidance", "lowered guidance", "reduces guidance")),
    SeedConcept("guidance_reaffirm", ("reaffirms guidance", "reiterates guidance")),
    SeedConcept("earnings_beat", ("beats estimates", "above consensus", "earnings beat")),
    SeedConcept("earnings_miss", ("misses estimates", "below consensus", "earnings miss")),
    SeedConcept("fda_approval", ("fda approval", "fda approved", "food and drug administration approval")),
    SeedConcept("fda_rejection", ("complete response letter", "fda rejected", "fda denial")),
    SeedConcept("clinical_success", ("met primary endpoint", "statistically significant", "positive topline results")),
    SeedConcept("clinical_failure", ("failed primary endpoint", "did not meet primary endpoint", "clinical hold")),
    SeedConcept("merger_agreement", ("definitive merger agreement", "business combination agreement")),
    SeedConcept("merger_termination", ("terminated merger agreement", "merger termination")),
    SeedConcept("contract_award", ("awarded a contract", "contract award", "purchase order")),
    SeedConcept("contract_termination", ("contract termination", "terminated the contract")),
    SeedConcept("share_repurchase", ("share repurchase", "stock buyback", "repurchase authorization")),
    SeedConcept("dividend_increase", ("increased dividend", "dividend increase")),
    SeedConcept("dividend_suspension", ("suspended dividend", "dividend suspension")),
    SeedConcept("restructuring", ("restructuring plan", "workforce reduction", "reduction in force")),
    SeedConcept("management_change", ("chief executive officer resigned", "appointed chief executive officer")),
    SeedConcept("auditor_change", ("change in certifying accountant", "dismissed its independent auditor")),
    SeedConcept("material_agreement", ("material definitive agreement",)),
    SeedConcept("asset_sale", ("sale of substantially all assets", "asset purchase agreement")),
    SeedConcept("investigation", ("formal investigation", "regulatory investigation", "subpoena")),
    SeedConcept("lawsuit", ("class action lawsuit", "patent infringement lawsuit", "filed a lawsuit")),
    SeedConcept("settlement", ("settlement agreement", "agreed to settle")),
    SeedConcept("trading_halt", ("trading halt", "halted pending news")),
    SeedConcept("strategic_alternatives", ("strategic alternatives",)),
    SeedConcept("material_weakness", ("material weakness", "ineffective internal control")),
    SeedConcept("restatement", ("financial restatement", "will restate", "should no longer be relied upon")),
)


PHRASE_TO_CONCEPT: dict[str, str] = {
    phrase: seed.concept
    for seed in SEED_CONCEPTS
    for phrase in seed.phrases
}
