"""Fort Technology audited actual consolidated statements, not its later exhibit-only amendment."""
from decimal import Decimal
from .contracts import digest
from .sec_html_context import VERSION

HASH = 'b7fc14cf644704fecec4c7be45a4b7001adb81dec0b814d8446b08ae32228fa6'


def plan_for(document_hash, cik):
    if document_hash != HASH or str(int(cik)).zfill(10) != '0002092967':
        return None
    rows = [
        (329, 4, 'Cash and cash equivalents', 'CashAndCashEquivalents', ('605', '546')),
        (329, 7, 'Inventory', 'Inventories', ('3862', '3119')),
        (329, 15, 'TOTAL ASSETS', 'Assets', ('7492', '4217')),
        (329, 31, 'TOTAL LIABILITIES', 'Liabilities', ('4699', '2813')),
        (329, 39, 'TOTAL SHAREHOLDERS’ EQUITY', 'Equity', ('2793', '1404')),
        (331, 3, 'Revenues', 'Revenue', ('10846', '9875')),
        (331, 6, 'Gross profit', 'GrossProfit', ('1079', '1649')),
        (331, 14, 'Operating profit (loss)', 'ProfitLossFromOperatingActivities', ('-1260', '528')),
        (331, 26, 'Net profit (loss) and total comprehensive profit (loss) for the year', 'ProfitLoss', ('-5830', '360')),
        (331, 28, 'Earnings (loss) per common share (basic and diluted)', 'DilutedEarningsLossPerShare', ('-0.69', '0.05')),
        (336, 6, 'Net cash provided by (used in) operating activities', 'CashFlowsFromUsedInOperatingActivities', ('-1233', '523')),
    ]
    headings = {329: 'CONSOLIDATED STATEMENTS OF FINANCIAL POSITION',
                331: 'CONSOLIDATED STATEMENTS OF PROFIT OR LOSS', 336: 'CONSOLIDATED STATEMENTS OF CASH FLOWS'}
    headers = {329: [[0, ['', '', '', '', 'December 31', '']], [1, ['', '', 'Note', '', '2025', '', '', '2024', '']]],
               331: [[0, ['', '', '', '', 'Year ended December 31', '']], [1, ['', '', 'Note', '', '2025', '', '', '2024', '']]],
               336: [[0, ['', '', 'Year ended December 31', '']], [1, ['', '', '2025', '', '', '2024', '']]]}
    facts = []
    for table, row, label, tag, values in rows:
        for i, value in enumerate(values):
            year = 2025 - i
            unit = 'USD/shares' if tag == 'DilutedEarningsLossPerShare' else 'USD'
            scale = 0 if unit == 'USD/shares' else 3
            column = (3 if table == 336 else 5) + i * 4
            facts.append(dict(concept='{https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full}' + tag,
                period_start=None if table == 329 else f'{year}-01-01', period_end=f'{year}-12-31',
                unit=unit, scale=scale, decimals='-3' if scale else '2',
                expected_value=str(Decimal(value) * (10 ** scale)), label=label,
                evidence=['FORT TECHNOLOGY INC.', 'International Financial Reporting Standards', 'U.S. dollars in thousands'],
                table_proof=dict(heading=headings[table], headers=headers[table]),
                locator=dict(kind='cells', table=table, row=row,
                             columns=[column, column + 1] if value.startswith('-') else [column], label_column=0)))
    body = dict(version=VERSION, document_hash=document_hash, cik='0002092967', scope='reviewed_observations', facts=facts)
    return dict(body, plan_hash=digest(body))
