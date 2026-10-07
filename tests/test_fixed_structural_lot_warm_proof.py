"""Real typed readers with explicit future-rule authority seam, no source seal stub."""
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from time import perf_counter

import pytest

from src.trading_runtime import fixed_structural_lot_warm_proof as subject
from src.trading_runtime import arte_journal_commit_v4 as commits
from src.trading_runtime.journal_contract import canonical_json
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import batch


def prepared(monkeypatch):
    client = attached_v4_client()
    item = batch()
    commits.publish_base_typed_batch_v4(client, item)
    header = client.tables['trading_commit_v4'][0]
    source = SimpleNamespace(price_authority=None)
    lease = SimpleNamespace(assert_current=lambda: None)
    gate = SimpleNamespace(compacted_through=item.last_sequence,
        compacted_batch_id=item.batch_id,
        compacted_commit_hash=sha256(canonical_json(header).encode()).hexdigest())
    scope = [object()]
    # Future release is intentionally not declared/certified here. Only the
    # outer installed authority is a controlled seam; actual prefix and typed
    # details are never mocked or granted a blank certification result.
    monkeypatch.setattr(subject, '_authority', lambda *_a: (
        source, (), (), lease, gate, tuple(scope)))
    client.typed_insert_dispatch = SimpleNamespace(_read_gate=lambda _run:(gate,None))
    return client, item, header, gate, scope


def test_initial_complete_prefix_then_exact_head_hit_and_context_miss(monkeypatch):
    client,item,header,gate,scope = prepared(monkeypatch)
    original = commits.load_verified_v4_prefix
    calls=[]
    def cold(*a,**kw):
        calls.append(1)
        return original(*a,**kw)
    monkeypatch.setattr(commits,'load_verified_v4_prefix',cold)
    first=subject.load_prefix(client,item.run_id)
    assert calls==[1]
    assert subject.load_prefix(client,item.run_id) is first
    assert calls==[1]
    scope.append(object())
    assert subject.load_prefix(client,item.run_id)==first
    assert len(calls)==2
    # A replacement lease identity/context gets a full verification, not reuse.
    scope[0]=object()
    subject.load_prefix(client,item.run_id)
    assert len(calls)==3
    header['source_cursor']='mutated'
    with pytest.raises(RuntimeError,match='head differs'):
        subject.load_prefix(client,item.run_id)
    assert client not in subject._CACHE


def test_initial_bad_detail_cannot_seed_proof(monkeypatch):
    client,item,header,gate,scope=prepared(monkeypatch)
    client.tables['trading_event_v1'][0]['entity_id']='mutated'
    with pytest.raises((RuntimeError,ValueError)):
        subject.load_prefix(client,item.run_id)
    assert client not in subject._CACHE


def test_unadvanced_unknown_frontier_fails_closed(monkeypatch):
    client,item,header,gate,scope=prepared(monkeypatch)
    subject.load_prefix(client,item.run_id)
    gate.compacted_through+=1
    with pytest.raises(RuntimeError,match='head differs'):
        subject.load_prefix(client,item.run_id)


def test_actual_new_committed_frontier_is_completely_verified(monkeypatch):
    from tests.test_arte_journal_commit_v4 import MemoryV4Dispatch
    from src.trading_runtime.arte_journal_writer import typed_row
    client,item,header,gate,scope=prepared(monkeypatch)
    old=subject.load_prefix(client,item.run_id)
    next_id='00000000-0000-0000-0000-000000000091'
    content={k:v for k,v in item.events[0].items() if k!='content_hash'}
    content.update(batch_id=next_id,record_id='00000000-0000-0000-0000-000000000092',sequence=2)
    second=replace(item,batch_id=next_id,prior_batch_id=item.batch_id,
        first_sequence=2,last_sequence=2,source_cursor='bucket-2',
        events=(typed_row('trading_event_v1',content),))
    dispatch=client.typed_insert_dispatch
    client.typed_insert_dispatch=MemoryV4Dispatch()
    commits.publish_base_typed_batch_v4(client,second)
    client.typed_insert_dispatch=dispatch
    gate.compacted_through=2;gate.compacted_batch_id=next_id
    gate.compacted_commit_hash=sha256(canonical_json(client.tables['trading_commit_v4'][-1]).encode()).hexdigest()
    current=subject.load_prefix(client,item.run_id)
    assert current.batch_ids==old.batch_ids+(next_id,)
    assert current.last_sequence==2
    assert subject.load_prefix(client,item.run_id) is current


