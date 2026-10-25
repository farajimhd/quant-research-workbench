from datetime import datetime
import unittest
from pipelines.fundamentals.fdic_extract import header_periods, numeric_row, lines, extract_document
from pipelines.fundamentals.fdic_publish import availability, financial_sql, ISSUERS


def word(x,y,text):return [x,y,x+len(text)*5,y+10,text]


class FDICExtractTests(unittest.TestCase):
    def test_merged_duration_headers_preserve_last_group(self):
        rows=[[word(100,10,'ThreeMonthsEnded'),word(300,10,'SixMonthsEnded')],
              [word(150,25,'June'),word(175,25,'30,'),word(350,25,'June'),word(375,25,'30,')],
              [word(100,40,'2025'),word(200,40,'2026'),word(300,40,'2025'),word(400,40,'2026')]]
        result=header_periods(rows,80,False,4)
        self.assertEqual(result,[('2025-04-01','2025-06-30'),('2026-04-01','2026-06-30'),
                                 ('2025-01-01','2025-06-30'),('2026-01-01','2026-06-30')])

    def test_dash_and_parentheses(self):
        result=numeric_row([word(0,0,'Cash'),word(50,0,'(1,250)'),word(100,0,'—')])
        self.assertEqual(result[1][0],-1250);self.assertIsNone(result[1][1])
        self.assertIsNone(numeric_row([word(0,0,'Cash'),word(50,0,'(1,250')]))

    def test_public_day_not_download_day(self):
        report=dict(filing_date='2026-08-05',attachment_created_at='2026-08-05T20:00:00Z',attachment_changed_at='2026-08-05T20:00:00Z')
        self.assertEqual(availability(report),'2026-08-06 04:00:00.000000')
        report['attachment_changed_at']='2026-08-07T12:00:00Z'
        self.assertEqual(availability(report),'2026-08-08 04:00:00.000000')

    def test_unreviewed_identity_rejected(self):
        with self.assertRaises(ValueError):financial_sql(['issuer:unreviewed'],datetime.fromisoformat('2026-10-02T23:59:59+00:00'))

    def test_formatting_characters_removed(self):
        self.assertEqual(lines([word(0,0,'\u200c'),word(10,0,'Assets\u200c')])[0][0][4],'Assets')


if __name__=='__main__':unittest.main()
