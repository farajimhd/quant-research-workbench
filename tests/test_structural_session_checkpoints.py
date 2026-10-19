import json
import unittest
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from pipelines.market_sip.events.structural_session_checkpoints import (
    calculate, normalized_boundary, restore_boundary,
)
from src.market_engine.streaming_level_book import StreamingLevelBook

NY = ZoneInfo('America/New_York')


def test_boundary_read_filters_full_key_before_payload(monkeypatch):
    from types import SimpleNamespace
    from pipelines.market_sip.events import structural_checkpoint_storage as storage
    from pipelines.market_sip.events import structural_session_checkpoints as sessions
    calls = []
    header = dict(checkpoint_boundary='PM', publication_revision=123, streaming_state_hash='hash')
    expected = {**header, 'streaming_state': 'validated elsewhere'}
    monkeypatch.setattr(sessions, 'restore_boundary', lambda row: row)
    def query(sql):
        calls.append(sql)
        return [dict(streaming_state='validated elsewhere',streaming_state_hash='hash')] if 'SELECT DISTINCT' in sql else [header]
    client = SimpleNamespace(query=query)
    assert storage.load_boundary(client, ticker="A'B", day='2026-03-11',
        as_of=datetime(2026, 3, 11, 16, tzinfo=NY)) == expected
    sql = calls[0]
    key = sql.split(' PREWHERE ')[1].split(' WHERE ')[0]
    assert ' FINAL PREWHERE ' in sql
    assert key == storage.boundary_key("A'B", '2026-03-11')
    assert ' AND ' not in key
    assert key.count('toDate(') == 2
    assert 'available_at<=' in sql and 'ORDER BY available_at DESC LIMIT 1' in sql
    assert ' FINAL ' not in calls[1] and 'publication_revision=123 LIMIT 2' in calls[1]
    assert calls[0] == calls[2]


def test_boundary_payload_rejects_conflicts_and_publication_races(monkeypatch):
    import pytest
    from types import SimpleNamespace
    from pipelines.market_sip.events import structural_checkpoint_storage as storage
    from pipelines.market_sip.events import structural_session_checkpoints as sessions
    monkeypatch.setattr(sessions, 'restore_boundary', lambda row: row)
    header = dict(checkpoint_boundary='PM',publication_revision=1,streaming_state_hash='h')
    payload = dict(streaming_state='payload',streaming_state_hash='h')
    for responses in [([header], []), ([header], [payload,dict(payload,streaming_state='other')]),
                      ([header], [dict(payload,streaming_state_hash='wrong')]),
                      ([header], [payload], [dict(header,publication_revision=2)])]:
        sequence=iter(responses)
        with pytest.raises(ValueError):
            storage.read_boundaries(SimpleNamespace(query=lambda sql: next(sequence)), 'ABX','2026-03-02')


def stamp(hour, minute=0, second=0):
    return datetime(2026, 7, 2, hour, minute, second, tzinfo=NY).timestamp()


def sample():
    return [dict(t=stamp(h, m), open=p, high=p+.02, low=p-.02, close=p, volume=100.)
            for h, m, p in [(4,5,10.),(9,29,10.1),(9,30,10.2),(10,0,10.),
                            (12,59,10.3),(13,0,10.1),(14,0,12.),(20,0,11.)]]


