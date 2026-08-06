"""Explicit boundary around the independently versioned SEC V5 authority.

News runtime code must import SEC behavior through this module and must never
import or call the legacy package's News classifier.
"""

from research.text_intelligence.scoped_labeling_v1.persistence import (
    RELATION_TABLE,
    TARGET_TABLE,
    attach_sec_ticker,
    create_tables,
    extend_sec_ticker_mappings,
    iter_sec_documents_for_filings,
    persistence_row,
    relationship_rows,
    row_to_document,
)
from research.text_intelligence.scoped_labeling_v1.pipeline import classify_sec_document
from research.text_intelligence.scoped_labeling_v1.schema import SCOPED_LABELING_VERSION
from research.text_intelligence.scoped_labeling_v1.sec_extractor import (
    sec_document_labeling_eligible,
)

__all__ = [
    "RELATION_TABLE",
    "SCOPED_LABELING_VERSION",
    "TARGET_TABLE",
    "attach_sec_ticker",
    "classify_sec_document",
    "create_tables",
    "extend_sec_ticker_mappings",
    "iter_sec_documents_for_filings",
    "persistence_row",
    "relationship_rows",
    "row_to_document",
    "sec_document_labeling_eligible",
]
