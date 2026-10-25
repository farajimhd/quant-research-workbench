import unittest
from datetime import datetime, timezone
from pipelines.fundamentals.bank_identity import issuer, symbol_rows, REVIEWED, OZKAP_REPORT
from pipelines.fundamentals.population import resolve_ticker


class BankIdentityTest(unittest.TestCase):
    def test_rejected_cik_preserves_reviewed_bank(self):
        security='security:9efe99914dfd0da2c2292c56'
        bank,proof=REVIEWED[security]
        def query(sql):
            if 'id_symbol_interval' in sql:return [dict(security_id=security,listing_id='listing')]
            if 'id_sec_market_bridge' in sql:return [dict(issuer_id=bank,cik='0001961415')]
            return [dict(issuer_id=bank,source_content_sha256=proof)]
        value=resolve_ticker(query,'HIFS',datetime(2026,10,2,tzinfo=timezone.utc))
        self.assertEqual(value['issuer'],bank)
        self.assertIsNone(value['issuer_cik'])

    def test_changed_or_unknown_evidence_blocks(self):
        security=next(iter(REVIEWED))
        for rows in ([],[dict(issuer_id='other',source_content_sha256='bad')]):
            with self.assertRaises(ValueError):issuer(lambda sql:rows,security)

    def test_preferred_public_boundary_and_known_time(self):
        def forbidden(sql):raise AssertionError('No lookup before public symbol evidence')
        self.assertEqual(symbol_rows(forbidden,'OZKAP',datetime(2021,11,10,4,tzinfo=timezone.utc)),[])
        queries=[]
        def query(sql):
            queries.append(sql)
            return [dict(source_hash=OZKAP_REPORT)] if 'reference_source' in sql else []
        symbol_rows(query,'OZKAP',datetime(2026,10,2,tzinfo=timezone.utc),known_at=datetime(2026,10,24,tzinfo=timezone.utc))
        self.assertIn('observed_at<=',queries[0])
        self.assertIn('s.inserted_at<=',queries[1])
        self.assertIn('l.delisted_date>',queries[1])

    def test_competing_sec_issuer_blocks(self):
        security=next(iter(REVIEWED));bank,proof=REVIEWED[security]
        def query(sql):
            if 'id_symbol_interval' in sql:return [dict(security_id=security,listing_id='listing')]
            if 'id_sec_market_bridge' in sql:return [dict(issuer_id='another',cik='0000000001')]
            return [dict(issuer_id=bank,source_content_sha256=proof)]
        with self.assertRaises(ValueError):resolve_ticker(query,'HIFS',datetime(2026,10,2,tzinfo=timezone.utc))