class SessionCheckpoints(unittest.TestCase):
    def test_certified_empty_day_keeps_prior_ah_and_causal_boundaries(self):
        from copy import deepcopy
        prior=self.calc(existing_ah=False)['AH']
        original=deepcopy(prior)
        value=calculate(prior=prior,ticker='TEST',day='2026-07-06',bars=[],actions=[],
            regular_close=datetime(2026,7,6,16,tzinfo=NY),existing_ah=True)
        self.assertEqual(set(value),{'PM','REGULAR'})
        self.assertEqual(prior,original)
        self.assertEqual(value['PM']['prior_hash'],prior['checkpoint_hash'])
        self.assertEqual(value['PM']['streaming_state'],value['REGULAR']['streaming_state'])
        # The generic calculator still fails closed if asked to fit a missing
        # AH without price data. Only the certified runner handles empty days.
        with self.assertRaisesRegex(ValueError,'Missing canonical price bars'):
            calculate(prior=prior,ticker='TEST',day='2026-07-06',bars=[],actions=[],
                regular_close=datetime(2026,7,6,16,tzinfo=NY),existing_ah=False)

    def test_genesis_reuse_matches_calculated_boundary_without_previous_ah(self):
        from scripts.backfill_structural_sessions import expected_prior_hash
        close=datetime(2026,7,2,13,tzinfo=NY)
        value=calculate(prior=None,ticker='NEW',day='2026-07-02',bars=sample(),
                        actions=[],regular_close=close,existing_ah=True)
        self.assertEqual(expected_prior_hash(None,'NEW','2026-07-02',close),value['PM']['prior_hash'])
        self.assertEqual(expected_prior_hash(None,'NEW','2026-07-02',close),value['REGULAR']['prior_hash'])
        self.assertEqual(expected_prior_hash({'checkpoint_hash':'existing'},'NEW','2026-07-02',close),'existing')

    def test_transport_compatibility_pins_both_requests_and_calculation(self):
        from copy import deepcopy
        from scripts.backfill_structural_sessions import compatible_writer_request,digest
        old=dict(population='reviewed',source_hashes={
            'pipelines/market_sip/events/structural_checkpoint_storage.py':'old',
            'algorithm.py':'same'})
        new=deepcopy(old)
        new['source_hashes']['pipelines/market_sip/events/structural_checkpoint_storage.py']='new'
        def proof(value):return dict(version='structural-exact-float-transport-v1',
            previous_request_hash=digest(old),current_request_hash=digest(value))
        self.assertEqual(compatible_writer_request(new,old,proof(new)),old)
        for changed in ('population','algorithm'):
            bad=deepcopy(new)
            if changed=='population':bad['population']='different'
            else:bad['source_hashes']['algorithm.py']='different'
            with self.assertRaises(ValueError):compatible_writer_request(bad,old,proof(bad))
        with self.assertRaises(ValueError):compatible_writer_request(new,old,proof(old))

    def calc(self, bars=None, **kwargs):
        return calculate(prior=None, ticker='TEST', day='2026-07-02',
            bars=sample() if bars is None else bars, actions=[],
            regular_close=datetime(2026,7,2,13,tzinfo=NY), **kwargs)

    def test_existing_ah_untouched_and_prefix_causality(self):
        value = self.calc(existing_ah=True)
        changed = sample(); changed[-1]['high'] = 999.
        self.assertEqual(value, self.calc(changed, existing_ah=True))
        self.assertEqual(set(value), {'PM', 'REGULAR'})
        self.assertEqual(value['PM']['boundary_at'], stamp(9,30))
        self.assertEqual(value['REGULAR']['boundary_at'], stamp(13))

    def test_full_state_restore_matches_uninterrupted(self):
        value = self.calc(existing_ah=True)
        _, _, row = normalized_boundary(value['PM'], 'a'*64)
        row['session_date'] = '2026-07-02'
        engine, payload = restore_boundary(row)
        for bar in sample():
            if stamp(9,30) < bar['t'] <= stamp(13):
                engine.update(bar)
        self.assertEqual(engine.checkpoint(), value['REGULAR']['streaming_state'])
        row['streaming_state'] = row['streaming_state'].replace('"TEST"', '"WRONG"')
        with self.assertRaises(ValueError):restore_boundary(row)

    def test_normalized_boundaries_are_not_ah(self):
        from copy import deepcopy
        value = self.calc(existing_ah=True)['REGULAR']
        original=deepcopy(value)
        levels, observations, row = normalized_boundary(value, 'b'*64)
        self.assertEqual(value,original)
        self.assertEqual(row['checkpoint_boundary'], 'REGULAR')
        for level in levels:
            self.assertEqual(level['retrospective'], 0)
            self.assertEqual(level['checkpoint_boundary'], 'REGULAR')
        for item in observations:self.assertEqual(item['checkpoint_boundary'], 'REGULAR')
        from src.market_engine.historical_level_checkpoint import digest
        self.assertEqual(digest(json.loads(row['streaming_state'])), digest(value))

    def test_missing_ah_uses_retrospective_algorithm(self):
        from research.level_book.v7.campaign import fit_day
        from src.market_engine.historical_level_checkpoint import digest
        bars = sample()
        engine = fit_day(None, 'TEST', '2026-07-02', bars, [])
        for bar in bars:engine.update(bar)
        value = self.calc(existing_ah=False)
        # Genesis details are the shared empty book contract.
        self.assertEqual(value['AH']['levels'], engine.historical_checkpoint(digest(bars))['levels'])
        self.assertTrue(value['AH']['retrospective'])

    def test_duplicate_or_reversed_inputs_rejected(self):
        with self.assertRaises(ValueError):self.calc(sample()[::-1], existing_ah=True)
        with self.assertRaises(ValueError):self.calc([sample()[0]]*2, existing_ah=True)

    def test_restore_preserves_fitted_levels_and_pending_discovery(self):
        from math import sin
        bars = [dict(t=stamp(9)+(i+1)*15,open=10+sin(i/6),close=10+sin(i/6),
                     low=9.98+sin(i/6),high=10.02+sin(i/6),volume=100.) for i in range(300)]
        values = self.calc(bars,existing_ah=True)
        levels, observations, row = normalized_boundary(values['PM'],'a'*64)
        self.assertGreater(len(levels),0)
        self.assertGreater(len(observations),0)
        engine, _ = restore_boundary(row)
        for bar in bars:
            if bar['t']>stamp(9,30):engine.update(bar)
        self.assertEqual(engine.checkpoint(),values['REGULAR']['streaming_state'])

    def test_bounded_insert_batches_preserve_every_observation(self):
        from types import SimpleNamespace
        from pipelines.market_sip.events.structural_checkpoint_storage import insert,OBSERVATIONS_TABLE
        calls=[]
        client=SimpleNamespace(http=SimpleNamespace(execute=calls.append),
            query=lambda sql: [dict(name='observation_id',type='String'),dict(name='price',type='Float64')])
        rows=[dict(observation_id=str(i),price=2.97) for i in range(5001)]
        insert(client,OBSERVATIONS_TABLE,rows)
        self.assertEqual(len(calls),2)
        decoded=[json.loads(line) for call in calls for line in call.split('\n')[1:]]
        import struct
        for row in decoded:row['price']=struct.unpack('<d',struct.pack('<Q',row['price']))[0]
        self.assertEqual(decoded,rows)
        self.assertTrue(all('reinterpretAsFloat64' in call for call in calls))


