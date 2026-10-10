"""Pure immutable configuration derivation; never publishes or approves source."""
from copy import deepcopy
from hashlib import sha256
import re

from . import strategy_one_hundred_fourteen_release as parent
from . import strategy_one_hundred_fourteen_configuration as parent_configuration
from .journal_contract import canonical_json
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .strategy_one_hundred_fifteen_release import (
    CONTROL_REVISION, CONTROL_PAYLOAD_HASH, BEHAVIOR, declared_policies, release_contract,
)

CONTROL_CODE_COMMIT = 'ff0dda321ed4fe99098bb1a334b8c5ccfaf661ea'
CONTROL_CODE_FINGERPRINT = '8c3713296d416166005bbc2f5d8c3de24a791a484559a5adf7a5d034fe6c2901'
SOURCE_PREFIX = 'strategy-one-hundred-fifteen-from'


def historical_parent_source_proof():
    from .historical_parent_source_proof import HistoricalParentSourceProof
    return HistoricalParentSourceProof(parent.release_contract().approved_digest,
        CONTROL_CODE_COMMIT, CONTROL_CODE_FINGERPRINT,
        '17827fe7c6c1bb2dfef7d33154b8a6de46293627e8425bf608cdd3b353917c31')


def manifest_policies():
    return {**declared_policies(),
        'entry_spread_risk_quote_source': deepcopy(parent_configuration.manifest_policies()['entry_spread_risk_quote_source'])}


def verify_exact_parent(source):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if (type(source) is not CertifiedStrategyOneConfiguration or source.strategy_number != 114
            or source.revision()['revision_id'] != CONTROL_REVISION
            or source.payload_hash != CONTROL_PAYLOAD_HASH):
        raise ValueError('Strategy115 requires exact certified Strategy114 control')
    manifest = parent_configuration.verify_prepared_strategy_one_hundred_fourteen_manifest(source.payload['strategy'])
    if (manifest['approved_code_commit'] != CONTROL_CODE_COMMIT
            or manifest['approved_code_fingerprint'] != CONTROL_CODE_FINGERPRINT):
        raise ValueError('Strategy115 parent source approval differs')
    return manifest


def verify_prepared_strategy_one_hundred_fifteen_manifest(strategy):
    release = release_contract()
    if (type(strategy) is not dict
            or type(strategy.get('strategy_number')) is not int
            or strategy['strategy_number'] != release.number
            or type(strategy.get('revision')) is not int
            or strategy['revision'] != release.executor_revision
            or strategy.get('strategy_id') != release.executor_strategy_id
            or strategy.get('execution_interval') != release.evaluation_interval):
        raise ValueError('Strategy115 prepared identity differs')
    manifest = strategy.get('numbered_release')
    policies = manifest_policies()
    required = {'contract', 'approved_digest', 'approved_code_commit',
        'approved_code_fingerprint', 'approval_reference', 'publication_mode',
        'source_revision_id', 'source_payload_hash', 'manifest_hash', *policies}
    if type(manifest) is not dict or set(manifest) != required:
        raise ValueError('Strategy115 complete manifest shape differs')
    if (canonical_json(manifest['contract']) != canonical_json(release.canonical_payload())
            or manifest['approved_digest'] != release.approved_digest
            or manifest['source_revision_id'] != CONTROL_REVISION
            or manifest['source_payload_hash'] != CONTROL_PAYLOAD_HASH
            or any(canonical_json(manifest[key]) != canonical_json(value)
                   for key, value in policies.items())
            or manifest['publication_mode'] != 'backtest_only'
            or not re.fullmatch('[0-9a-f]{40}', str(manifest['approved_code_commit']))
            or not re.fullmatch('[0-9a-f]{64}', str(manifest['approved_code_fingerprint']))
            or type(manifest['approval_reference']) is not str
            or not 1 <= len(manifest['approval_reference'].strip()) <= 512):
        raise ValueError('Strategy115 policy or source approval differs')
    if manifest['manifest_hash'] != sha256(canonical_json(
            {k: v for k, v in manifest.items() if k != 'manifest_hash'}).encode()).hexdigest():
        raise ValueError('Strategy115 manifest seal differs')
    return manifest


def verify_strategy_one_hundred_fifteen_manifest(strategy):
    manifest = verify_prepared_strategy_one_hundred_fifteen_manifest(strategy)
    from .strategy_registry import numbered_strategy, fixed_strategy_executor
    release = release_contract()
    if numbered_strategy(release.number) != release:
        raise ValueError('Strategy115 installed declaration differs')
    fixed_strategy_executor(release.executor_strategy_id, release.executor_revision).verify()
    return manifest


def derive_strategy_one_hundred_fifteen_configuration(source, *, approved_code_commit,
        approved_code_fingerprint, approval_reference):
    verify_exact_parent(source)
    payload = deepcopy(source.payload)
    if payload.get('assignments'):
        raise ValueError('Strategy115 cannot inherit mutable assignments')
    release = release_contract()
    manifest = {**manifest_policies(), 'contract': release.canonical_payload(),
        'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit,
        'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': CONTROL_REVISION, 'source_payload_hash': CONTROL_PAYLOAD_HASH}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    number = release.number
    profile = f'strategy-one-{number}'
    payload['strategy'].update(strategy_number=number, revision=release.executor_revision,
        name=f'Early Squeeze Strategy {number}', profile_id=profile,
        profile_revision=number, numbered_release=manifest)
    verify_prepared_strategy_one_hundred_fifteen_manifest(payload['strategy'])
    payload['strategy_profile'].update(profile_id=profile, revision=number,
        definition_revision=number, name=f'Early Squeeze Strategy {number}', description=BEHAVIOR)
    payload['run_plan'].update(name=f'Strategy {number} Backtest',
        description=f'Sealed extended-session Strategy {number}', profile_id=profile)
    nodes = encode_nodes(payload)
    if not 1 <= len(nodes) <= 10000:
        raise ValueError('Strategy115 exceeds native configuration node bound')
    return dict(source_candidate_id=f'{SOURCE_PREFIX}:{CONTROL_REVISION}',
        source_candidate_hash=source.payload_hash,
        payload_hash=sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash=node_hash(nodes), node_count=len(nodes), payload=payload)

