"""Frozen V5 lexical feature contract used by V6.

V6 deliberately does not fit or own another TF-IDF vocabulary. Re-exporting
the V5 implementation keeps one authority for lexical transformation while the
V6 numeric channel is versioned independently in ``numeric_features``.
"""

from research.news_reaction_model.v5.text_features import (  # noqa: F401
    SparseFeatureRows,
    SparseTfidfBundle,
    article_model_text,
    compact_char_text,
    load_bundle,
    load_feature_manifest,
)

__all__ = [
    "SparseFeatureRows",
    "SparseTfidfBundle",
    "article_model_text",
    "compact_char_text",
    "load_bundle",
    "load_feature_manifest",
]
