"""App identity consumers with certified external-parent fixture authority.

The frozen writer owns execution certification; current app tests do not replace
that certificate with a source override or change the immutable writer identity.
"""
from copy import deepcopy
from uuid import UUID

import pytest

from test_strategy_seventy_four_configuration import source_fixture
from test_strategy_fifty_release import APPROVAL
from src.backend import backtest_strategy_one_configuration as configuration
from src.trading_runtime import strategy_seventy_four_release as release74
from src.trading_runtime import strategy_seventy_five_release as release75
from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope


def prepared(number):
    release = release74 if number == 74 else release75
    # Both releases really inherit this same immutable published Strategy73.
    assert release.PARENT_REVISION_ID == release74.PARENT_REVISION_ID
    assert release.PARENT_PAYLOAD_HASH == release74.PARENT_PAYLOAD_HASH
    derive = (release.derive_strategy_seventy_four_configuration if number == 74
              else release.derive_strategy_seventy_five_configuration)
    return derive(source_fixture(), **APPROVAL)


def certificate(number):
    envelope = prepared(number)
    return configuration.CertifiedStrategyOneConfiguration(
        str(UUID(int=number)), envelope['payload_hash'], envelope['node_hash'],
        envelope['source_candidate_id'], envelope['source_candidate_hash'],
        'synthetic-external-publication-read-only', envelope['payload'])


@pytest.mark.parametrize('number', [74, 75])
def test_actual_selected_identity_uses_exact_registered_certificate(monkeypatch, number):
    authority = certificate(number)
    client = object()
    calls = []

    def external_certification(actual_client, actual_number):
        calls.append((actual_client, actual_number))
        return authority

    monkeypatch.setattr(configuration, 'certify_numbered_configuration', external_certification)
    revision = authority.revision()
    selected = configuration.selected_numbered_revision(
        revision_id=revision['revision_id'], run_plan_id=revision['run_plan_id'], client=client)
    assert selected == revision
    assert calls == [(client, number)]


@pytest.mark.parametrize('number', [74, 75])
@pytest.mark.parametrize('defect', ['foreign_uuid', 'foreign_plan'])
def test_selected_identity_rejects_crossed_external_authority(monkeypatch, number, defect):
    authority = certificate(number)
    monkeypatch.setattr(configuration, 'certify_numbered_configuration', lambda *_: authority)
    revision = authority.revision()
    identity = revision['revision_id']
    plan = revision['run_plan_id']
    if defect == 'foreign_uuid':
        identity = f'strategy-one-{number}:{UUID(int=999)}'
    else:
        plan = 'foreign-plan'
    with pytest.raises(ValueError):
        configuration.selected_numbered_revision(
            revision_id=identity, run_plan_id=plan, client=object())


@pytest.mark.parametrize('number', [74, 75])
def test_actual_numbered_receiver_preserves_both_source_prefixes(number):
    envelope = prepared(number)
    payload, nodes = _verified_numbered_envelope(envelope)
    assert payload == envelope['payload']
    assert len(nodes) == envelope['node_count']
    changed = deepcopy(envelope)
    changed['source_candidate_id'] = changed['source_candidate_id'].replace(
        'seventy-four' if number == 74 else 'seventy-five', 'seventy-five' if number == 74 else 'seventy-four')
    with pytest.raises(ValueError, match='source provenance'):
        _verified_numbered_envelope(changed)


@pytest.mark.parametrize('number', [74, 75])
def test_current_app_does_not_issue_frozen_writer_execution_certificate(number):
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    with pytest.raises(ValueError, match='source|authority|metadata'):
        certify_numbered_fixed_v4_projection(number)
