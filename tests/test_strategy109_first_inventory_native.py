"""Actual Portfolio/OMS fixture; producer/source/SQL transport seams stay explicit.

These controls are not a full-session benchmark or a financial backtest.
Capability issuers, journal loaders and per-lot execution are not substituted.
"""
import inspect
import json
from pathlib import Path
import textwrap
from asyncio import CancelledError
from uuid import uuid4

import pytest

from tests import test_strategy108_initial_held_reuse as prior
from src.backend import backtest_fixed_lot_first_inventory_source_reuse as first_source
from src.backend import backtest_fixed_lot_initial_recovery_reuse as initial
from src.backend import backtest_fixed_lot_management_reuse as management

RUN_TAG = uuid4().hex[:12]


def actual_successor(monkeypatch, *, tag, paired=False, cold_probe=False, fault_probe=False,
                     scope_probe=None, cold_graph=False):
    tag = f'{tag}-{RUN_TAG}'
    original = first_source.load_first_inventory
    reads = []
    operations = []
    faults = []
    probes = {}

    def observed(operation, loader, *, inventory_key):
        operations.append(operation)
        mode = type(operation.policy).__name__
        if scope_probe is not None and mode not in probes:
            probes[mode] = scope_probe(operation, original, inventory_key)
        if fault_probe and not faults:
            before = (initial._READ.get(), initial._OPERATION.get(),
                      management._ACTIVE.get(), management._CONTEXT_OWNER.get())
            for error in (RuntimeError, CancelledError):
                def failed_loader():
                    assert initial._READ.get() is operation
                    assert management._ACTIVE.get() is None
                    assert management._CONTEXT_OWNER.get() is None
                    raise error('bounded native cancellation control')
                with pytest.raises(error, match='bounded native cancellation control'):
                    original(operation, failed_loader, inventory_key=inventory_key)
                after = (initial._READ.get(), initial._OPERATION.get(),
                         management._ACTIVE.get(), management._CONTEXT_OWNER.get())
                assert all(left is right for left, right in zip(before, after))
                operation.require()
                faults.append(error.__name__)
        result, retained = original(operation, loader, inventory_key=inventory_key)
        reads.append({'retained_source': retained, 'operation_type': type(operation.policy).__name__})
        return result, retained

    monkeypatch.setattr(first_source, 'load_first_inventory', observed)
    source = textwrap.dedent(inspect.getsource(prior._actual108))
    source = source.replace('_actual108', '_actual109')
    source = source.replace('strategy_one_hundred_eight', 'strategy_one_hundred_nine')
    source = source.replace('108', '109')
    cold_preparer = 'from src.backend.backtest_fixed_structural_lot_execution_v19 import prepare_fixed_structural_lot_session'
    assert source.count(cold_preparer) == 1
    source = source.replace(cold_preparer,
        'from src.backend.backtest_fixed_structural_lot_execution_v20 import prepare_fixed_structural_lot_session')
    control = "control.setattr(reuse,'_selected',lambda *a,**kw:None)"
    assert source.count(control) == 2
    source = source.replace(control,
        "control.setattr(first_source,'selected_first_inventory_source_policy',lambda *a,**kw:None)")
    source = source.replace('strategy109-controlled-native-fixture-v1.py',
                            f'strategy109-controlled-native-fixture-{tag}-v1.py')
    namespace = dict(vars(prior), first_source=first_source)
    if cold_graph:
        from tests.test_strategy109_configuration_transport import (
            ConfigurationSelectTransport, bind_complete_cold_configuration, prepared_quote_select_rows,
        )
        from src.backend import backtest_fixed_structural_lot_native as root_native
        from src.backend import backtest_fixed_structural_lot_native_v20 as normal_native
        source_guard = root_native.verify_current_installed_source
        transport = ConfigurationSelectTransport()
        # Reconstruct quote metadata from the current authority, rather than
        # inheriting the earlier fixture's gate token through its quote lambda.
        import test_fixed_structural_lot_checkpoint_reader_profile as fixture
        from src.backend.backtest_fixed_structural_lot_source import _load_quotes
        from types import SimpleNamespace

        def quote_loader(old):
            def load(market, authority, *, client):
                prepared = SimpleNamespace(quotes=old.quotes,
                    session_date=market.sessions[0], price_authority=authority)
                class QuoteRows:
                    def execute(self, query):
                        return prepared_quote_select_rows(prepared, query)
                return _load_quotes(market, authority, client=QuoteRows())
            return load

        selected_text = textwrap.dedent(inspect.getsource(fixture.selected))
        selected_marker = 'getattr(previous, name))'
        assert selected_text.count(selected_marker) == 1
        selected_text = selected_text.replace(selected_marker,
            "quote_loader(old) if name == '_load_quotes' else getattr(previous, name))")
        selected_namespace = dict(vars(fixture), quote_loader=quote_loader)
        exec(compile(selected_text, '<native109-current-quote-loader>', 'exec'),
             selected_namespace)
        monkeypatch.setattr(fixture, 'selected', selected_namespace['selected'])
        marker = "    namespace=dict(vars(prior));namespace['capture_output']=counts['output'].append"
        assert source.count(marker) == 1
        injection = """
    if cold_graph:
        sql = 'def execute(self,sql,*args,**kwargs):return controlled_price_rows(plans,sql)'
        assert text.count(sql) == 1
        text = text.replace(sql,
            'def execute(self,sql,*args,**kwargs):return cold_sql.execute(sql) if sql in cold_sql.responses else prepared_quote_select_rows(source,sql) if \\'FROM arte.liquidity_100ms_v1 AS l \\' in sql else controlled_price_rows(plans,sql)')
        cold = '            class SourceRows:'
        assert text.count(cold) == 1
        text = text.replace(cold,
            '            cold_sql=bind_complete_cold_configuration(source,configuration_transport,source_guard)\\n'
            '            monkeypatch.setattr(root_native,\\'verify_current_installed_source\\',source_guard)\\n'
            '            monkeypatch.setattr(normal_native,\\'verify_current_installed_source\\',source_guard)\\n' + cold)
        execute = "    exec(compile(source,'<controlled-native109>','exec'),namespace)"
        assert text.count(execute) == 1
        injected = "    source=source.replace('parent=source_fixture()', 'parent=configuration_transport.parent')\\n"
        for name in ('configuration_transport','bind_complete_cold_configuration','prepared_quote_select_rows',
                     'source_guard','root_native','normal_native'):
            injected += "    namespace[" + repr(name) + "]=" + name + "\\n"
        text = text.replace(execute, injected + execute)
"""
        source = source.replace(marker, injection.strip('\n') + '\n' + marker)
        execute = "    exec(compile(text,'<controlled-native109>','exec'),namespace)"
        assert source.count(execute) == 1
        source = source.replace(execute,
            "    namespace.update(configuration_transport=configuration_transport,"
            "bind_complete_cold_configuration=bind_complete_cold_configuration,"
            "prepared_quote_select_rows=prepared_quote_select_rows,"
            "source_guard=source_guard,root_native=root_native,normal_native=normal_native)\n" + execute)
        namespace.update(configuration_transport=transport,
            bind_complete_cold_configuration=bind_complete_cold_configuration,
            prepared_quote_select_rows=prepared_quote_select_rows,
            source_guard=source_guard, root_native=root_native, normal_native=normal_native)
    exec(compile(source, '<reviewable-native109-fixture>', 'exec'), namespace)
    counts = namespace['_actual109'](monkeypatch, paired=paired, cold_probe=cold_probe,
                                      cold_graph=cold_graph)
    assert any(read['retained_source'] for read in reads)
    assert initial._READ.get() is None and initial._OPERATION.get() is None
    assert management._ACTIVE.get() is None and management._CONTEXT_OWNER.get() is None
    assert all(operation not in initial._ISSUED and not operation.cache
               and operation.bytes == 0 for operation in operations)
    receipt = dict(strategy=109, native_fixture_transport_seams=True,
        entry_and_recovery_issuers_unchanged=True, complete_journal_loader_unchanged=True,
        reads=reads, faults=faults, probes=probes, issued_operations_discarded=True,
        measurements=counts, full_session_acceptance=False,
        financial_acceptance=False)
    path = Path('D:/TradingML/runtimes/strategy-optimization-20261005') / (
        f'strategy109-native-first-inventory-{tag}-v1.json')
    with path.open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
    print('native_qualification_receipt=' + str(path))
    return counts, reads


