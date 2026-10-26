import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('TRITON_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v55/qualification/triton')
os.environ.setdefault('TORCHINDUCTOR_CACHE_DIR','D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v55/qualification/inductor')
import unittest
from dataclasses import replace
import numpy as np
import torch
from research.vectorized_backtest.v55.genome import sample,node_count
from research.vectorized_backtest.v55.structure import grow,prune,remap_node,remap_head
from research.vectorized_backtest.v55.composition import proposal,cross,mutation
from research.vectorized_backtest.v55.network_pack import pack_networks,select_packed
from tests import test_vector_v55_shared as shared_helpers

def sized(member,size,seed=7):
    net=member.networks['shared'];r=proposal(np.random.default_rng(seed),'test-growth')
    if size<64:net=prune(net,tuple(range(size,64)))
    while len(net.nodes)<size:
        count=max(x for x in (1,2,4,8) if x<=size-len(net.nodes));net=grow(r,net,count)
    return replace(member,networks={'shared':net}).validate()

class StructureTests(unittest.TestCase):
    def original(self):return sample(np.random.default_rng(2021),1)[0]
    def pair(self,a,b,ticks=5,device='cpu'):
        helper=shared_helpers.SharedTests();p,d,pos,opened,prev=helper.setup_plan(device,[a,b],u=7)
        for tick in range(ticks):
            bits=p.signals(d,torch.tensor(tick,device=device),pos,opened,prev)
            self.assertTrue(torch.equal(bits[:,0],bits[:,1]));self.assertTrue(torch.equal(p.ratios[:,0],p.ratios[:,1]))
        p.close()
    def test_growth_preserves_multiclock_outputs_and_original_ids(self):
        m=self.original();larger=sized(m,129)
        self.assertEqual([n.uid for n in m.networks['shared'].nodes],[n.uid for n in larger.networks['shared'].nodes[:64]])
        self.pair(m,larger)
    def test_minimum_and_maximum_budget_are_enforced(self):
        m=self.original();self.assertEqual(node_count(sized(m,506).networks['shared']),512)
        with self.assertRaises(ValueError):sized(m,507)
        self.assertEqual(len(sized(m,16).networks['shared'].nodes),16)
        with self.assertRaises(ValueError):prune(m.networks['shared'],tuple(range(49)))
    def test_duplicate_ids_are_rejected(self):
        n=self.original().networks['shared'];nodes=list(n.nodes);nodes[-1]=replace(nodes[-1],uid=nodes[0].uid)
        with self.assertRaises(ValueError):replace(n,nodes=tuple(nodes)).validate()
    def test_pruning_remaps_every_delayed_and_readout_reference(self):
        net=sized(self.original(),129).networks['shared'];removed={0,31,64,127};out=prune(net,removed)
        self.assertEqual([n.uid for i,n in enumerate(net.nodes) if i not in removed],[n.uid for n in out.nodes])
        for old,new in zip([n for i,n in enumerate(net.nodes) if i not in removed],out.nodes):
            old_ids=[net.nodes[-s-1].uid for s in old.sources if s<0 and -s-1 not in removed]
            new_ids=[out.nodes[-s-1].uid for s in new.sources if s<0];self.assertEqual(old_ids,new_ids)
        out.validate()
    def test_crossover_aligns_ids_after_permutation(self):
        a=sized(self.original(),69);net=a.networks['shared'];order=list(reversed(range(69)));lookup={net.nodes[i].uid:j for j,i in enumerate(order)}
        b=replace(a,networks={'shared':replace(net,nodes=tuple(remap_node(net.nodes[i],net.nodes,lookup) for i in order),heads=tuple(remap_head(h,net.nodes,lookup) for h in net.heads))})
        child,info=cross(np.random.default_rng(2),a,b)
        self.assertEqual(info['matching_ids'],69);self.pair(a,child)
    def test_crossover_imports_unique_units_and_never_truncates(self):
        a=sized(self.original(),80,1);b=sized(self.original(),90,2);c,_=cross(np.random.default_rng(2),a,b)
        self.assertEqual({n.uid for n in c.networks['shared'].nodes},{n.uid for m in (a,b) for n in m.networks['shared'].nodes})
        with self.assertRaises(ValueError):cross(np.random.default_rng(2),sized(a,400,3),sized(b,400,4))
    def test_boolean_composition_imports_missing_donor_dependencies(self):
        from research.vectorized_backtest.v55.whole_network import cross as logical
        a=self.original();b=sized(a,65);net=b.networks['shared'];heads=list(net.heads);h=heads[0];branch=h.branches[0];sources=list(branch.sources);sources[0]=64
        heads[0]=replace(h,branches=(replace(branch,sources=tuple(sources)),));b=replace(b,networks={'shared':replace(net,heads=tuple(heads))}).validate()
        c,info=logical(a,b,'xnor',('entry',));self.assertEqual(info['imported_units'],1);self.assertEqual(len(c.networks['shared'].nodes),65)
    def test_scheduled_growth_and_removal_are_real_node_edits(self):
        a=self.original();r=proposal(np.random.default_rng(3),'scheduled');r.plan={'edits':[dict(mode='structure',field='grow')]}
        b,fields=mutation(r,a,1);self.assertGreater(len(b.networks['shared'].nodes),64);self.assertGreater(fields[0]['node_delta'],0)
        r=proposal(np.random.default_rng(4),'scheduled');r.plan={'edits':[dict(mode='structure',field='remove')]}
        c,fields=mutation(r,b,1);self.assertLess(len(c.networks['shared'].nodes),len(b.networks['shared'].nodes))
    def test_ragged_subset_exact_and_storage_has_no_maximum_padding(self):
        ms=[sized(self.original(),n) for n in (16,65,129,506)];p=pack_networks(ms);self.assertEqual(p['total_nodes'],716)
        a=select_packed(p,[3,0,2,3]);b=pack_networks([ms[i] for i in (3,0,2,3)])
        for key in ('counts','offsets_cpu','uids'):np.testing.assert_array_equal(a[key],b[key])
        for x,y in zip(a['values'],b['values']):np.testing.assert_array_equal(x,y)
    def test_state_restore_rejects_different_genome_ids(self):
        helper=shared_helpers.SharedTests();a=sized(self.original(),65,1);b=sized(self.original(),65,2)
        p,*_=helper.setup_plan(members=[a]);q,*_=helper.setup_plan(members=[b])
        with self.assertRaises(ValueError):q.restore_state(p.export_state())
    @unittest.skipUnless(torch.cuda.is_available(),'CUDA required')
    def test_mixed_width_gpu_parity_and_no_cross_candidate_memory_reads(self):
        helper=shared_helpers.SharedTests();ms=[sized(self.original(),n,seed=n) for n in (16,63,64,65,127,128,129,255,256,257,496,506)]
        # Exercise delayed indices beyond the original 64, including self loops
        # and cross-unit loops at the last cell of every ragged candidate.
        for i,m in enumerate(ms):
            net=m.networks['shared'];nodes=list(net.nodes);n=len(nodes)
            nodes[-1]=replace(nodes[-1],sources=(0,-n),weights=(.3,.4),bias=.1)
            nodes[-2]=replace(nodes[-2],sources=(0,-n),weights=(-.2,.7),bias=-.2)
            heads=list(net.heads);h=heads[0];heads[0]=replace(h,branches=(replace(h.branches[0],sources=(n-1,),weights=(1.,)),))
            ms[i]=replace(m,networks={'shared':replace(net,nodes=tuple(nodes),heads=tuple(heads))}).validate()
        cpu=helper.setup_plan(members=ms,u=19);gpu=helper.setup_plan('cuda',ms,u=19)
        for tick in range(5):
            x=cpu[0].signals(cpu[1],torch.tensor(tick),*cpu[2:]);y=gpu[0].signals(gpu[1],torch.tensor(tick,device='cuda'),*gpu[2:]);self.assertTrue(torch.equal(x,y.cpu()))
            torch.testing.assert_close(cpu[0].ratios,gpu[0].ratios.cpu(),rtol=1e-5,atol=3e-7)
            torch.testing.assert_close(cpu[0].export_state()['memory'],gpu[0].export_state()['memory'],rtol=1e-5,atol=4e-7)
        cpu[0].close();gpu[0].close()
    @unittest.skipUnless(torch.cuda.is_available(),'CUDA required')
    def test_mixed_width_compiled_financial_replay(self):
        helper=shared_helpers.SharedTests();helper.members=lambda n=3:[sized(self.original(),k) for k in (16,65,506)]
        helper.test_real_shared_plan_compiled_replay_matches_eager_cpu()

if __name__=='__main__':unittest.main()