def test_declared_cold_reader_never_borrows_writer_proof(monkeypatch):
    from src.trading_runtime import arte_oms_projection as oms
    source=SimpleNamespace(installed_payload={'strategy':{'numbered_release':{
        'contract':{'rule_set_contracts':[subject.RULE]}}}},require_installed_admission=lambda:None)
    client=SimpleNamespace(fixed_structural_lot_profile=SimpleNamespace(operation=SimpleNamespace(source=source)))
    monkeypatch.setattr(subject,'load_prefix',lambda *_a:pytest.fail('cold reader borrowed writer'))
    with pytest.raises(ValueError,match='verified bounded prefix'):
        oms.load_latest_committed_oms_groups(client,object())
    source.installed_payload['strategy']['numbered_release']['contract']['rule_set_contracts'].append(subject.RULE)
    with pytest.raises(ValueError,match='one exact declared rule'):
        subject.selected(source)


def test_immutable_transport_does_not_cache_failed_or_mutable_state():
    class Client:
        def execute(self,sql):return '[{"quantity":1}]'
    retained={}
    proxy=subject._ImmutableReads(Client(),retained)
    assert proxy.execute('SELECT immutable')=='[{"quantity":1}]'
    assert not retained  # caller publishes only after complete reader succeeds
    with pytest.raises(ValueError,match='SELECT'):
        proxy.execute('INSERT unsafe')
    with pytest.raises(ValueError,match='SELECT'):
        proxy.execute('SELECT immutable',query_id='changed')


def test_real_oms_reader_reconstructs_fresh_objects_on_transport_hit(monkeypatch):
    from tests import test_arte_oms_projection as fixture
    from src.trading_runtime import arte_oms_projection as oms
    retained=[]
    original=oms._load_latest_committed_oms_groups
    def capture(client,prefix,**kwargs):
        result=original(client,prefix,**kwargs)
        if result and not retained:
            retained.append((client,prefix,result))
        return result
    monkeypatch.setattr(oms,'_load_latest_committed_oms_groups',capture)
    # Real typed intent + OMS children publication and cold integrity negatives.
    fixture.test_oms_group_projection_has_normalized_children_and_committed_fence()
    client,prefix,expected=retained[0]
    # Fixture's final negative mutates one child; restore the exact complete
    # transport captured before its negative controls, not an invented state.
    # Rebuild using the same actual fixture and capture complete SELECT bytes.
    responses={}
    captured=[]
    def capture_again(client,prefix,**kwargs):
        proxy=subject._ImmutableReads(client,{})
        result=original(proxy,prefix,**kwargs)
        if result and not responses:
            responses.update(proxy.new)
            captured.append((client,prefix,result))
        return result
    monkeypatch.setattr(oms,'_load_latest_committed_oms_groups',capture_again)
    fixture.test_oms_group_projection_has_normalized_children_and_committed_fence()
    client,prefix,expected=captured[0]
    proxy=subject._ImmutableReads(SimpleNamespace(execute=lambda _:pytest.fail('cache missed')),responses)
    a=original(proxy,prefix)
    b=original(proxy,prefix)
    assert a==b and a is not b and a[0].group is not b[0].group
    assert a==expected
    a[0].group['filled_quantity']='foreign mutable actor state'
    assert original(proxy,prefix)==b
    with pytest.raises(RuntimeError,match='pinned run authority'):
        original(proxy,prefix,allowed_accounts=frozenset({'foreign'}))
    class Reader:
        fixed_structural_lot_profile=SimpleNamespace(operation=SimpleNamespace(source=object()))
        backtest_v4_lease=object()
        calls=0
        def execute(self,sql):
            self.calls+=1
            return responses[sql]
    reader=Reader()
    # Installed/frontier seam is tested separately above. This exercises the
    # actual cache wrapper AND complete actual OMS typed reader on every hit.
    monkeypatch.setattr(subject,'selected',lambda _:True)
    monkeypatch.setattr(subject,'load_prefix',lambda *_a,**_kw:prefix)
    subject._CACHE[reader]=subject._Proof((),prefix,'controlled-verified-frontier')
    start=perf_counter()
    for _ in range(10):original(reader,prefix)
    cold_s=perf_counter()-start;cold_calls=reader.calls
    first=subject.load_oms_groups(reader,prefix)
    start_calls=reader.calls;start=perf_counter()
    for _ in range(10):assert subject.load_oms_groups(reader,prefix)==first
    warm_s=perf_counter()-start
    assert reader.calls==start_calls
    assert first==expected
    with pytest.raises(RuntimeError,match='pinned run authority'):
        subject.load_oms_groups(reader,prefix,allowed_accounts=frozenset({'foreign'}))
    print(f'actual_typed_oms_10_reads cold_s={cold_s:.6f} warm_s={warm_s:.6f} select_calls_cold={cold_calls} select_calls_warm={reader.calls-start_calls}')


