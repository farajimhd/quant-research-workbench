"""Versioned deterministic semantic authority for rendered News and SEC text."""

from .labeler import label_document
from .schema import LABEL_AUTHORITY_VERSION, SemanticDocument, SemanticResult

__all__ = [
    "LABEL_AUTHORITY_VERSION",
    "SemanticDocument",
    "SemanticResult",
    "label_document",
]
