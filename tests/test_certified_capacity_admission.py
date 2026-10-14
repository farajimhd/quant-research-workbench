from unittest import TestCase
from pipelines.certified_research.capacity import PilotRate,admission,plan_demands,PlanCapacityGuard
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit
from pipelines.certified_research.contracts import CampaignConfig,digest
from datetime import date
from unittest.mock import patch,Mock


class CapacityAdmission(TestCase):
    def test_backfill_reuses_v7_profile_and_does_not_charge_for_copying_bars(self):
        config=CampaignConfig(date(2026,1,2),date(2026,1,2))
        plans=(PlannedUnit(Unit('bars','certified_bars'),{}),
            PlannedUnit(Unit('levels','v7_backfill',('bars',),config.query_memory_bytes),{}))
        value=plan_demands(config,plans)
        self.assertTrue(value['ready'])
        self.assertEqual(value['demands'],dict(campaign_metadata=2,v7=1))

    def test_plan_demand_includes_metadata_and_never_omits_unknown_writers(self):
        config=CampaignConfig(date(2026,1,2),date(2026,1,2),workers=2)
        plans=(PlannedUnit(Unit('source','canonical_source'),{}),
            PlannedUnit(Unit('bars','market_aggregate',('source',),config.query_memory_bytes),{}),
            PlannedUnit(Unit('levels','v7',('bars',),config.query_memory_bytes),{}))
        value=plan_demands(config,plans)
        self.assertTrue(value['ready'])
        self.assertEqual(value['demands'],dict(campaign_metadata=3,market_aggregate=1,v7=1))
        self.assertEqual(value['active_units']['campaign_metadata'],1)
        unknown=plan_demands(config,plans+(PlannedUnit(Unit('extra','unclassified_writer'),{}),))
        self.assertFalse(unknown['ready'])
        self.assertEqual(unknown['unknown_products'],{'unclassified_writer':1})
        self.assertEqual(unknown['demands']['campaign_metadata'],4)

    def test_exact_plan_guard_blocks_missing_metadata_rate_and_binds_certificate(self):
        config=CampaignConfig(date(2026,1,2),date(2026,1,2))
        plans=(PlannedUnit(Unit('bars','market_aggregate'),{}),)
        storage=dict(evidence_hash='a'*64,disks=[dict(name=p,free_space=100000,unreserved_space=100000)
            for p in ('live_market_ssd','sip_raw_ssd')])
        market=PilotRate('market_aggregate','b'*64,'c'*64,1,100,10,10,1)
        metadata=PilotRate('campaign_metadata','d'*64,'e'*64,1,100,0,10,1)
        args=dict(reserve_bytes={'live_market_ssd':0,'sip_raw_ssd':0},volume_groups={'live_market_ssd':'D','sip_raw_ssd':'D'})
        with patch('pipelines.certified_research.capacity.inventory',return_value=storage):
            guard=PlanCapacityGuard(Mock(),config,[market],{'market_aggregate':'b'*64},**args)
            self.assertEqual(guard('f'*64,plans)['status'],'blocked')
            guard=PlanCapacityGuard(Mock(),config,[market,metadata],{'market_aggregate':'b'*64,'campaign_metadata':'d'*64},**args)
            certificate=guard('f'*64,plans)
            self.assertEqual(certificate['status'],'admitted')
            self.assertEqual(certificate['plan_hash'],'f'*64)
            fingerprint=certificate.pop('certificate_hash')
            self.assertEqual(fingerprint,digest(certificate))
            unknown=guard('f'*64,plans+(PlannedUnit(Unit('other','unknown'),{}),))
            self.assertEqual(unknown['status'],'blocked')

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
