from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


TAXONOMY_VERSION = "sec-disclosure-taxonomy-v1"
POLICY_VERSION = "qwen3-embedding-policy-v1"
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"


# EDGAR submission types that are valid and common in the archive but are not
# represented as numbered rows in the public Forms Index. These definitions are
# manually reviewed; amendments inherit the base type during candidate matching.
MANUAL_FORM_DEFINITIONS: dict[str, str] = {
    "10-12B": "Exchange Act securities registration statement",
    "10-12G": "Registration of a class of securities under the Exchange Act",
    "13F-HR": "Institutional investment manager holdings report",
    "20FR12B": "Foreign issuer Exchange Act securities registration statement",
    "20FR12G": "Foreign issuer Exchange Act securities registration statement",
    "24F-2NT": "Investment-company annual notice of securities sold",
    "253G1": "Regulation A offering circular",
    "253G2": "Regulation A offering circular supplement",
    "253G3": "Regulation A offering circular amendment",
    "253G4": "Regulation A offering circular supplement",
    "424B1": "Prospectus filed under Rule 424(b)(1)",
    "424B2": "Prospectus filed under Rule 424(b)(2)",
    "424B3": "Prospectus filed under Rule 424(b)(3)",
    "424B4": "Prospectus filed under Rule 424(b)(4)",
    "424B5": "Prospectus filed under Rule 424(b)(5)",
    "424B7": "Prospectus filed under Rule 424(b)(7)",
    "424B8": "Prospectus filed under Rule 424(b)(8)",
    "424H": "Preliminary prospectus filed under Rule 424(h)",
    "425": "Business-combination communication",
    "485APOS": "Post-effective investment-company amendment under Rule 485(a)",
    "485BPOS": "Post-effective investment-company amendment under Rule 485(b)",
    "485BXT": "Post-effective investment-company amendment extension",
    "486APOS": "Post-effective business-development-company amendment under Rule 486(a)",
    "486BPOS": "Post-effective business-development-company amendment under Rule 486(b)",
    "487": "Investment-company pricing amendment",
    "497": "Investment-company definitive materials",
    "497K": "Investment-company summary prospectus",
    "497VPI": "Variable insurance product initial summary prospectus",
    "497VPU": "Variable insurance product updated summary prospectus",
    "40-17G": "Investment-company fidelity bond filing",
    "40-APP": "Investment Company Act application for exemptive relief",
    "ARS": "Annual report to security holders",
    "DEFA14A": "Additional definitive proxy soliciting material",
    "DEFM14A": "Definitive proxy statement for merger or acquisition",
    "DEF 14A": "Definitive proxy statement",
    "DEF 14C": "Definitive information statement",
    "FWP": "Free writing prospectus",
    "N-CSRS": "Certified semiannual shareholder report",
    "N-14 8C": "Investment-company business-combination registration statement",
    "N-30B-2": "Periodic and interim reports sent to investment-company shareholders",
    "N-MFP2": "Monthly money market fund portfolio report",
    "N-MFP3": "Monthly money market fund portfolio report",
    "N-Q": "Quarterly schedule of portfolio holdings",
    "N-VPFS": "Variable insurance product summary prospectus",
    "N-VP": "Variable insurance product filing",
    "NPORT-P": "Monthly portfolio holdings report",
    "POS AM": "Post-effective amendment to a registration statement",
    "POS AMI": "Post-effective investment-company amendment",
    "POS 8C": "Post-effective amendment under Investment Company Act Rule 8c",
    "POS EX": "Post-effective investment-company amendment",
    "POSASR": "Automatic shelf registration post-effective amendment",
    "PRE 14A": "Preliminary proxy statement",
    "PRE 14C": "Preliminary information statement",
    "PREM14A": "Preliminary proxy statement for merger or acquisition",
    "PREC14A": "Preliminary proxy statement for contested solicitation",
    "PRER14A": "Revised preliminary proxy statement",
    "DEFC14A": "Definitive proxy statement for contested solicitation",
    "DEFR14A": "Revised definitive proxy statement",
    "S-3ASR": "Automatic shelf registration statement",
    "S-8 POS": "Post-effective amendment to employee benefit plan registration",
    "SC 13D": "Beneficial ownership report",
    "SC 13G": "Short-form beneficial ownership report",
    "SC 13E3": "Going-private transaction statement",
    "SC 14D9": "Target-company tender-offer recommendation",
    "SC TO-I": "Issuer tender-offer statement",
    "SC TO-T": "Third-party tender-offer statement",
    "SUPPL": "Voluntary prospectus or offering supplement",
}


@dataclass(frozen=True, slots=True)
class SemanticLabel:
    category: str
    impact_label: str
    impact_score: int
    affected_security_scope: str
    rationale: str
    embedding_enabled: bool
    input_strategy: str = "complete_document_chunks"


