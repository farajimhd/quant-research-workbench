from datetime import date,datetime
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs
from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock
from services.reference_gateway.providers import MassiveReferenceClient
from pipelines.certified_research.historical_identity import normalize,reconcile


class HistoricalIdentityAcquisition(TestCase):
    def test_executor_handler_resume_stop_and_identity_rejection(self):
        from pipelines.certified_research import historical_identity as h
        payload=dict(ticker='TEST',market='stocks',locale='us',composite_figi='BBG000000001',
            primary_exchange='XNAS',currency_name='usd',type='CS',active=True)
        observation=h.normalize(payload,date(2026,1,2),'TEST')
        client=Mock();provider=Mock();provider.fetch_ticker_overview.return_value=payload
        dictionary=Mock(dictionary_database='certified_research_test_identity')
        handlers=h.AcquisitionHandlers('certified_research_test_identity',Mock(),lambda:client,
            lambda:provider,lambda:dictionary,Mock())
        plan=SimpleNamespace(unit=SimpleNamespace(product='identity_observation'),
            arguments=dict(as_of='2026-01-02',ticker='TEST',composite_figi='BBG000000001'))
        with patch.object(h,'load',return_value=None),patch.object(h,'publish') as publish:
            handlers.producer(plan,{},Event())
            provider.fetch_ticker_overview.assert_called_once_with('TEST',as_of=date(2026,1,2))
            publish.assert_called_once_with(client.query,handlers.database,dictionary,[observation])
            dictionary.close.assert_called_once()
        provider.reset_mock()
        with patch.object(h,'load',return_value=observation):
            handlers.producer(plan,{},Event())
            self.assertEqual(handlers.verifier(plan,{})['output_hash'],observation.evidence_hash)
            provider.fetch_ticker_overview.assert_not_called()
        stopped=Event();stopped.set()
        with self.assertRaises(InterruptedError):handlers.producer(plan,{},stopped)
        plan.arguments['composite_figi']='BBG000000002'
        with patch.object(h,'load',return_value=observation):
            with self.assertRaisesRegex(ValueError,'different security'):handlers.producer(plan,{},Event())
        plan.arguments['ticker']='invalid ticker'
        with self.assertRaisesRegex(ValueError,'arguments'):handlers.producer(plan,{},Event())

    def test_dated_provider_calls_and_identity_projection(self):
        client=MassiveReferenceClient(base_url='https://example.invalid',api_key='fixture',page_limit=1000,max_pages=2)
        day=date(2026,1,2)
        payload=dict(ticker='AAPL',market='stocks',locale='us',composite_figi='BBG000B9XRY4',
            share_class_figi='BBG001S5N8V8',cik='320193',primary_exchange='XNAS',currency_name='usd',
            type='CS',active=True,list_date='1980-12-12',market_cap=999,weighted_shares_outstanding=123)
        with patch('services.reference_gateway.providers.fetch_json',return_value={'results':payload}) as fetch:
            observed=client.fetch_ticker_overview('AAPL',as_of=day)
            self.assertEqual(parse_qs(urlparse(fetch.call_args.args[0]).query)['date'],['2026-01-02'])
        observation=normalize(observed,day,'AAPL')
        self.assertEqual(reconcile(observation,expected_composite_figi='BBG000B9XRY4',expected_cik='320193'),observation)
        self.assertFalse(hasattr(observation,'market_cap'))
        corrected=dict(payload,market_cap=1,weighted_shares_outstanding=2)
        self.assertEqual(normalize(corrected,day,'AAPL').evidence_hash,observation.evidence_hash)
        with self.assertRaisesRegex(ValueError,'different security'):
            reconcile(observation,expected_composite_figi='BBG000000000')
        with self.assertRaisesRegex(ValueError,'calendar date'):
            client.fetch_ticker_overview('AAPL',as_of=datetime(2026,1,2))
        with patch('services.reference_gateway.providers.fetch_json',return_value={'results':[payload]}) as fetch:
            client.fetch_us_stock_tickers(active=False,as_of=day)
            params=parse_qs(urlparse(fetch.call_args.args[0]).query)
            self.assertEqual(params['date'],['2026-01-02']);self.assertEqual(params['active'],['false'])

    def test_incomplete_or_conflicting_evidence_cannot_become_an_interval(self):
        payload=dict(ticker='TEST',market='stocks',locale='us',composite_figi='BBG000000001',
            primary_exchange='XNAS',currency_name='usd',active=True,list_date='2026-02-01')
        with self.assertRaisesRegex(ValueError,'lifecycle'):
            normalize(payload,date(2026,1,2),'TEST')
        payload['list_date']='2025-01-01';payload['active']=None
        with self.assertRaisesRegex(ValueError,'incomplete'):
            normalize(payload,date(2026,1,2),'TEST')
