"""Immutable certified participant-scope correction."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_twenty_three_release as parent
CONTROL_REVISION = 'strategy-one-123:fd3d32bd-801c-4f9e-89c5-08f82dbd7114'
CONTROL_PAYLOAD_HASH = '3494259e27f1b5e3bdebac66be7e96f87d96e6ab1c6a90ccc60edbc8cfc79f22'
BEHAVIOR = 'Retain every Strategy123 trading policy, sizing, exposure and trading cost. Prepare native feature packets only for certified possible entry participants within the requested flat-start session window, without changing portfolio assignments or admission decisions. Certify empty participant windows explicitly and reject all held-position feature lookups in them. Preserve complete feature history, pinned source identity, causal completed multi-resolution inputs, inherited exit priority and sequential cash, liquidity, fills and OCA ordering. PM/AH only; AH retains prior-day V7 and full regular-session warmup. Backtest-only; financial improvement, full-session speed and validation remain unproven.'

def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=124, executor_revision=124, behavior_specification=BEHAVIOR, approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    release.verify()
    return release

def declared_policies():
    return deepcopy(parent.declared_policies())