def normalize_type(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "").strip().upper())


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(value or "")).lower()
    value = re.sub(r"\(pdf\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def taxonomy_key(scope: str, submitted_type: str) -> str:
    raw = f"{TAXONOMY_VERSION}|{scope}|{normalize_type(submitted_type)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def title_match_metrics(query: str, candidate: str) -> dict[str, float]:
    left = normalize_title(query).split()
    right = normalize_title(candidate).split()
    if not left or not right:
        return {"token_coverage": 0.0, "ordered_coverage": 0.0, "span_density": 0.0, "char_similarity": 0.0, "score": 0.0}
    right_positions: dict[str, list[int]] = {}
    for index, token in enumerate(right):
        right_positions.setdefault(token, []).append(index)
    token_coverage = len(set(left) & set(right)) / len(set(left)) if left else 0.0
    positions: list[int] = []
    cursor = -1
    for token in left:
        next_positions = [position for position in right_positions.get(token, []) if position > cursor]
        if next_positions:
            cursor = next_positions[0]
            positions.append(cursor)
    ordered_coverage = len(positions) / len(left)
    span_density = (len(positions) / (positions[-1] - positions[0] + 1)) if positions else 0.0
    char_similarity = SequenceMatcher(None, " ".join(left), " ".join(right)).ratio()
    score = 0.35 * token_coverage + 0.30 * ordered_coverage + 0.20 * span_density + 0.15 * char_similarity
    return {
        "token_coverage": round(token_coverage, 6),
        "ordered_coverage": round(ordered_coverage, 6),
        "span_density": round(span_density, 6),
        "char_similarity": round(char_similarity, 6),
        "score": round(score, 6),
    }


def semantic_label(submitted_type: str, title: str = "", *, scope: str = "form") -> SemanticLabel:
    dtype = normalize_type(submitted_type)
    text = f"{dtype} {normalize_title(title)}"

    # Structured fund and ABS datasets frequently contain generic words such as
    # "annual report". Type authority must win before broad title heuristics.
    if dtype.startswith(("NPORT", "N-PORT", "N-MFP", "N-PX", "PROXY VOTING")) or any(term in text for term in ("portfolio holdings", "proxy voting record", "money market fund portfolio")):
        return SemanticLabel("fund_dataset", "Fund or underlying-security disclosure; low direct stock catalyst", 2, "Fund shares or disclosed portfolio securities", "Structured fund records are useful for holdings or governance analysis but should not be treated as one issuer narrative.", False, "structured_extraction_only")
    if dtype.startswith(("N-CSR", "N-CSRS", "N-CEN", "N-Q", "N-VP", "N-30B", "485", "486", "487", "497")):
        return SemanticLabel("fund_product_disclosure", "Fund or product disclosure; low direct stock catalyst", 2, "Fund shares or variable insurance products", "Useful for fund and product analysis, but not an ordinary operating-company stock document.", False, "separate_fund_pipeline")
    if dtype.startswith(("ABS", "SF-", "10-D", "EX-33", "EX-34", "EX-35", "EX-36", "EX-102", "EX-103", "EX-106")) or "asset backed" in text:
        return SemanticLabel("structured_finance", "Structured-product disclosure", 2, "Asset-backed securities and collateral pools", "Relevant to structured-credit instruments rather than ordinary issuer common stock.", False, "structured_extraction_only")
    if dtype.startswith(("EX-101", "EX-104")) or dtype in {"XML", "XBRL"}:
        return SemanticLabel("technical_representation", "Technical representation; no independent catalyst", 0, "Same subject as parent filing", "Machine-readable data usually duplicates or structures the parent disclosure.", False, "structured_extraction_only")
    if dtype.startswith(("8-K", "6-K", "1-U")) or "current report" in text:
        return SemanticLabel("current_event", "High potential; event-dependent", 5, "Reporting issuer securities", "Current-event disclosure can contain earnings, financing, leadership, bankruptcy, acquisition, or other material news.", True)
    if dtype.startswith(("10-K", "10-Q", "20-F", "40-F", "1-K", "1-SA", "11-K")) or any(term in text for term in ("annual report", "quarterly report", "semiannual report")):
        return SemanticLabel("periodic_fundamentals", "Medium-to-high fundamental relevance", 4, "Reporting issuer securities", "Periodic financial, operating, risk, and management disclosure.", True)
    if dtype.startswith(("S-4", "F-4", "N-14", "SC TO", "SC 14D", "SC13E", "425", "DEFM14", "PREM14")) or any(term in text for term in ("merger", "business combination", "tender offer", "going private")):
        return SemanticLabel("corporate_transaction", "High potential transaction relevance", 5, "Acquirer, target, and transaction securities", "Transaction terms and approvals can directly affect involved securities.", True)
    if dtype.startswith(("S-1", "S-3", "S-8", "F-1", "F-3", "424", "FWP", "D", "C", "253G", "POS")) or any(term in text for term in ("registration statement", "prospectus", "offering statement")):
        return SemanticLabel("offering", "Offering and capital-structure relevance", 4, "Offered and existing issuer securities", "Offering terms can reveal financing, dilution, security design, or withdrawal.", True)
    if dtype.startswith(("SC 13D", "SCHEDULE 13D")):
        return SemanticLabel("ownership_activism", "High-to-medium ownership and activism relevance", 4, "Issuer named in the ownership schedule", "Substantial ownership and stated intentions can influence management or control.", True)
    if dtype.startswith(("SC 13G", "SCHEDULE 13G", "13F-")):
        return SemanticLabel("ownership", "Ownership relevance; usually delayed", 2, "Issuer or underlying portfolio securities", "Ownership data is useful but generally delayed and not an immediate catalyst.", True)
    if dtype in {"3", "3/A", "4", "4/A", "5", "5/A", "144", "144/A"}:
        return SemanticLabel("insider_ownership", "Insider ownership or sale signal", 3, "Issuer equity or derivatives", "Relevance depends on transaction type, size, and discretion.", True)
    if "14A" in dtype or "14C" in dtype or dtype.startswith("PX14") or "proxy" in text:
        return SemanticLabel("governance", "Governance relevance; usually indirect", 3, "Reporting issuer equity", "Board elections, compensation, shareholder proposals, and voting matters can affect governance.", True)
    if dtype.startswith(("EX-31", "EX-32")) or dtype in {"CORRESP", "UPLOAD", "CERT", "NO ACT", "EX-FILING FEES"}:
        return SemanticLabel("administrative", "Administrative or compliance support", 1, "None independently", "Supports filing mechanics or compliance and normally adds no independent economic event.", False, "preserve_only")
    if dtype.startswith("EX-2"):
        return SemanticLabel("transaction_exhibit", "High potential; parent-transaction dependent", 5, "Securities involved in the parent transaction", "Transaction agreement exhibit; use with parent filing context.", True)
    if dtype.startswith("EX-10"):
        return SemanticLabel("material_contract", "Potentially material; parent-context dependent", 4, "Reporting issuer securities", "Material contracts can contain commercial, debt, employment, or financing terms.", True)
    if dtype.startswith("EX-99"):
        return SemanticLabel("additional_exhibit", "Context-dependent; can be high", 4, "Depends on parent filing and exhibit subject", "Often contains earnings releases or investor materials, but the parent and title are required.", True)
    if dtype.startswith("EX-"):
        return SemanticLabel("exhibit", "Exhibit-dependent", 2, "Depends on parent filing", "Exhibit number alone does not establish economic meaning.", True)
    if any(term in text for term in ("application for", "notice of", "notification", "withdrawal", "designation", "appointment")):
        return SemanticLabel("administrative", "Administrative or regulatory", 1, "Usually none directly", "Primarily records a regulatory process or status rather than issuer operating information.", False, "preserve_only")
    return SemanticLabel("other_disclosure", "Content-dependent disclosure", 2, "Filing subject or reporting entity", "The official type alone does not justify a stronger market-impact claim.", True)


DOCUMENT_RULES: tuple[tuple[str, str, str], ...] = (
    ("prefix", "EX-101", "XBRL data exhibit"),
    ("prefix", "EX-104", "Cover page interactive data exhibit"),
    ("prefix", "EX-102", "Asset-level data file"),
    ("prefix", "EX-103", "Asset-level data supporting file"),
    ("prefix", "EX-106", "Asset-level data file"),
    ("prefix", "EX-31", "Officer certification"),
    ("prefix", "EX-32", "Section 906 certification"),
    ("prefix", "EX-33", "Asset-backed servicing compliance report"),
    ("prefix", "EX-34", "Asset-backed servicing compliance assertion"),
    ("prefix", "EX-35", "Asset-backed servicer compliance statement"),
    ("prefix", "EX-36", "Asset-backed investor communication"),
    ("prefix", "EX-2", "Transaction agreement exhibit"),
    ("prefix", "EX-10", "Material contract exhibit"),
    ("prefix", "EX-99", "Additional exhibit or press release"),
    ("prefix", "EX-1", "Underwriting or distribution agreement exhibit"),
    ("prefix", "EX-3", "Charter or bylaws exhibit"),
    ("prefix", "EX-4", "Security instrument or rights exhibit"),
    ("prefix", "EX-", "Other submitted exhibit"),
    ("exact", "EX-FILING FEES", "Filing fee exhibit"),
    ("exact", "PART II", "Form narrative attachment: Part II"),
    ("exact", "PART II AND III", "Form narrative attachment: Parts II and III"),
    ("exact", "PROXY VOTING RECORD", "Proxy voting record attachment"),
    ("exact", "NPORT-EX", "Form N-PORT Part F attachment"),
    ("exact", "XML", "Generic XML document"),
)
