import json
import unittest
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from pipelines.market_sip.events.structural_session_checkpoints import (
    calculate, normalized_boundary, restore_boundary,
)
from src.market_engine.streaming_level_book import StreamingLevelBook

NY = ZoneInfo('America/New_York')


def stamp(hour, minute=0, second=0):
    return datetime(2026, 7, 2, hour, minute, second, tzinfo=NY).timestamp()


def sample():
    return [dict(t=stamp(h, m), open=p, high=p+.02, low=p-.02, close=p, volume=100.)
            for h, m, p in [(4,5,10.),(9,29,10.1),(9,30,10.2),(10,0,10.),
                            (12,59,10.3),(13,0,10.1),(14,0,12.),(20,0,11.)]]


class SessionCheckpoints(unittest.TestCase):
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
        value = self.calc(existing_ah=True)['REGULAR']
        levels, observations, row = normalized_boundary(value, 'b'*64)
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
