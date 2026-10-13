import io,json
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import Mock,patch
from pipelines.certified_research import installation
from scripts import prepare_certified_research as launcher


class SchemaInstallation(TestCase):
    def test_complete_catalog_and_runnable_schema_formats(self):
        manifest=installation.manifest()
        self.assertEqual(len(manifest['tables']),28)
        self.assertEqual(len({t['name'] for t in manifest['tables']}),28)
        self.assertEqual({t['group'] for t in manifest['tables']},{'published','acquisition','staging'})
        self.assertTrue(all(c['codec'] for t in manifest['tables'] for c in t['columns']))
        for format in ('json','sql','text'):
            output=io.StringIO()
            with redirect_stdout(output):self.assertEqual(launcher.main(['schema','--format',format]),0)
            if format=='json':self.assertEqual(json.loads(output.getvalue())['installation_hash'],manifest['installation_hash'])
            self.assertIn('research_listing_observation_v1',output.getvalue())
            self.assertIn('research_stage_short_pressure_v1',output.getvalue())

    def test_ownership_and_existing_schema_gate_precede_writes(self):
        from pipelines.certified_research.publication import DictionaryClaim
        query=Mock()
        with self.assertRaisesRegex(ValueError,'ownership'):installation.install(query,'arte',Mock())
        query.assert_not_called()
        claim=Mock(spec=DictionaryClaim);claim.dictionary_database='arte'
        with patch.object(installation,'verify',side_effect=ValueError('explicit migration required')):
            with self.assertRaisesRegex(ValueError,'migration'):installation.install(query,'arte',claim)
        query.assert_not_called()

    def test_install_launcher_releases_database_and_keeper_ownership(self):
        client=Mock();keeper=Mock();claim=Mock();output=io.StringIO()
        with patch('scripts.build_market_day.Client',return_value=client), \
                patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'), \
                patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper), \
                patch('pipelines.certified_research.publication.DictionaryClaim',return_value=claim) as ownership, \
                patch.object(installation,'install',return_value=dict(tables=28,created=0,installation_hash='a'*64)) as install, \
                redirect_stdout(output):
            self.assertEqual(launcher.main(['install-schema','--format','json']),0)
        ownership.assert_called_once_with(keeper.client,'arte')
        install.assert_called_once_with(client.query,'arte',claim)
        claim.close.assert_called_once();keeper.close.assert_called_once();client.close.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())['tables'],28)
