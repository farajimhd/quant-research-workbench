from datetime import date
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock,patch
from threading import Event
from uuid import uuid4
from pipelines.certified_research.launch import ExecutionLaunch,combine_registries
from pipelines.certified_research.campaign import PlannedUnit,StoredPlan
from pipelines.certified_research.execution import Unit
from pipelines.certified_research.contracts import CampaignConfig,digest


class ExecutionLaunchTests(TestCase):
    def test_original_parity_is_required_before_storage_admission_and_writers(self):
        dataset=str(uuid4());claim=SimpleNamespace(check=Mock());guard=Mock();query=Mock();capacity=Mock()
        config=CampaignConfig(date(2026,1,2),date(2026,1,2))
        runtime=SimpleNamespace(dataset=dataset,database='arte',claim=claim,request=Mock(),engine=Mock())
        plans=(PlannedUnit(Unit('fundamentals','fundamentals'),dict(entity_key=1,cik='320193',
            start='2026-01-02',end='2026-01-02',source_hash='a'*64)),)
        launch=ExecutionLaunch(query,config,dataset,plans,'a'*64,claim,Mock(),guard,capacity,Mock(),runtime)
        with self.assertRaisesRegex(ValueError,'original-document parity'):launch.admit()
        capacity.assert_not_called();query.assert_not_called()
        parity=Mock(return_value=dict(certified=True,all_bulk_matched=True,original_vintage_qualified=True,source_hash='wrong'))
        launch.reference_handlers.original_parity_guard=parity
        with self.assertRaisesRegex(ValueError,'frozen source'):launch.admit()
        capacity.assert_not_called();query.assert_not_called()
        parity.return_value['source_hash']='a'*64
        capacity.side_effect=ValueError('storage reached after parity')
        with patch('pipelines.certified_research.launch.installation.verify'):
            with self.assertRaisesRegex(ValueError,'storage reached'):launch.admit()
        self.assertEqual(parity.call_args.args[0].parent_hash,'a'*64)

    def test_parity_verifier_reaches_reference_producer_unchanged(self):
        from pipelines.certified_research.handlers import ReferenceHandlers
        plan=PlannedUnit(Unit('fundamentals','fundamentals'),dict(entity_key=1,cik='320193',
            start='2026-01-02',end='2026-01-02',source_hash='a'*64))
        client=Mock();parity=Mock()
        handlers=ReferenceHandlers(str(uuid4()),'arte',Mock(),lambda:client,Mock(),(plan,),
            dictionary_claim_factory=Mock(),original_parity_guard=parity)
        with patch('pipelines.certified_research.reference_producer.run_sec') as run:
            handlers.producer(plan,{},Event())
        self.assertIs(run.call_args.kwargs['original_parity_guard'],parity)
        client.close.assert_called_once()

    def test_frozen_specification_locator_automatically_builds_stored_verifier(self):
        from pipelines.certified_research.sec_scope_specification import StoredGuard
        dataset=str(uuid4());claim=SimpleNamespace(check=Mock());query=Mock()
        config=CampaignConfig(date(2026,1,2),date(2026,1,2))
        runtime=SimpleNamespace(dataset=dataset,database='arte',claim=claim,request=Mock(),engine=Mock())
        plans=(PlannedUnit(Unit('fundamentals','fundamentals'),dict(entity_key=1,cik='320193',start='2026-01-02',
            end='2026-01-02',source_hash='a'*64,original_specification_hash='b'*64)),)
        launch=ExecutionLaunch(query,config,dataset,plans,'a'*64,claim,Mock(),Mock(),Mock(),Mock(),runtime)
        self.assertIsInstance(launch.reference_handlers.original_parity_guard,StoredGuard)
        query.assert_not_called()
        saved=StoredPlan(dataset,config,plans,'a'*64,launch.controller.plan_hash)
        with patch('pipelines.certified_research.launch.load_plan',return_value=saved):
            restored=ExecutionLaunch.from_stored(query,'arte',dataset,claim,Mock(),Mock(),Mock(),Mock(),lambda frozen:runtime)
        self.assertIsInstance(restored.reference_handlers.original_parity_guard,StoredGuard)
        self.assertEqual(restored.reference_handlers.original_parity_guard.locators,launch.reference_handlers.original_parity_guard.locators)

    def test_stored_launch_preserves_configuration_and_checks_graph_before_capacity_or_writes(self):
        dataset=str(uuid4());claim=SimpleNamespace(check=Mock());guard=Mock();query=Mock()
        config=CampaignConfig(date(2026,1,2),date(2026,1,5),workers=2)
        runtime=SimpleNamespace(dataset=dataset,database='arte',claim=claim,request=Mock(),engine=Mock())
        plans=(PlannedUnit(Unit('bars','market_aggregate'),dict(day='2026-01-02',listing_key=1,ticker='TEST')),)
        capacity=Mock()
        parity=Mock()
        original=ExecutionLaunch(query,config,dataset,plans,'a'*64,claim,Mock(),guard,capacity,Mock(),runtime,original_parity_guard=parity)
        saved=StoredPlan(dataset,config,plans,'a'*64,original.controller.plan_hash)
        factory=Mock(return_value=runtime)
        with patch('pipelines.certified_research.launch.load_plan',return_value=saved) as loader:
            restored=ExecutionLaunch.from_stored(query,'arte',dataset,claim,Mock(),guard,capacity,Mock(),factory,
                maximum_units=99,maximum_metadata_bytes=8192,original_parity_guard=parity)
            self.assertIs(restored.reference_handlers.original_parity_guard,parity)
            loader.assert_called_once_with(query,'arte',dataset,maximum_units=99,maximum_metadata_bytes=8192)
            factory.assert_called_once_with(saved)
            self.assertIs(restored.config,config)
            self.assertEqual(restored.controller.plan_hash,original.controller.plan_hash)
            query.assert_not_called();capacity.assert_not_called()
            # Runtime initialization cannot alter frozen plan arguments unnoticed.
            def changed(plan):
                plan.units[0].arguments['ticker']='CHANGED'
                return runtime
            with self.assertRaisesRegex(ValueError,'immutable stored plan'):
                ExecutionLaunch.from_stored(query,'arte',dataset,claim,Mock(),guard,capacity,Mock(),changed)
            query.assert_not_called();capacity.assert_not_called()

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
        plans[0].arguments['ticker']='CHANGED'
        with self.assertRaisesRegex(ValueError,'frozen campaign plan'):launch.admit()
        with self.assertRaisesRegex(ValueError,'frozen campaign plan'):
            launch.controller.authority(plans[0].unit,{})
        query.assert_not_called()
        plans[0].arguments['ticker']='TEST'
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