def test_native_initial_and_proposal_reads_match_independent_control(monkeypatch):
    counts, reads = actual_successor(monkeypatch, tag='paired', paired=True)
    assert len(counts['initial_pairs']) == 1
    assert len(counts['proposal_pairs']) >= 3
    assert len(counts['output']) == 1
    assert any(read['retained_source'] and read['operation_type'] ==
        'InitialHeldRecoveryReusePolicy' for read in reads)
    assert any(read['retained_source'] and read['operation_type'] ==
        'ProposalDecisionInventoryReusePolicy' for read in reads)
    assert any(not read['retained_source'] for read in reads)


def test_native_foreign_reader_uses_complete_source_reconstruction(monkeypatch):
    counts, _ = actual_successor(monkeypatch, tag='foreign', cold_probe=True)
    assert counts['foreign_probed'] and counts['cold_complete'] > 0


def test_native_loader_exception_and_cancellation_restore_and_discard_scope(monkeypatch):
    actual_successor(monkeypatch, tag='cancellation', fault_probe=True)


def test_genuine_initial_and_proposal_scope_rejects_mutated_authorities(monkeypatch):
    def probe(operation, original, inventory_key):
        source = operation.proof.owner.operation.source
        from src.trading_runtime.strategy_registry import fixed_strategy_executor
        release = source.installed_payload['strategy']['numbered_release']['contract']
        registration = fixed_strategy_executor(release['executor_strategy_id'],
                                                release['executor_revision'])
        request = operation.proof.normalized_snapshot[4][0][1]
        proposal = request.entry.proposal
        quote = source._quotes[(proposal.ticker, proposal.boundary_ms)]
        context = operation.proof.normalized_snapshot[4][0][0]
        packet = context.unit.packet
        from types import MappingProxyType
        function = first_source.selected_first_inventory_source_policy
        payload = source.installed_payload
        payload['strategy']['parameters']['first_inventory_source_reuse_policy']['max_contexts'] += 1
        mutations = (
            (function, '__code__', function.__code__.replace(co_name='foreign_first_source_selector')),
            (source, 'installed_json', json.dumps(payload)),
            (registration, 'contract_factory', lambda: None),
            (quote, 'bid_int', quote.bid_int + 1),
            (packet, 'root', MappingProxyType({**packet.root, 'configuration_nodes_hash': '0' * 64})),
        )
        denied = []
        for target, attribute, changed in mutations:
            saved = getattr(target, attribute)
            calls = []
            def loader():
                calls.append(True)
                return ()
            try:
                object.__setattr__(target, attribute, changed)
                with pytest.raises(ValueError):
                    original(operation, loader, inventory_key=inventory_key)
                assert not calls
                denied.append(attribute)
            finally:
                object.__setattr__(target, attribute, saved)
            operation.require()
        return {'denied_before_loader': denied}
    actual_successor(monkeypatch, tag='mutations', scope_probe=probe)


