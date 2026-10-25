import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from tests.test_vector_v51_pilot import members,metrics
from research.vectorized_backtest.v51.initial_rescore import rescore,validate_import
from research.vectorized_backtest.v51.reproduction_resume import reproduce
from research.vectorized_backtest.v51.full_common import aggregate
from research.vectorized_backtest.v51.objectives import Objective
from research.vectorized_backtest.v51.local_runner import CompactPedigree
from research.vectorized_backtest.v51.genome import sample
from research.vectorized_backtest.v51.species import Species
from research.vectorized_backtest.v51.identity import digest
from research.vectorized_backtest.v42.publication import write_json,write_checkpoint
from research.vectorized_backtest.v42.distributed_assets import sha

RUNTIME=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v51/tests')


class RescoreTests(unittest.TestCase):
    def test_new_cost_changes_only_score_and_preserves_input(self):
        m=metrics(-10,2);m['drawdown']=[50.,0.]
        old={k:v.tolist() for k,v in aggregate([m],Objective(drawdown_weight=1.,loss_weight=.1)).items()}
        before=digest(old);new=rescore(old)
        self.assertEqual(new['score'],[-13.,-10.5]);self.assertEqual(digest(old),before)
        self.assertEqual({k:v for k,v in old.items() if k!='score'},{k:v for k,v in new.items() if k!='score'})
        direct=aggregate([m]);self.assertEqual(new['score'],direct['score'].tolist())

    def test_rescore_rejects_invalid_metrics(self):
        m={k:v.tolist() for k,v in aggregate([metrics(1,1)]).items()}
        for key,value in [('net_pnl',float('nan')),('drawdown',-1),('gross_losses',-1)]:
            with self.assertRaises(ValueError):rescore(dict(m,**{key:[value]}))

    def test_saved_founder_rng_reproduction_is_repeatable_without_replay(self):
        pop=members(30);ped=CompactPedigree(3);ids=ped.birth(pop,1,[(-1,-1)]*30,['fresh']*30)
        initial=dict(population=[m.payload() for m in pop],ids=ids.tolist(),pedigree=list(ped.records.values()),
            next_serial=ped.next_serial,rng=np.random.default_rng(2020).bit_generator.state,species=Species().state)
        m=metrics(10,30);old={k:v.tolist() for k,v in aggregate([m],Objective(drawdown_weight=1.,loss_weight=.1)).items()}
        saved=dict(panel=rescore(old));job=dict(population=10,selection={})
        with patch('research.vectorized_backtest.v51.initial_population.immigrant',side_effect=lambda rng,probe:sample(rng,1)[0]):
            one,_=reproduce(json.loads(json.dumps(initial)),saved,[m],job,2,probe={},repaired=True)
            two,_=reproduce(json.loads(json.dumps(initial)),saved,[m],job,2,probe={},repaired=True)
        self.assertEqual(digest(one),digest(two));self.assertEqual(len(one['population']),10)
        self.assertEqual(initial['next_serial'],30)

    def test_resume_requires_bound_founder_manifest_and_rejects_changed_cost(self):
        RUNTIME.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(dir=RUNTIME) as tmp:
            root=Path(tmp);(root/'global').mkdir()
            job=dict(version='v51-full-v1',campaign=str(root),source_sha256='source',objective=dict(drawdown_weight=.05,loss_weight=.05),
                execution={},training_sha256='training',validation_sha256='validation')
            with self.assertRaisesRegex(ValueError,'founder reuse'):validate_import(job)
            state=dict(completed=1,seals={'1':'seal'})
            origin=dict(new_source_sha256='source',population=6144,initial_market_replays=0,first_new_replay_generation=2,
                validation_feedback=False,generation1_complete_sha256='seal',checkpoint_state_sha256=digest(state),
                **{k:job[k] for k in ('objective','execution','training_sha256','validation_sha256')})
            write_json(root/'initial-reuse.json',origin);job['initial_reuse_sha256']=sha(root/'initial-reuse.json')
            contract=dict(initial_reuse_sha256=job['initial_reuse_sha256']);write_checkpoint(root/'global/checkpoint.json',contract,state)
            validate_import(job)
            with self.assertRaisesRegex(ValueError,'objective'):validate_import(dict(job,objective=dict(drawdown_weight=1.,loss_weight=.1)))
            write_checkpoint(root/'global/checkpoint.json',contract,dict(state,population=['changed']))
            with self.assertRaisesRegex(ValueError,'successor changed'):validate_import(job)


if __name__=='__main__':unittest.main()
