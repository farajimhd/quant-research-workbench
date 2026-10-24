"""BTQ March 2026 actual IFRS statements; dashes remain unreported, not zero."""
from .contracts import digest
from .sec_html_context import VERSION

HASH='31e103768b55698ee3cf13fc6d180a89032056396afc6da7cc7e5ffb618a09d2'


def plan_for(document_hash,cik):
    if document_hash!=HASH or str(int(cik)).zfill(10)!='0001821866':return None
    headers={1:[[0,['','','March 31, 2026 $','','','December 31, 2025 $','']]],
        5:[[0,['','','Three months ended March 31, 2026 $','','','Three months ended March 31, 2025 $','']]],
        # Preserve the printed truncated year. The exact document title below
        # independently states the two reporting periods; no calendar guess.
        12:[[0,['','','Three months ended March 31, 202 $','','','Three months ended March 31, 2025 $','']]]}
    headings={1:'BTQ TECHNOLOGIES CORP. Condensed Interim Consolidated Statements of Financial Position',
        5:'BTQ TECHNOLOGIES CORP. Consolidated Statements of Operations and Comprehensive Loss',
        12:'BTQ TECHNOLOGIES CORP. Condensed Interim Consolidated Statements of Cash Flows'}
    definitions=[
        (1,9,'Total current assets','CurrentAssets',('14431909','22416087')),
        (1,17,'Total assets','Assets',('26090597','33786999')),
        (1,24,'Total current liabilities','CurrentLiabilities',('2042856','2176993')),
        (1,27,'Total liabilities','Liabilities',('2765562','2176993')),
        (5,23,'Net loss and comprehensive loss for the period','ProfitLoss',('-19927935','-1811980')),
        (5,24,'Loss per share, basic and diluted','DilutedEarningsLossPerShare',('-0.14','-0.01')),
        (5,25,'Weighted average number of common shares outstanding, basic and diluted','AdjustedWeightedAverageShares',('140772652','132121207')),
        (12,16,'Net cash used in operating activities','CashFlowsFromUsedInOperatingActivities',('-8005077','-1485026')),
        (5,2,'Revenue (Note 13)','Revenue',(None,'250000')),
    ]
    facts=[]
    for table,row,label,tag,values in definitions:
        for i,value in enumerate(values):
            if value is None:continue  # Explicitly unqualified dash; never zero.
            year=2026-i;start=None if table==1 else f'{year}-01-01'
            end=('2026-03-31' if i==0 else '2025-12-31') if table==1 else f'{year}-03-31'
            unit='CAD/shares' if tag=='DilutedEarningsLossPerShare' else 'shares' if tag=='AdjustedWeightedAverageShares' else 'CAD'
            col=2+3*i
            facts.append(dict(concept='{https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full}'+tag,
                period_start=start,period_end=end,unit=unit,scale=0,decimals='2' if '.' in value else '0',
                expected_value=value,label=label,evidence=['BTQ TECHNOLOGIES CORP.',
                    'Three Months Ended March 31, 2026 and 2025','Expressed in Canadian dollars','International Accounting Standards Board'],
                table_proof=dict(heading=headings[table],headers=headers[table]),
                locator=dict(kind='cells',table=table,row=row,columns=[col,col+1] if value.startswith('-') else [col],label_column=0)))
    body=dict(version=VERSION,document_hash=document_hash,cik='0001821866',scope='reviewed_observations',facts=facts)
    return dict(body,plan_hash=digest(body))
