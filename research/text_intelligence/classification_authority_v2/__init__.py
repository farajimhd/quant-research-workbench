"""Combined source-metadata and semantic classification authority V2."""

from .authority import classify_document
from .schema import CLASSIFICATION_AUTHORITY_VERSION, ClassificationResult

__all__ = (
    "CLASSIFICATION_AUTHORITY_VERSION",
    "ClassificationResult",
    "classify_document",
)
