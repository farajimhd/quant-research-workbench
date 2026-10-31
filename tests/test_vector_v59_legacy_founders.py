import unittest
from dataclasses import asdict
from research.vectorized_backtest.v59.legacy_founders import convert
from research.vectorized_backtest.v59.genome import MARKET_INPUTS,INPUTS,STAGES
from research.vectorized_backtest.v59.feature_policy import ALLOWED
from research.vectorized_backtest.v27.genome import Policy


class LegacyConversion(unittest.TestCase):
    def test_deterministic_conversion_removes_memory_without_sampling(self):
        import copy
        net=dict(nodes=[dict(sources=[ALLOWED[0],-1,MARKET_INPUTS+7],weights=[.5,.2,.3],bias=.1,uid=1,op='tanh',slot=1)],threshold=.2)
        payload=dict(networks={s:copy.deepcopy(net) for s in STAGES},policy=asdict(Policy()),minimum_age={})
        saved=copy.deepcopy(payload)
        a,counts=convert(payload);b,other=convert(payload)
        self.assertEqual(payload,saved);self.assertEqual(a.payload(),b.payload());self.assertEqual(counts,other)
        self.assertEqual(counts['delayed_edges_removed'],5);self.assertEqual(counts['feedback_edges_removed'],5)
        for net in a.networks.values():
            self.assertEqual(net.nodes[0].sources,(ALLOWED[0],))
            self.assertEqual(net.nodes[0].weights,(.5,))


if __name__=='__main__':unittest.main()
