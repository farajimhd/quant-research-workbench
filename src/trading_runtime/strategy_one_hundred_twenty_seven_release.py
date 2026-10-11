"""Immutable native inherited-checkpoint correction."""
from copy import deepcopy
from dataclasses import replace
from . import strategy_one_hundred_twenty_six_release as parent
CONTROL_REVISION = 'strategy-one-126:731cf1d3-1e04-471c-a31b-b6f1d346daed'
CONTROL_PAYLOAD_HASH = '9d912db183e7b0b9eafc1e99c2498cfa9c9c0a13f3f15fde8b111f14bd4694d8'
BEHAVIOR = 'Retain every Strategy126 trading policy, sizing, exposure and cost. Retry one fully buffered plain SELECT on a fresh connection after an incomplete HTTP response; discard the partial response and fail on the second error. Never retry ambiguous writes or streaming reads. Preserve source hashes, certified parent selection, typed readback and Keeper fences, causal completed channels, acquisition clocks and sequential Portfolio/OMS ordering. PM/AH only; AH retains prior-day V7 and full regular-session warmup. Backtest-only; financial improvement and validation remain unproven.'

def release_contract():
    prior = parent.release_contract()
    draft = replace(prior, number=127, executor_revision=127, behavior_specification=BEHAVIOR, approved_digest='')
    release = replace(draft, approved_digest=draft.digest())
    release.verify()
    return release

def declared_policies():
    return deepcopy(parent.declared_policies())
