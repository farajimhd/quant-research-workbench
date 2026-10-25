import copy
import json
import tempfile
import unittest
from pathlib import Path
from pipelines.fundamentals.fdic_reports import acquire, inventory


class FDICReportsTest(unittest.TestCase):
    def row(self):
        return dict(orgCertNum=90211, instFlngId=1, sysLstActDttm='2026-08-05',
            sysAddRecDttm='2018-01-01', instFincLglName='Hingham Institution for Savings',
            instFlngForm=dict(instFlngFormNme='Form 10-Q~Quarterly Report', instFlngFormConfInd='N'),
            instFlngAtchList=[dict(instFlngId=1, instFlngAtchId=1, instFlngAtchSizeQty=9,
                instFlngAtchOrglNme='report.pdf', instFlngAtchCrteDttm='2026-08-05T12:00:00Z',
                sysLstActDttm='2026-08-05T12:00:00Z')])

    def test_displayed_date_and_identity(self):
        row = self.row()
        self.assertEqual(len(inventory([row], 'HIFS', '2026-01-01', '2026-10-02')), 1)
        for mutation in [dict(orgCertNum=110), dict(instFlngAllFilesPrivate=True)]:
            with self.assertRaises(ValueError):
                inventory([dict(row, **mutation)], 'HIFS', '2026-01-01', '2026-10-02')
        with self.assertRaises(ValueError):
            inventory([row, row], 'HIFS', '2026-01-01', '2026-10-02')

    def test_acquire_reuse_corruption(self):
        report = inventory([self.row()], 'HIFS', '2026-01-01', '2026-10-02')[0]
        class Response:
            status_code = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
            def iter_content(self, size): yield b'%PDF-test'
        class Session:
            calls = 0
            def get(self, *args, **kwargs):
                self.calls += 1
                return Response()
        session = Session()
        with tempfile.TemporaryDirectory(dir='D:/TradingML/runtimes') as directory:
            first = acquire(report, directory, session)
            self.assertFalse(first['reused'])
            self.assertTrue(acquire(report, directory, session)['reused'])
            self.assertEqual(session.calls, 1)
            changed = dict(report, filename='changed.pdf')
            with self.assertRaises(ValueError): acquire(changed, directory, session)
            Path(directory, first['sha256'] + '.pdf').write_bytes(b'corrupted')
            with self.assertRaises(ValueError): acquire(report, directory, session)


if __name__ == '__main__': unittest.main()