def test_oversized_inventory_discards_retained_result_and_reloads_complete_cold(monkeypatch):
    def probe(operation, original, inventory_key):
        outcomes = []
        for kind in ('rows', 'bytes'):
            rows = ([{}] * (operation.policy.max_inventory_rows + 1) if kind == 'rows'
                    else [{'payload': 'x' * (operation.policy.max_inventory_bytes // 2 + 1)}])
            calls = []
            before = (initial._READ.get(), initial._OPERATION.get(),
                      management._ACTIVE.get(), management._CONTEXT_OWNER.get())
            def complete_transport():
                assert management._ACTIVE.get() is None
                assert management._CONTEXT_OWNER.get() is None
                result = list(rows)
                calls.append((initial._READ.get(), result))
                return result
            result, retained = original(operation, complete_transport,
                                         inventory_key=(inventory_key, kind))
            assert not retained
            assert len(calls) == 2 and calls[0][0] is operation and calls[1][0] is None
            assert result is calls[1][1] and result is not calls[0][1]
            assert len(result) == len(rows) and result == rows
            after = (initial._READ.get(), initial._OPERATION.get(),
                     management._ACTIVE.get(), management._CONTEXT_OWNER.get())
            assert all(left is right for left, right in zip(before, after))
            operation.require()
            outcomes.append({'budget': kind, 'complete_rows': len(result), 'loader_calls': 2,
                             'retained_result_returned': False})
        return {'synthetic_inventory_transport': True, 'complete_cold_reloads': outcomes}
    actual_successor(monkeypatch, tag='overflow', scope_probe=probe)


def test_genuine_scope_cannot_retain_future_context_or_changed_batch_prefix(monkeypatch):
    def probe(operation, original, inventory_key):
        prefix = operation.proof.prefix
        assert prefix is not None
        contexts = operation.proof.frontier[-2]
        assert contexts
        records = tuple(context.record for context in contexts)
        assert all(0 < record.sequence <= prefix.last_sequence for record in records)
        changes = (
            (records[0], 'sequence', prefix.last_sequence + 1),
            (prefix, 'batch_ids', (*prefix.batch_ids, 'uncommitted-future-batch')),
            (prefix, 'last_sequence', prefix.last_sequence + 1),
        )
        denied = []
        for target, attribute, changed in changes:
            saved = getattr(target, attribute)
            calls = []
            def loader():
                calls.append(True)
                return ()
            try:
                object.__setattr__(target, attribute, changed)
                with pytest.raises(ValueError):
                    original(operation, loader, inventory_key=inventory_key)
                assert not calls
                denied.append(attribute)
            finally:
                object.__setattr__(target, attribute, saved)
            operation.require()
        return {'valid_predecessor_contexts': len(contexts),
                'prefix_sequence': prefix.last_sequence,
                'future_or_changed_prefix_denied_before_loader': denied}
    actual_successor(monkeypatch, tag='future-prefix', scope_probe=probe)


def test_independently_prepared_v20_cold_graph_has_no_owned_reuse_or_writer_lease(monkeypatch):
    counts, _ = actual_successor(monkeypatch, tag='independent-cold-v20', cold_graph=True)
    cold = counts['cold_graph']
    assert cold['contexts'] > 0 and cold['groups'] > 0
    assert cold['distinct_source'] and cold['no_writer_lease']
    assert cold['companion_mutation_rejected']
