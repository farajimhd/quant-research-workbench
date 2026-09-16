"""Immutable successor of a saved R1 v3 configuration, with episode VWAP entry."""
from copy import deepcopy

from src.trading_runtime.r1_ladder import CONTRACT, VWAP_CONTRACT

PROFILE_ID = VWAP_CONTRACT
LABEL = 'R1 resistance ladder v4 — MACD episode VWAP entry'
DESCRIPTION = (
    'Within a bullish completed 5s MACD episode, if no other resistance band lies '
    'between execution VWAP and R1, enter on a completed green 1s candle crossing '
    'from at/below VWAP to above VWAP. Compare consecutive completed candle/VWAP '
    'samples; the crossing is not latched for later entry. If an intervening '
    'resistance exists, wait for a fresh completed R1 upper-edge crossover. '
    'Unavailable VWAP cannot authorize the VWAP path. All Strategy 303 liquidity, '
    'RVOL, time, sizing, swing-stop and continuation rules are retained. Targets '
    'retain the R1-based overhead resistance/ATR selection, including VWAP entries.'
)


def build(source_configuration, *, published_configuration=None):
    return build_successor(source_configuration, source_contract=CONTRACT,
        profile_id=PROFILE_ID, label=LABEL, description=DESCRIPTION,
        published_configuration=published_configuration)


def build_successor(source_configuration, *, source_contract, profile_id, label,
                    description, published_configuration=None):
    """Copy the exact saved candidate, replacing only its strategy identity/gate.

    Rename prefixed rule/watchlist/plan references together so the new candidate
    remains independent. The source snapshot is never modified or rebuilt from
    a potentially different current default configuration.
    """
    source_id = source_configuration['strategy']['active_profile_id']
    source = next(p for p in source_configuration['strategy']['profiles']
                  if p['profile_id'] == source_id)
    if source['parameters'].get('r1_ladder_contract') != source_contract or source_id != source_contract:
        raise ValueError(f'R1 successor requires a saved {source_contract} source configuration')

    def clone(value):
        if isinstance(value, dict):
            return {key: clone(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clone(item) for item in value]
        if isinstance(value, str):
            if value == source_id or value.startswith(source_id+'-'):
                return profile_id+value[len(source_id):]
            if value == source['name']:
                return label
        return deepcopy(value)

    payload = clone(source_configuration)
    if published_configuration is not None:
        # A historical candidate embeds other, potentially older, published
        # profiles. Preserve their current immutable authority in this new
        # release; never try to republish those historical copies as changes.
        published = {p['profile_id']: deepcopy(p)
                     for p in published_configuration['strategy']['profiles']
                     if p.get('publication_status') == 'published'}
        if profile_id in published:
            raise ValueError('VWAP successor identity is already published')
        profiles = payload['strategy']['profiles']
        profiles = [published.pop(p['profile_id'], p) for p in profiles]
        payload['strategy']['profiles'] = profiles + list(published.values())
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == profile_id)
    profile.update(name=label, description=description, publication_status='draft', revision=1)
    plan = next(p for p in payload['run_plans']['plans'] if p['profile_id'] == profile_id)
    plan.update(name=label, description=description)
    return payload, payload['canvas'], plan['run_plan_id']


def create(source_configuration):
    from .trading_configuration_service import configuration_base, create_test_candidate

    payload, canvas, plan = build(source_configuration, published_configuration=configuration_base())
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan, strategy_profile_id=PROFILE_ID)
