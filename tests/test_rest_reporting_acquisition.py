from datetime import date,datetime,timezone
from decimal import Decimal
from threading import Event
from unittest import TestCase
from unittest.mock import Mock
from urllib.parse import urlsplit,parse_qs
from pipelines.market_sip.events.rest_reporting_acquisition import TradePages,normalize


BASE=int(datetime(2026,1,2,tzinfo=timezone.utc).timestamp())*10**9

def trade(sip=1000000123,sequence=1):
    return dict(sip_timestamp=BASE+sip,participant_timestamp=None,sequence_number=sequence,conditions=[5,13,30,31,32,33],
                price=Decimal('1.2345'),size=10,decimal_size='10.125',exchange=11,tape=3)


class RestReportingAcquisition(TestCase):
    def test_complete_bounded_pages_preserve_nanoseconds_fractional_size_and_unknown_clock(self):
        fetch=Mock(side_effect=[dict(status='OK',results=[trade()],next_url='https://api.massive.com/v3/trades/TEST?cursor=next'),
                                dict(status='OK',results=[trade(1000001123,2)])])
        provider=TradePages('private-fixture-key',page_limit=1,fetch=fetch)
        pages=list(provider.pages('TEST',date(2026,1,2),Event()))
        self.assertEqual([p[2] for p in pages],[False,True])
        self.assertEqual(pages[0][1][0]['sip_timestamp_ns'],BASE+1000000123)
        self.assertIsNone(pages[0][1][0]['participant_timestamp_ns'])
        self.assertEqual(pages[0][1][0]['size'],'10.125')
        self.assertEqual(len(pages[0][1][0]['conditions']),6)
        self.assertEqual(parse_qs(urlsplit(fetch.call_args_list[0].args[0]).query)['timestamp.gte'],[str(BASE)])

    def test_pagination_stop_failure_and_credentials_cannot_be_silently_accepted(self):
        for next_url,error in [('https://other.invalid/v3/trades/TEST?cursor=x','endpoint'),
                               ('https://api.massive.com/v3/trades/TEST?token=x','credentials')]:
            fetch=Mock(return_value=dict(status='OK',results=[trade()],next_url=next_url))
            with self.assertRaisesRegex(ValueError,error):
                list(TradePages('private-key',page_limit=1,fetch=fetch).pages('TEST',date(2026,1,2),Event()))
            self.assertEqual(fetch.call_count,1)
        fetch=Mock(return_value=dict(status='OK',results=[trade()],next_url='https://api.massive.com/v3/trades/TEST?cursor=x'))
        with self.assertRaisesRegex(ValueError,'page bound'):
            list(TradePages('private-key',page_limit=1,max_pages=1,fetch=fetch).pages('TEST',date(2026,1,2),Event()))
        stop=Event();stop.set()
        with self.assertRaises(InterruptedError):list(TradePages('key',fetch=fetch).pages('TEST',date(2026,1,2),stop))
        fetch=Mock(side_effect=RuntimeError('url contains private-key'))
        with self.assertRaisesRegex(RuntimeError,'page acquisition failed') as raised:
            list(TradePages('private-key',fetch=fetch).pages('TEST',date(2026,1,2),Event()))
        self.assertNotIn('private-key',str(raised.exception))

    def test_invalid_input_and_unsorted_pages_fail_closed(self):
        for changes in (dict(sip_timestamp=True),dict(price='NaN'),dict(sequence_number=-1),dict(conditions=[None])):
            with self.assertRaises(ValueError):normalize(dict(trade(),**changes))
        fetch=Mock(return_value=dict(status='OK',results=[trade(1000001123),trade(1000000123,2)]))
        with self.assertRaisesRegex(ValueError,'unordered'):
            list(TradePages('key',fetch=fetch).pages('TEST',date(2026,1,2),Event()))
        fetch.return_value=dict(status='OK',results=[dict(trade(),sip_timestamp=BASE-1)])
        with self.assertRaisesRegex(ValueError,'UTC source day'):
            list(TradePages('key',fetch=fetch).pages('TEST',date(2026,1,2),Event()))

    def test_transports_keep_public_http_status_without_keys_or_bodies(self):
        from pipelines.market_sip.events.rest_reporting_acquisition import _fetch,TransportError
        from unittest.mock import patch
        from urllib.error import HTTPError
        with patch('urllib.request.urlopen',side_effect=HTTPError('https://private-key',403,'private-body',{},None)):
            with self.assertRaisesRegex(TransportError,'HTTP 403') as raised:_fetch('https://private-key')
        self.assertNotIn('private',str(raised.exception))

    def test_default_transport_preserves_decimal_and_bounds_response_bytes(self):
        from pipelines.market_sip.events.rest_reporting_acquisition import _fetch
        from unittest.mock import patch
        response=Mock();response.read.return_value=b'{"price":1.234567890123456789}'
        context=Mock();context.__enter__=Mock(return_value=response);context.__exit__=Mock(return_value=False)
        with patch('urllib.request.urlopen',return_value=context):
            self.assertEqual(_fetch('https://example.invalid')['price'],Decimal('1.234567890123456789'))
        response.read.assert_called_once_with(8*1024**2+1)
        response.read.return_value=b' '*(8*1024**2+1)
        with patch('urllib.request.urlopen',return_value=context):
            with self.assertRaisesRegex(ValueError,'byte bound'):_fetch('https://example.invalid')
