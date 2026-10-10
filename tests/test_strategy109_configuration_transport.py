"""Real captured parent SELECT transport; draft candidate is never published."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from src.backend.backtest_strategy_one_configuration import (
    certify_numbered_configuration, RELEASE_TABLE, NODE_TABLE,
)
from src.backend.historical_runtime_versions import backend_source_fingerprint
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes
from src.trading_runtime.strategy_one_hundred_nine_release import (
    derive_strategy_one_hundred_nine_configuration,
)
from src.trading_runtime.journal_contract import canonical_json


RUNTIME = Path('D:/TradingML/runtimes/strategy-optimization-20261005')


class ConfigurationSelectTransport:
    """Exact captured responses plus a completely encoded unpublished candidate."""
    def __init__(self):
        captured = json.loads((RUNTIME / 'strategy109-certified-parent-sql-transport-v1.json')
                             .read_text(encoding='utf-8'))
        assert captured['configuration_only'] and not captured['market_or_validation_reads']
        self.responses = dict(captured['responses'])
        self.queries = []
        self.parent = certify_numbered_configuration(self, 42)
        assert asdict(self.parent) == captured['parent']

    def execute(self, query):
        self.queries.append(query)
        if query not in self.responses:
            raise AssertionError('Uncaptured configuration SELECT: ' + query)
        return self.responses[query]

    def add_draft(self, derived):
        strategy = derived['payload']['strategy']
        number = strategy['strategy_number']
        attempt = str(uuid4())
        release = dict(release_attempt_id=attempt, strategy_id=strategy['strategy_id'],
            source_candidate_id=derived['source_candidate_id'],
            source_candidate_hash=derived['source_candidate_hash'],
            payload_hash=derived['payload_hash'], node_count=derived['node_count'],
            node_hash=derived['node_hash'])
        release_query = ('SELECT release_attempt_id,strategy_id,source_candidate_id,'
            'source_candidate_hash,payload_hash,node_count,node_hash '
            f'FROM {RELEASE_TABLE} WHERE strategy_number={number} FORMAT JSONEachRow')
        node_query = ('SELECT node_id,parent_node_id,child_key,child_ordinal,value_kind,'
            'text_value,int_value,float_value,bool_value '
            f'FROM {NODE_TABLE} WHERE strategy_number={number} '
            f"AND release_attempt_id=toUUID('{attempt}') ORDER BY node_id FORMAT JSONEachRow")
        assert release_query not in self.responses and node_query not in self.responses
        self.responses[release_query] = json.dumps(release)
        self.responses[node_query] = '\n'.join(json.dumps(node) for node in encode_nodes(derived['payload']))
        return release_query, node_query


def prepared_transport():
    transport = ConfigurationSelectTransport()
    derived = derive_strategy_one_hundred_nine_configuration(transport.parent,
        approved_code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
            cwd=Path(__file__).resolve().parents[1], text=True).strip(),
        approved_code_fingerprint=backend_source_fingerprint(),
        approval_reference='unpublished controlled configuration SELECT transport')
    queries = transport.add_draft(derived)
    return transport, derived, queries


def bind_complete_cold_configuration(source, transport, source_guard):
    """Keep complete normal certificate readers and the original clean-source guard."""
    assert source.parent_json == canonical_json(transport.parent.payload)
    manifest = source.installed_payload['strategy']['numbered_release']
    derived = derive_strategy_one_hundred_nine_configuration(transport.parent,
        **{key: manifest[key] for key in (
            'approved_code_commit', 'approved_code_fingerprint', 'approval_reference')})
    assert canonical_json(derived['payload']) == source.installed_json
    transport.add_draft(derived)
    candidate = certify_numbered_configuration(transport, 109)
    source_guard(candidate)
    return transport


def test_normal_reader_reconstructs_complete_real_parent_and_draft_successor():
    transport, derived, _ = prepared_transport()
    candidate = certify_numbered_configuration(transport, 109)
    assert candidate.payload == derived['payload']
    assert candidate.payload_hash == derived['payload_hash']
    assert candidate.node_hash == derived['node_hash']
    receipt = dict(strategy=109, parent_payload_hash=transport.parent.payload_hash,
        candidate_payload_hash=candidate.payload_hash, candidate_node_hash=candidate.node_hash,
        query_count=len(transport.queries), unique_queries=len(set(transport.queries)),
        certificate_reader_overridden=False, source_clean_admission_checked=False,
        native_execution_qualified=False, published=False, financial_acceptance=False)
    path = RUNTIME / f'strategy109-normal-configuration-transport-{uuid4().hex[:12]}-v1.json'
    with path.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
    print('configuration_transport_receipt=' + str(path))


def test_normal_reader_rejects_changed_captured_parent_node():
    transport, _, _ = prepared_transport()
    query = next(query for query in transport.responses
        if f'FROM {NODE_TABLE} WHERE strategy_number=42 ' in query)
    nodes = transport.responses[query].splitlines()
    first = json.loads(nodes[0])
    first['text_value'] = 'uncertified parent delta'
    nodes[0] = json.dumps(first)
    transport.responses[query] = '\n'.join(nodes)
    with pytest.raises(RuntimeError, match='nodes differ from release seal'):
        certify_numbered_configuration(transport, 109)


def test_normal_source_guard_enforces_actual_checkout_state():
    from src.backend.backtest_fixed_structural_lot_native import verify_current_installed_source
    transport, _, _ = prepared_transport()
    candidate = certify_numbered_configuration(transport, 109)
    root = Path(__file__).resolve().parents[1]
    dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=root)
    if dirty:
        with pytest.raises(ValueError, match='not exact clean approved executor'):
            verify_current_installed_source(candidate)
    else:
        verify_current_installed_source(candidate)
