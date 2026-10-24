"""Aya actual consolidated IFRS statements, pinned to retained source bytes."""
from decimal import Decimal
from .contracts import digest
from .sec_html_context import VERSION

HASH = 'cf0b35d9a7b1078788daeb0def4d3e65b0e49c392fdd09ac7469fe441040ee70'


def plan_for(document_hash, cik):
    if document_hash != HASH or str(int(cik)).zfill(10) != '0001826836':
        return None
    rows = [
        (2, 5, 'Cash and cash equivalents (Note 21)', 'CashAndCashEquivalents', ('136322', '30944')),
        (2, 9, 'Inventories (Note 5)', 'Inventories', ('34595', '27389')),
        (2, 23, 'TOTAL ASSETS', 'Assets', ('631733', '400107')),
        (2, 38, 'TOTAL LIABILITIES', 'Liabilities', ('207909', '153626')),
        (2, 45, 'TOTAL EQUITY', 'Equity', ('423824', '246481')),
        (5, 4, 'Revenue (Note 15)', 'Revenue', ('202102', '39117')),
        (5, 6, 'Gross profit', 'GrossProfit', ('83167', '5382')),
        (5, 13, 'Operating income (loss)', 'ProfitLossFromOperatingActivities', ('62517', '-38747')),
        (5, 20, 'Net income (loss)', 'ProfitLoss', ('46280', '-26027')),
        (5, 32, 'Diluted income (loss) per common share (Note 23)(3)', 'DilutedEarningsLossPerShare', ('0.32', '-0.17')),
        (5, 34, 'Weighted average number of shares - diluted (Note 23)', 'AdjustedWeightedAverageShares', ('142370945', '129842508')),
        # Unlabelled totals are bounded by exact adjacent section rows, not inferred by value.
        (2, 14, '', 'CurrentAssets', ('232450', '76540')),
        (2, 32, '', 'CurrentLiabilities', ('120050', '53116')),
        (9, 22, '', 'CashFlowsFromUsedInOperatingActivities', ('71948', '-8615')),
    ]
    headings = {2: 'Consolidated Statements of Financial Position',
                5: 'Consolidated Statements of Comprehensive Income (Loss)',
                9: 'Consolidated Statement of Cash Flows'}
    headers = {2: [[1, ['', 'December 31, 2025', 'December 31, 2024']]],
               5: [[1, ['', 'Years ended December 31,']], [2, ['', '2025', '2024']]],
               9: [[1, ['', 'Years ended December 31,']], [2, ['', '2025', '2024']]]}
    boundaries = {(2, 14): [[4, ['Current', '', '']], [15, ['Non-current', '', '']]],
                  (2, 32): [[25, ['Current', '', '']], [33, ['Non-current', '', '']]],
                  (9, 22): [[4, ['OPERATING ACTIVITIES', '', '']], [23, ['INVESTING ACTIVITIES', '', '']]]}
    facts = []
    for table, row, label, tag, values in rows:
        for i, value in enumerate(values):
            year = 2025 - i
            unit = 'USD/shares' if tag == 'DilutedEarningsLossPerShare' else 'shares' if tag == 'AdjustedWeightedAverageShares' else 'USD'
            scale = 3 if unit == 'USD' else 0
            facts.append(dict(concept='{https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full}' + tag,
                period_start=None if table == 2 else f'{year}-01-01', period_end=f'{year}-12-31',
                unit=unit, scale=scale, decimals='-3' if scale else '2' if unit == 'USD/shares' else '0',
                expected_value=str(Decimal(value) * (10 ** scale)), label=label,
                evidence=['AYA GOLD & SILVER INC.', 'International Financial Reporting Standards',
                          'Expressed in thousands of US dollars'],
                table_proof=dict(heading=headings[table], headers=headers[table] + boundaries.get((table, row), [])),
                locator=dict(kind='cells', table=table, row=row, columns=[i + 1], label_column=0)))
    body = dict(version=VERSION, document_hash=document_hash, cik='0001826836', scope='reviewed_observations', facts=facts)
    return dict(body, plan_hash=digest(body))
