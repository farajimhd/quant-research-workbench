from unittest import TestCase
from pipelines.certified_research.capacity import PilotRate,admission


class CapacityAdmission(TestCase):
    def test_counts_additional_units_and_peak_concurrency_without_double_counting_disk_space(self):
        storage=dict(evidence_hash='a'*64,disks=[dict(name=p,free_space=10000,unreserved_space=9000)
            for p in ('live_market_ssd','sip_raw_ssd')])
        live=PilotRate('bars','b'*64,'c'*64,10,1000,100,50,2)
        source=PilotRate('events','d'*64,'e'*64,10,1000,100,50,2,policy='sip_raw_ssd')
        args=dict(active_units={'bars':2,'events':2},reserve_bytes={'live_market_ssd':1000,'sip_raw_ssd':1000},
            volume_groups={'live_market_ssd':'D_SSD','sip_raw_ssd':'D_SSD'})
        result=admission(storage,{'bars':30,'events':30},[live,source],{'bars':'b'*64,'events':'d'*64},**args)
        self.assertEqual(result['products'][0]['persisted_bytes'],3750)
        self.assertEqual(result['products'][0]['transient_bytes'],188)
        self.assertEqual(result['volumes']['D_SSD']['available_bytes'],7000)
        self.assertEqual(result['volumes']['D_SSD']['required_bytes'],7876)
        self.assertEqual(result['status'],'blocked')
        separate=dict(args,volume_groups={'live_market_ssd':'D_SSD','sip_raw_ssd':'OTHER_SSD'})
        self.assertEqual(admission(storage,{'bars':30,'events':30},[live,source],{'bars':'b'*64,'events':'d'*64},**separate)['status'],'admitted')

    def test_missing_or_incompatible_pilots_block_and_invalid_inputs_fail(self):
        storage=dict(evidence_hash='a'*64,disks=[dict(name=p,free_space=1000,unreserved_space=1000)
            for p in ('live_market_ssd','sip_raw_ssd')])
        args=dict(active_units={'bars':1},reserve_bytes={'live_market_ssd':0,'sip_raw_ssd':0},
            volume_groups={'live_market_ssd':'D','sip_raw_ssd':'D'})
        self.assertEqual(admission(storage,{'bars':1},[],{'bars':'b'*64},**args)['status'],'blocked')
        rate=PilotRate('bars','c'*64,'d'*64,1,100,0,0,1)
        self.assertEqual(admission(storage,{'bars':1},[rate],{'bars':'b'*64},**args)['status'],'blocked')
        with self.assertRaises(ValueError):admission(storage,{'bars':1},[rate],{'bars':'c'*64},**dict(args,volume_groups={}))
        with self.assertRaises(ValueError):admission(storage,{'bars':1},[rate],{'bars':'c'*64},**dict(args,active_units={'bars':2}))
