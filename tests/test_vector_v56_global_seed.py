import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import unittest
from unittest.mock import patch
from dataclasses import replace
from tests.test_vector_v56_whole_network import parent
from research.vectorized_backtest.v56.campaign_seed import seed
from research.vectorized_backtest.v56.local_runner import CompactPedigree
from research.vectorized_backtest.v56.lineage import program_key
from research.vectorized_backtest.v42.templates import FAMILIES

class GlobalSeedTests(unittest.TestCase):
    def test_complete_global_seed_inherits_donor_families_and_keeps_mix_groups(self):
        base=parent(0)
        def member(i):
            return replace(base,networks=dict(base.networks,entry=replace(base.networks['entry'],size_bias=i/10000)))
        days=[f'2026-08-{i+1:02d}' for i in range(24)];exports={}
        for d,day in enumerate(days):
            pop=[member(d*128+i) for i in range(128)];ped=CompactPedigree(3);ids=ped.birth(pop,50,[(-1,-1)]*128,['fresh']*128)
            exports[day]=dict(source_sha256='source',objective={},validation_opened=False,export_sha256=day,
                records=[dict(payload=m.payload(),program_sha256=program_key(m),serial=int(i),generation=50,score=1.,pedigree=ped.records[int(i)]) for i,m in zip(ids,pop)])
        def reproduce(experts,random_seed,progress=None):
            donors=[experts[days[0]][0][0],experts[days[1]][0][0]]
            children=[member(3072+i) for i in range(3072)]
            metadata=[dict(parent_serials=donors,group='2',method='tree:union',source_sessions=days[:2],steps=[],fragments=[]) for _ in children]
            return children,metadata,dict(version='fixture')
        job=dict(seed=2021,campaign_stage='global',training_days=days,source_sha256='source',objective={},expert_capacity_per_session=128,global_initialization=dict(version='v56-blind-mix-v1'))
        with patch('research.vectorized_backtest.v56.blind_mix.reproduce',side_effect=reproduce):result=seed(exports,job,None)
        self.assertEqual((len(result['population']),result['expert_count'],result['blind_count'],result['fresh_count']),(6144,3072,3072,0))
        records={r['serial']:r for r in result['pedigree']}
        for serial in result['ids'][3072:]:
            row=records[serial];self.assertEqual(len(row['family_contributions']),len(FAMILIES))
            self.assertEqual(row['random_ancestry'],2);self.assertEqual(row['template_ancestry'],0)
            self.assertEqual(row['founder_groups'],['2']);self.assertEqual(row['founder_methods'],['tree:union'])
            self.assertEqual(row['source_sessions'],days[:2]);self.assertIn('-R002-',row['label'])
        # Later reproduction must still accept the inherited legacy vector.
        ped=CompactPedigree(3,result['pedigree'],result['next_serial'])
        child=ped.birth([member(7000)],2,[(result['ids'][0],result['ids'][-1])],['crossover'])[0]
        self.assertEqual(ped.records[int(child)]['random_ancestry'],3)

if __name__=='__main__':unittest.main()
