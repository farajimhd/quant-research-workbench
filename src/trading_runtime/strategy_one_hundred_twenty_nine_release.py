"""Immutable reader isolation successor; original producer identity retained."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_twenty_eight_release as parent
CONTROL_REVISION = 'strategy-one-128:14f89065-a10e-4575-bd8e-67bdd4252118'
CONTROL_PAYLOAD_HASH = 'cd6c669dab72bb5763c797d4fb5ed89d12cfb6b5fa3f9ca611d1df3d2379d881'
BEHAVIOR = 'Retain every Strategy128 trading policy, bounded reader policy, sizing, exposure and cost. Isolate bounded read buffering in its own module and retain the original certified market-data producer source identity. Preserve complete-window-before-yield behavior, declared row and byte limits, unexposed-window subdivision, terminal transport errors and sequential Portfolio/OMS ordering. PM/AH only; AH retains prior-day V7 and full regular-session warmup. Backtest-only; financial improvement and validation remain unproven.'

def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=129, executor_revision=129,
        behavior_specification=BEHAVIOR, approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    release.verify()
    return release

def declared_policies():
    return deepcopy(parent.declared_policies())
