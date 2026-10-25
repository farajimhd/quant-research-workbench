import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from tests.test_vector_v51_pilot import members,metrics
from research.vectorized_backtest.v51.genome import sample
from research.vectorized_backtest.v51.lineage import next_population,program_key
from research.vectorized_backtest.v51.local_runner import CompactPedigree
from research.vectorized_backtest.v51.identity import digest
from research.vectorized_backtest.v51.reproduction_resume import validate_transition,verify_frozen
from research.vectorized_backtest.v42.publication import write_json,write_checkpoint
from research.vectorized_backtest.v42.distributed_assets import sha

RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v51/reproduction-repair-tests')


class ReproductionTests(unittest.TestCase):
    def test_frozen_identity_uses_json_canonical_form(self):
        state=dict(population=[m.payload() for m in members(10)],ids=list(range(10)),rng=np.random.default_rng(1).bit_generator.state)
        saved=json.loads(json.dumps(dict(state,sha256=digest(state))))
        self.assertNotEqual(state['population'],saved['population'])
        verify_frozen(state,saved)
        saved['ids'][0]=99
        with self.assertRaisesRegex(ValueError,'identity mismatch'):verify_frozen(state,saved)

    def evolve(self,scores,eligible,positions):
        pop=members(10);ped=CompactPedigree(3);ids=ped.birth(pop,1,[(-1,-1)]*10,['fresh']*10)
        m=metrics(1,10)
        m['closed_positions']=positions
        for i,count in enumerate(positions):
            if not count:
                for k in ('episode_entry_clocks','episode_exit_clocks','episode_listings'):m[k][i]=[]
        with patch('research.vectorized_backtest.v51.initial_population.immigrant',side_effect=lambda rng,probe:sample(rng,1)[0]) as fresh:
            result,newids,telemetry=next_population(np.random.default_rng(12),pop,ids,ped,[],2,
                session_values=np.asarray([scores]),session_eligible=np.asarray([eligible]),
                global_values=scores,global_eligible=eligible,ordered=[m],target_size=10,probe={})
        return pop,result,newids,ped,telemetry,fresh.call_count

    def test_single_negative_active_parent_gets_real_descendants(self):
        old,new,ids,ped,t,calls=self.evolve([-4]+[0]*9,[True]*10,[1]+[0]*9)
        self.assertEqual(t['elites'],1);self.assertEqual(t['parent_mutations'],7)
        self.assertEqual(t['fresh_candidates'],2);self.assertEqual(t['crossovers'],0)
        self.assertEqual(calls,2);self.assertEqual(ids[0],0)
        descendants=[ped.records[int(i)] for i in ids if ped.records[int(i)]['operation']=='parent_mutation']
        self.assertEqual(len(descendants),7)
        self.assertTrue(all(r['parents']==[0,-1] and r['mutations']==1 and r['ancestors']==[(0,1)] for r in descendants))
        self.assertEqual(len({program_key(m) for m in new}),10)
        self.assertEqual(program_key(new[0]),program_key(old[0]))

    def test_financial_invalid_and_inactive_never_become_parents(self):
        old,new,ids,ped,t,calls=self.evolve([100,0,-4]+[-8]*7,[False]+[True]*9,[1,0]+[1]*8)
        self.assertEqual(ids[0],2)
        self.assertEqual(t['active_parent_candidates'],8)
        self.assertTrue(all(p not in (0,1) for i in ids[1:] for p in ped.records[int(i)]['parents'] if p>=0))
        self.assertGreater(t['crossovers']+t['parent_mutations'],0)

    def test_no_active_parent_uses_bounded_fresh_slots(self):
        _,new,ids,ped,t,calls=self.evolve([0]*10,[True]*10,[0]*10)
        self.assertEqual(t['elites'],0);self.assertEqual(t['fresh_candidates'],10)
        self.assertEqual(t['parent_mutations'],0);self.assertEqual(calls,10)

    def test_deterministic_reproduction(self):
        a=self.evolve([-4]+[0]*9,[True]*10,[1]+[0]*9)
        b=self.evolve([-4]+[0]*9,[True]*10,[1]+[0]*9)
        self.assertEqual(digest([m.payload() for m in a[1]]),digest([m.payload() for m in b[1]]))
        self.assertEqual(a[2].tolist(),b[2].tolist());self.assertEqual(a[3].records,b[3].records)

    def test_fresh_calibration_is_required_and_training_only(self):
        from research.vectorized_backtest.v51.initial_population import immigrant
        with self.assertRaisesRegex(ValueError,'sealed training probe'):immigrant(np.random.default_rng(1),None)
        probe={'training':'sealed'}
        with patch('research.vectorized_backtest.v42.calibration.policy_families',side_effect=lambda rng,p:(p,[])) as families,patch('research.vectorized_backtest.v42.calibration.calibrate',side_effect=lambda rng,p,pr:(p,{})) as calibrate:
            immigrant(np.random.default_rng(1),probe)
        families.assert_called_once();calibrate.assert_called_once();self.assertIs(calibrate.call_args.args[2],probe)

    def test_transition_binds_new_successor_and_original_seals(self):
        RUNTIME.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);(root/'global').mkdir();state=dict(completed=2,seals={'1':'one','2':'two'})
            job=dict(campaign=str(root),source_sha256='new',training_sha256='train',validation_sha256='val',objective={},execution={})
            t=dict(new_source_sha256='new',market_replays=0,resume_after_generation=2,validation_feedback=False,
                checkpoint_state_sha256=digest(state),original_seals=state['seals'],**{k:job[k] for k in ('training_sha256','validation_sha256','objective','execution')})
            path=root/'transition.json';write_json(path,t)
            job.update(reproduction_transition=str(path),reproduction_transition_sha256=sha(path))
            contract=dict(reproduction_transition_sha256=sha(path));write_checkpoint(root/'global/checkpoint.json',contract,state)
            validate_transition(job)
            write_checkpoint(root/'global/checkpoint.json',contract,dict(state,extra='tampered'))
            with self.assertRaisesRegex(ValueError,'successor changed'):validate_transition(job)
            write_checkpoint(root/'global/checkpoint.json',contract,dict(state,completed=3,seals={'1':'changed','2':'two'}))
            with self.assertRaisesRegex(ValueError,'Original seals'):validate_transition(job)


if __name__=='__main__':unittest.main()
