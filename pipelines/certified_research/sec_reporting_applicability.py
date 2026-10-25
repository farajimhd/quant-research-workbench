"""Source-bound, filing-specific absence of corporate financial statements.

This does not determine listing eligibility, exempt another filing, or authorize
using an underlying obligor's financials as the trust's own financials.
"""
import re

from .contracts import digest
from .sec_html_context import normalize, parse

VERSION = 'sec-explicit-financial-statement-applicability-v1'
_ITEM8 = r'Item\s+8\s*\.?\s+Financial Statements and Supplementary Data\s*\.?'


def review(source, document_hash):
    """Return only explicit source evidence; absence of evidence stays unknown."""
    parsed = parse(source, document_hash)
    text = normalize(' '.join(parsed.text))
    direct = re.search(_ITEM8 + r'\s+(?:Not Applicable|None)\s*\.?\s+Item\s+9\b', text, re.I)
    reason = None
    proof = None
    if direct:
        reason = 'item_8_explicitly_not_applicable_or_none'
        proof = direct.group(0)
    else:
        omitted = re.search(
            r'PART II\s+The following Items have been omitted in accordance with '
            r'General Instruction J to Form 10-K:\s+(.*?)\s+PART III\b', text, re.I)
        if omitted and re.search(_ITEM8 + r'\s+Item\s+9\b', omitted.group(1), re.I):
            reason = 'item_8_explicitly_omitted_under_instruction_j'
            proof = omitted.group(0)
    body = dict(version=VERSION, document_hash=document_hash, scope='this_filing_only',
                status='corporate_statements_not_provided' if reason else 'unresolved',
                reason=reason, evidence=proof)
    return dict(body, review_hash=digest(body))
