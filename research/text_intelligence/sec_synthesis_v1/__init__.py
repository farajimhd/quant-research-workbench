"""Accession-level deterministic SEC narrative and XBRL synthesis."""

from .contracts import CONTRACT_VERSION, ENGINE_VERSION, validate_document
from .engine import SecSynthesisEngine

__all__ = ["CONTRACT_VERSION", "ENGINE_VERSION", "SecSynthesisEngine", "validate_document"]