def test_actual_keeper_lease_gate_and_installed_binding_guards(monkeypatch):
    from tests.test_live_signal_completion_keeper import FakeKazoo
    from src.trading_runtime.keeper_session import ManagedKeeperSession
    from src.backend.backtest_v4_keeper_lease import BacktestV4KeeperLease
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch, _Gate, _gate_path
    from src.trading_runtime import fixed_structural_lot_profile as profiles
    source=SimpleNamespace(run_id='62908518-9fd4-4a8e-8c90-2162ccb237e1',
        installed_payload={'strategy':{'numbered_release':{'contract':{'rule_set_contracts':[subject.RULE]}}}},
        installed_json='exact-controlled-installed-tree',selected_configuration_hash='a'*64,
        price_authority=None,valid=True)
    def admit():
        if not source.valid:raise ValueError('mutated installed binding')
    source.require_installed_admission=admit
    # Explicit release owner seam only. Real Keeper lease/dispatch and all new
    # source/run/price/context checks execute; no source certificate is stubbed.
    profile=SimpleNamespace(operation=SimpleNamespace(source=source))
    monkeypatch.setattr(profiles,'require_fixed_structural_lot_profile',lambda p:profile)
    keeper=FakeKazoo();keeper.add_listener=lambda _:None
    session=ManagedKeeperSession(keeper);session._on_state('CONNECTED')
    lease=BacktestV4KeeperLease.acquire(session,run_id=source.run_id,owner_id='controlled-writer')
    dispatch=TypedInsertDispatch(keeper);dispatch.initialize_new_run(source.run_id)
    gate,_=dispatch._read_gate(source.run_id)
    zero='00000000-0000-0000-0000-000000000000'
    keeper.nodes[_gate_path(source.run_id)]=(_Gate('open',0,gate.epoch,0,1,
        '00000000-0000-0000-0000-000000000001','a'*64,zero).wire(),1,0)
    client=SimpleNamespace(fixed_structural_lot_profile=profile,backtest_v4_lease=lease,
        typed_insert_strict=True,typed_insert_dispatch=dispatch)
    assert subject._authority(client,source.run_id,100_000,None)[3] is lease
    with pytest.raises(ValueError,match='foreign source'):
        subject._authority(client,'foreign',100_000,None)
    with pytest.raises(ValueError,match='foreign source'):
        subject._authority(client,source.run_id,100_000,object())
    source.valid=False
    with pytest.raises(ValueError,match='mutated installed'):
        subject._authority(client,source.run_id,100_000,None)
    source.valid=True
    client.fixed_structural_lot_contexts=(SimpleNamespace(source=object()),)
    with pytest.raises(ValueError,match='foreign entry'):
        subject._authority(client,source.run_id,100_000,None)
    client.fixed_structural_lot_contexts=()
    client.typed_insert_strict=False
    with pytest.raises(RuntimeError,match='exclusive writer'):
        subject._authority(client,source.run_id,100_000,None)
    client.typed_insert_strict=True
    lease.release()
    with pytest.raises(RuntimeError):
        subject._authority(client,source.run_id,100_000,None)


def test_absent_rule_preserves_default():
    assert not subject.selected(SimpleNamespace(installed_payload=None))
    assert not subject.selected(SimpleNamespace(installed_payload={'strategy':{
        'numbered_release':{'contract':{'rule_set_contracts':[]}}}}))


def test_bounded_prefix_hit_benchmark(monkeypatch):
    client,item,header,gate,scope=prepared(monkeypatch)
    start=perf_counter()
    for _ in range(10):commits.load_verified_v4_prefix(client,item.run_id)
    cold=perf_counter()-start
    subject.load_prefix(client,item.run_id)
    start=perf_counter()
    for _ in range(10):subject.load_prefix(client,item.run_id)
    warm=perf_counter()-start
    print(f'actual_typed_prefix_10_reads cold_s={cold:.6f} warm_s={warm:.6f} full_walks_cold=10 full_walks_warm=0')
