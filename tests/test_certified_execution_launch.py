from datetime import date
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock,patch
from uuid import uuid4
from pipelines.certified_research.launch import ExecutionLaunch,combine_registries
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit
from pipelines.certified_research.contracts import CampaignConfig,digest


class ExecutionLaunchTests(TestCase):
    def test_admission_binds_exact_plan_and_prevents_writes_on_failure(self):
        dataset=str(uuid4());claim=SimpleNamespace(check=Mock());guard=Mock();query=Mock()
        config=CampaignConfig(date(2026,1,2),date(2026,1,2))
        runtime=SimpleNamespace(dataset=dataset,database='arte',claim=claim,request=Mock(),engine=Mock())
        plans=(PlannedUnit(Unit('bars','market_aggregate'),dict(day='2026-01-02',listing_key=1,ticker='TEST')),)
        def capacity(plan_hash,units):
            value=dict(status='admitted',plan_hash=plan_hash,units=len(units))
            return dict(value,certificate_hash=digest(value))
        launch=ExecutionLaunch(query,config,dataset,plans,'a'*64,claim,Mock(),guard,capacity,Mock(),runtime)
        schema_patch=patch('pipelines.certified_research.launch.installation.verify')
        schema_check=schema_patch.start();self.addCleanup(schema_patch.stop)
        self.assertEqual(launch.admit()['plan_hash'],launch.controller.plan_hash)
        schema_check.assert_called_once_with(query,'arte',require_tables=True)
        query.assert_not_called()
        launch.capacity_guard=lambda *args:dict(status='blocked')
        with self.assertRaisesRegex(ValueError,'blocked'):
            launch.run()
        query.assert_not_called()
        launch.capacity_guard=lambda *args:dict(status='admitted',plan_hash='f'*64)
        with self.assertRaisesRegex(ValueError,'another execution'):
            launch.admit()
        launch.capacity_guard=lambda h,p:dict(status='admitted',plan_hash=h,certificate_hash='f'*64)
        with self.assertRaisesRegex(ValueError,'fingerprint'):
            launch.admit()
        launch.capacity_guard=Mock()
        schema_check.side_effect=ValueError('Installation tables are missing')
        with self.assertRaisesRegex(ValueError,'missing'):launch.run()
        launch.capacity_guard.assert_not_called()
        query.assert_not_called()

    def test_registry_conflicts_and_missing_verifiers_fail_before_execution(self):
        callback=Mock()
        first=SimpleNamespace(registry=lambda:({'source':callback},{'source':callback}))
        with self.assertRaisesRegex(ValueError,'Competing'):
            combine_registries((first,first))
        bad=SimpleNamespace(registry=lambda:({'source':callback},{}))
        with self.assertRaisesRegex(ValueError,'authority verifier'):
            combine_registries((bad,))
