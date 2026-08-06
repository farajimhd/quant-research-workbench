"""News Synthesis V1 production authority.

Historical taxonomy and migration tools intentionally require explicit module
imports; importing this package exposes only the active V1 contract and engine.
"""

from .contracts import CONTRACT_VERSION
from .engine import ENGINE_VERSION, NewsSynthesisEngine

__all__ = ["CONTRACT_VERSION", "ENGINE_VERSION", "NewsSynthesisEngine"]