if __name__ == '__main__':unittest.main()


class CoreOptimizationParity(unittest.TestCase):
    def test_vector_replay_matches_scalar_across_chunks_gaps_and_resume(self):
        from copy import deepcopy
        prior=SessionCheckpoints().calc(existing_ah=False)['AH']
        begin=datetime(2026,7,6,4,tzinfo=NY).timestamp()
        end=datetime(2026,7,6,20,tzinfo=NY).timestamp()
        def engine():return StreamingLevelBook(prior,ticker='TEST',session='2026-07-06',start=begin,end=end)
        bars=[dict(t=begin+i+1,open=10.,high=10.01,low=9.99,close=10.,volume=100.) for i in range(5200)]
        del bars[600:650]
        scalar=engine();batched=engine()
        for b in bars:scalar.update(b)
        batched.update_many(bars)
        self.assertEqual(scalar.checkpoint(),batched.checkpoint())
        saved=batched.checkpoint();before=deepcopy(saved)
        batched.update(dict(t=begin+6000,open=10.,high=10.1,low=9.9,close=10.,volume=20.))
        self.assertEqual(saved,before)
        restored=StreamingLevelBook.restore(saved)
        for key in ('high','low'):
            if getattr(restored,key) is not None:setattr(restored,key,tuple(getattr(restored,key)))
        restored.update_many([dict(t=begin+6000,open=10.,high=10.1,low=9.9,close=10.,volume=20.)])
        self.assertEqual(restored.checkpoint(),batched.checkpoint())
        with self.assertRaises(ValueError):engine().update_many([bars[1],bars[0]])

    def test_vector_input_matches_scalar_including_dst_and_large_integers(self):
        from pipelines.market_sip.events.structural_session_checkpoints import input_bars
        from pipelines.market_sip.events.causal_v7_product import input_bar
        rows=[dict(bucket_index=14700+i,open_int=p,high_int=p+2,low_int=p-2,close_int=p,volume=123,price_valid=1)
              for i,p in enumerate([1_000_000,9007199254740993]*4200)]
        for day in (date(2026,3,8),date(2026,11,1),date(2026,7,2)):
            self.assertEqual(input_bars(day,iter(rows)),[input_bar(day,r) for r in rows])

    def test_pure_context_cache_retains_duplicate_observation_identity(self):
        from research.level_book.v7.clickhouse_persistence import observation_identity
        from hashlib import sha256
        from research.level_book.v7.clickhouse_persistence import canonical_json
        for occurrence in (0,1,0):
            value=(2.97,.01,100.,110.,'support','2026-01-02',occurrence)
            self.assertEqual(observation_identity(value),sha256(canonical_json(value).encode()).hexdigest())
        self.assertNotEqual(observation_identity((1,0)),observation_identity((1,1)))
        self.assertNotEqual(observation_identity((1,)),observation_identity((1.,)))
        self.assertNotEqual(observation_identity((0.,)),observation_identity((-0.,)))

    def test_streamed_source_detects_cross_batch_duplicates_and_cancels(self):
        from pipelines.market_sip.events.structural_session_checkpoints import source_rows
        from types import SimpleNamespace
        import pyarrow as pa
        calls=[]
        def batches(query,query_id):
            self.assertIn('FORMAT ArrowStream',query)
            yield pa.RecordBatch.from_pylist([dict(bucket_index=14700)])
            yield pa.RecordBatch.from_pylist([dict(bucket_index=14700)])
        client=SimpleNamespace(http=SimpleNamespace(iter_arrow_record_batches=batches),active=None,
                               profile_totals={},cancel=lambda:calls.append('cancel'))
        unit=SimpleNamespace(build_id='b',session_date='2026-01-02',ticker='A',attempt_id='x')
        with self.assertRaisesRegex(ValueError,'Duplicate'):list(source_rows(client,unit))
        self.assertEqual(calls,['cancel']);self.assertIsNone(client.active)
