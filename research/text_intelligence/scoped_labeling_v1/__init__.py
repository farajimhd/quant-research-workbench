"""SEC V5 authority; the former News path is retired in favor of Synthesis V1."""

from .pipeline import classify_sec_document
from .schema import SCOPED_LABELING_VERSION

__all__ = [
    "SCOPED_LABELING_VERSION",
    "classify_sec_document",
]
