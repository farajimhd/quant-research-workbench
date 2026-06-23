"""Temporal event model v2.

This version trains a single-ticker temporal predictor on top of frozen market
structure embeddings. The input path mirrors production: compact event chunks
are encoded once by the pretrained market encoder, then a temporal model reads a
sequence of embeddings and predicts future return horizons.
"""

