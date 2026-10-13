from datetime import date,datetime
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs
from services.reference_gateway.providers import MassiveReferenceClient
from pipelines.certified_research.historical_identity import normalize,reconcile


class HistoricalIdentityAcquisition(TestCase):
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
