"""News Synthesis V1 production authority.

Historical taxonomy and migration tools intentionally require explicit module
imports; importing this package exposes only the active V1 contract and engine.
"""

from .contracts import CONTRACT_VERSION
from .engine import ENGINE_VERSION, NewsSynthesisEngine
from .funnel import FUNNEL_VERSION, NewsSynthesisFunnel
from .provider_context import ROUTER_VERSION, classify_provider_context

__all__ = [
    "CONTRACT_VERSION", "ENGINE_VERSION", "FUNNEL_VERSION", "NewsSynthesisEngine",
    "NewsSynthesisFunnel", "ROUTER_VERSION",
    "classify_provider_context",
]
