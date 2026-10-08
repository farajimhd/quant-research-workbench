"""Installed JSON declarations retain exact parameters and causal semantics."""
from copy import deepcopy
import json

import numpy as np
import pytest

from src.trading_runtime.native_channel_qualification import (
    parse_native_channel_qualification_policy, qualify_native_channels,
)
from src.trading_runtime.native_channel_event_qualification import (
    NativeChannelAcquisitionQualificationPolicy, event_qualification_payload,
    parse_event_qualification_policy,
)
from tests.test_native_channel_qualification import matrix


def declaration():
    aligned, policy = matrix()
    return aligned, policy, json.loads(json.dumps(policy.payload()))


def test_transport_round_trip_preserves_rule_and_real_columnar_decisions():
    aligned, original, payload = declaration()
    parsed = parse_native_channel_qualification_policy(payload)
    assert parsed == original
    mask = np.ones(aligned.height, dtype=bool)
    assert np.array_equal(qualify_native_channels(aligned, mask, parsed),
                          qualify_native_channels(aligned, mask, original))
    payload['bands'][0]['lower'] = 9.0
    assert parsed == original  # mutable transport cannot change parsed policy


@pytest.mark.parametrize('field,value', [
    ('rule', 'another-rule'), ('input_policy_digest', '0' * 64),
    ('extra', True), ('bands', []),
])
def test_foreign_or_incomplete_rule_fails(field, value):
    _, _, payload = declaration()
    payload[field] = value
    with pytest.raises(ValueError):
        parse_native_channel_qualification_policy(payload)


@pytest.mark.parametrize('field,value', [
    ('decision_interval_ms', 100.0), ('participation_lookback_bars', True),
    ('news', 0), ('splits', True), ('source', 'historical-flatfiles'),
    ('clock', 'forming candle'), ('missing', 'forward-fill'),
    ('returns', 'future close'), ('resolutions_ms', [60000, 60000]),
    ('freshness_by_resolution', [[60000, 59999.0]]),
])
def test_input_numeric_aliases_and_semantic_changes_fail(field, value):
    _, _, payload = declaration()
    payload['input_policy'][field] = value
    with pytest.raises(ValueError):
        parse_native_channel_qualification_policy(payload)


def test_missing_parameters_never_receive_constructor_defaults():
    _, _, payload = declaration()
    for field in ('participation_lookback_bars', 'volatility_lookback_bars'):
        changed = deepcopy(payload)
        del changed['input_policy'][field]
        with pytest.raises(ValueError):
            parse_native_channel_qualification_policy(changed)


@pytest.mark.parametrize('field,value', [
    ('lower', 0), ('lower', False), ('upper', float('inf')),
    ('channel', 'future_reward'), ('extra', 'ticker-specific'),
])
def test_band_numeric_aliases_future_labels_and_extras_fail(field, value):
    _, _, payload = declaration()
    payload['bands'][0][field] = value
    with pytest.raises(ValueError):
        parse_native_channel_qualification_policy(payload)


def test_event_rule_round_trip_and_clock_tampering():
    _, channels, _ = declaration()
    original = NativeChannelAcquisitionQualificationPolicy(channels, (60000,))
    payload = json.loads(json.dumps(event_qualification_payload(original)))
    assert parse_event_qualification_policy(payload) == original
    for field, value in (('event_clock', 'proposal time'),
                         ('evidence', 'at or after acquisition'),
                         ('fresh_resolutions_ms', [60000.0])):
        changed = deepcopy(payload)
        changed[field] = value
        with pytest.raises(ValueError):
            parse_event_qualification_policy(changed)
