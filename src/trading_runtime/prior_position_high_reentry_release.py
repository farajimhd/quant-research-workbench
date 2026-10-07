"""Preparation-only exact-parent reentry successor; no installed admission."""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import re

from .journal_contract import canonical_json
from .strategy_registry import NumberedStrategyRelease
from .strategy_one_configuration_tree import encode_nodes, node_hash
from .prior_position_high_reentry import (PriorPositionHighReentryPolicy,
    PRIOR_POSITION_HIGH_REENTRY_RULE, PRIOR_POSITION_HIGH_REENTRY_INPUT)


@dataclass(frozen=True)
class ReentryParentBinding:
    parent_release: NumberedStrategyRelease
    revision_id: str
    payload_hash: str
    node_hash: str
    source_candidate_id: str
    source_candidate_hash: str
    published_commit: str
    published_fingerprint: str

    def __post_init__(self):
        if type(self.parent_release) is not NumberedStrategyRelease:
            raise TypeError('Reentry parent binding requires exact immutable release')
        self.parent_release.verify()
        if type(self.source_candidate_id) is not str or not 1 <= len(self.source_candidate_id) <= 512:
            raise ValueError("Reentry parent source provenance is malformed")
        patterns = ((self.revision_id, f'strategy-one-{self.parent_release.number}:' + r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'),
            (self.payload_hash, r'[0-9a-f]{64}'), (self.node_hash, r'[0-9a-f]{64}'),
            (self.source_candidate_hash, r'[0-9a-f]{64}'), (self.published_commit, r'[0-9a-f]{40}'),
            (self.published_fingerprint, r'[0-9a-f]{64}'))
        if any(type(value) is not str or re.fullmatch(pattern, value) is None for value, pattern in patterns):
            raise ValueError('Reentry parent binding requires exact published identity fields')


def verify_reentry_parent(source, binding):
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    if type(source) is not CertifiedStrategyOneConfiguration or type(binding) is not ReentryParentBinding:
        raise TypeError('Reentry preparation requires typed parent and immutable binding')
    binding.__post_init__()
    from .strategy_registry import numbered_strategy
    parent = binding.parent_release
    strategy = source.payload['strategy']
    manifest = strategy.get('numbered_release')
    if (numbered_strategy(parent.number) != parent or type(manifest) is not dict
            or canonical_json(manifest.get('contract')) != canonical_json(parent.canonical_payload())
            or manifest.get('approved_digest') != parent.approved_digest
            or type(strategy.get('strategy_number')) is not int or strategy['strategy_number'] != parent.number
            or type(strategy.get('revision')) is not int or strategy['revision'] != parent.executor_revision
            or strategy.get('strategy_id') != parent.executor_strategy_id
            or strategy.get('execution_interval') != parent.evaluation_interval
            or manifest.get('publication_mode') != 'backtest_only'
            or manifest.get('manifest_hash') != sha256(canonical_json({k:v for k,v in manifest.items()
                                                            if k != 'manifest_hash'}).encode()).hexdigest()):
        raise ValueError('Reentry parent manifest/release authority differs')
    nodes = encode_nodes(source.payload)
    if (source.strategy_number != parent.number or source.revision()['revision_id'] != binding.revision_id
            or source.payload_hash != binding.payload_hash
            or sha256(canonical_json(source.payload).encode()).hexdigest() != binding.payload_hash
            or source.node_hash != binding.node_hash or source.node_hash != node_hash(nodes)
            or source.source_candidate_id != binding.source_candidate_id
            or source.source_candidate_hash != binding.source_candidate_hash
            or manifest.get("source_payload_hash") != binding.source_candidate_hash
            or manifest['approved_code_commit'] != binding.published_commit
            or manifest['approved_code_fingerprint'] != binding.published_fingerprint
            or source.payload.get('assignments')):
        raise ValueError('Reentry preparation differs from complete pinned parent')
    return manifest


def _verify_declaration(release, parent):
    if type(release) is not NumberedStrategyRelease:
        raise TypeError('Reentry declaration requires exact release type')
    release.verify()
    adapter = 'declared-numbered-fixed-policy-adapter@1'
    inputs = parent.input_contracts + (() if adapter in parent.input_contracts else (adapter,))
    if (release.rule_set_contracts != parent.rule_set_contracts + (PRIOR_POSITION_HIGH_REENTRY_RULE,)
            or release.input_contracts != inputs + (PRIOR_POSITION_HIGH_REENTRY_INPUT,)
            or release.executor_strategy_id != parent.executor_strategy_id
            or release.evaluation_interval != parent.evaluation_interval
            or release.number != release.executor_revision or release.number == parent.number):
        raise ValueError('Reentry declaration must preserve exact parent prefix and add only paired rule')


def derive_reentry_configuration(source, *, binding, release, approved_code_commit,
                                  approved_code_fingerprint, approval_reference):
    """Build a candidate; caller approval fields never authorize execution."""
    parent_manifest = verify_reentry_parent(source, binding)
    _verify_declaration(release, binding.parent_release)
    if (type(approved_code_commit) is not str or re.fullmatch('[0-9a-f]{40}', approved_code_commit) is None
            or type(approved_code_fingerprint) is not str or re.fullmatch('[0-9a-f]{64}', approved_code_fingerprint) is None
            or type(approval_reference) is not str or not 1 <= len(approval_reference.strip()) <= 512):
        raise ValueError('Reentry preparation approval metadata is malformed')
    metadata = {'contract', 'approved_digest', 'approved_code_commit', 'approved_code_fingerprint',
                'approval_reference', 'publication_mode', 'source_revision_id', 'source_payload_hash', 'manifest_hash'}
    policies = {k: deepcopy(v) for k, v in parent_manifest.items() if k not in metadata}
    policies['prior_position_high_reentry_policy'] = PriorPositionHighReentryPolicy().payload()
    manifest = {**policies, 'contract': release.canonical_payload(), 'approved_digest': release.approved_digest,
        'approved_code_commit': approved_code_commit, 'approved_code_fingerprint': approved_code_fingerprint,
        'approval_reference': approval_reference, 'publication_mode': 'backtest_only',
        'source_revision_id': binding.revision_id, 'source_payload_hash': binding.payload_hash}
    manifest['manifest_hash'] = sha256(canonical_json(manifest).encode()).hexdigest()
    payload = deepcopy(source.payload)
    number = release.number
    profile = f'strategy-one-{number}'
    name = f'Early Squeeze Strategy {number}'
    payload['strategy'].update(strategy_number=number, revision=number, name=name,
        profile_id=profile, profile_revision=number, numbered_release=manifest)
    payload['strategy_profile'].update(profile_id=profile, revision=number,
        definition_revision=number, name=name, description=release.behavior_specification)
    payload['run_plan'].update(name=f'Strategy {number} Backtest',
        description=f'Sealed extended-session Strategy {number}', profile_id=profile)
    nodes = encode_nodes(payload)
    return {'source_candidate_id': f'prior-position-high-reentry-from:{binding.revision_id}',
        'source_candidate_hash': binding.payload_hash,
        'payload_hash': sha256(canonical_json(payload).encode()).hexdigest(),
        'node_hash': node_hash(nodes), 'node_count': len(nodes), 'payload': payload}


def verify_prepared_reentry_configuration(result, source, *, binding, release):
    """Rebuild the entire tree; a resealed economic mutation cannot pass."""
    if type(result) is not dict or set(result) != {'source_candidate_id', 'source_candidate_hash',
            'payload_hash', 'node_hash', 'node_count', 'payload'}:
        raise ValueError('Reentry prepared envelope shape differs')
    strategy = result['payload']['strategy']
    manifest = strategy['numbered_release']
    expected = derive_reentry_configuration(source, binding=binding, release=release,
        approved_code_commit=manifest['approved_code_commit'],
        approved_code_fingerprint=manifest['approved_code_fingerprint'],
        approval_reference=manifest['approval_reference'])
    if canonical_json(result) != canonical_json(expected):
        raise ValueError('Reentry prepared tree differs from exact parent derivation')
    return result
