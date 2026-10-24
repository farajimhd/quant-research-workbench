"""Exact retained IFRS registration exhibits: selected actual consolidated facts.

No currency conversion, pro-forma values, missing-marker zeros or subsidiary
attribution. Coordinates, headers, statement subjects, values and bytes are pinned.
"""
from decimal import Decimal
from .contracts import digest
from .sec_html_context import VERSION

OGC='6c9512490513186f7bd7f6e12bd409a6dd1f51a6218d68f288c64617805ae3d1'
NUR='c1a233ae3fbdf7c93483fa0b798a5c593ea440893749c6145e19f0a791575865'
AIBZ='e558f8f3c817c3d0e8f7cc7cf737c872c7cf6070f973409d97271f2eca8c6e10'


def plan_for(document_hash,cik):
    identity={OGC:'0001487326',NUR:'0001680637',AIBZ:'0002100457'}
    if identity.get(document_hash)!=str(int(cik)).zfill(10):return None
    if document_hash==OGC:
        issuer='OceanaGold Corporation';currency='USD';scale=6
        evidence=['in millions of United States dollars','International Financial Reporting Standards']
        periods={0:((None,'2025-12-31'),(None,'2024-12-31')),3:(('2025-01-01','2025-12-31'),('2024-01-01','2024-12-31')),7:(('2025-01-01','2025-12-31'),('2024-01-01','2024-12-31'))}
        headings={0:'Consolidated Statements of Financial Position As at December 31',3:'Consolidated Statements of Income For the years ended December 31',7:'Consolidated Statements of Cash Flows For the years ended December 31'}
        headers={t:[(1,['','Notes','2025','2024'])] for t in periods}
        rows=[
            (0,4,'Cash and cash equivalents','CashAndCashEquivalents',('476.5','193.5'),(3,6)),
            (0,8,'Total current assets','CurrentAssets',('731.8','465.3'),(2,4)),
            (0,16,'TOTAL ASSETS','Assets',('3255.4','2489.1'),(3,6)),
            (0,26,'Total current liabilities','CurrentLiabilities',('505.7','308.8'),(2,4)),
            (0,34,'TOTAL LIABILITIES','Liabilities',('884.2','562.6'),(2,4)),
            (3,3,'Revenue','Revenue',('1893.2','1294.0'),(3,6)),
            (3,10,'Operating profit','ProfitLossFromOperatingActivities',('740.3','274.4'),(2,4)),
            (3,23,'Net profit','ProfitLoss',('645.7','192.0'),(3,6)),
            (7,14,'Net cash provided by operating activities','CashFlowsFromUsedInOperatingActivities',('984.2','593.9'),(2,4)),
        ];split_negative=False
    elif document_hash==NUR:
        issuer='Nuran Wireless Inc.';currency='CAD';scale=0
        evidence=['Expressed in Canadian dollars','International Financial Reporting Standards']
        periods={1:((None,'2026-03-31'),(None,'2025-12-31')),2:(('2026-01-01','2026-03-31'),('2025-01-01','2025-03-31')),5:(('2026-01-01','2026-03-31'),('2025-01-01','2025-03-31'))}
        headings={1:'Condensed Interim Consolidated Statements of Financial Position',2:'Condensed Interim Consolidated Statements of net Loss and Comprehensive Loss',5:'Amended Condensed Interim Consolidated Statements of Cash Flows'}
        headers={1:[(0,['','','2026-03-31','','','2025-12-31',''])],2:[(0,['','','Three months ended','']),(1,['','','','2026-03-31','','','','2025-03-31',''])],5:[(0,['','','2026-03-31','','','2025-03-31','']),(1,['','','(3 months)','','','(3 months)',''])]}
        rows=[
            (1,8,'Inventories (Note 4)','Inventories',('6031752','6389739'),(3,7)),
            (1,12,'Current assets','CurrentAssets',('16378312','19077136'),(3,7)),
            (1,18,'Total assets','Assets',('27908407','29998169'),(3,7)),
            (1,28,'Current liabilities','CurrentLiabilities',('24260508','24078577'),(3,7)),
            (1,31,'Total liabilities','Liabilities',('24583198','24401783'),(3,7)),
            (1,39,'Total shareholders’ equity','Equity',('3325209','5596386'),(3,7)),
            (2,3,'Revenue','Revenue',('871066','2209079'),(3,7)),
            (2,5,'Gross profit (loss)','GrossProfit',('630417','1898380'),(3,7)),
            (2,19,'Net loss for the period','ProfitLoss',('-2033140','-1689530'),(3,7)),
            (5,27,'Net cash from operating activities','CashFlowsFromUsedInOperatingActivities',('-2180259','-1650940'),(3,7)),
        ];split_negative=True
    else:
        issuer='BITZERO HOLDINGS INC.';currency='USD';scale=0
        evidence=['Expressed in United States Dollars','International Financial Reporting Standards']
        periods={6:((None,'2026-03-31'),(None,'2025-09-30')),13:(('2025-10-01','2026-03-31'),('2024-10-01','2025-03-31'))}
        headings={6:'As at March 31, 2026 and September 30, 2025',13:'Interim condensed consolidated statement of cash flows'}
        headers={6:[(0,['','','','','March 31,','','September 30,']),(1,['','','Note','','2026','','2025'])],13:[(0,['','','For the 6 months','','For the 6 months']),(1,['','','ended','','ended']),(2,['','','31-Mar-26','','31-Mar-25'])]}
        rows=[
            (6,18,'Cash and cash equivalents','CashAndCashEquivalents',('832680','2501986'),(5,9)),
            (6,21,'TOTAL ASSETS','Assets',('55835949','42740718'),(5,9)),
            (6,30,'Total equity','Equity',('24300243','12850711'),(5,9)),
            (6,48,'Total liabilities','Liabilities',('31535706','29890007'),(5,9)),
            (13,6,'Net loss for the period','ProfitLoss',('-12502299','-6961155'),(3,7)),
            (13,28,'','CashFlowsFromUsedInOperatingActivities',('-8225035','-12102491'),(3,7)),
        ];split_negative=True
    selected=[]
    for table,row,label,tag,values,columns in rows:
        for i,value in enumerate(values):
            number=Decimal(value);start,end=periods[table][i];column=columns[i]
            selected.append(dict(concept='{https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full}'+tag,
                period_start=start,period_end=end,unit=currency,scale=scale,label=label,
                expected_value=format(number.scaleb(scale),'f'),decimals=str(-number.as_tuple().exponent-scale),
                evidence=[issuer,*evidence],table_proof=dict(heading=headings[table],headers=[[index,cells] for index,cells in headers[table]]),
                locator=dict(kind='cells',table=table,row=row,columns=[column,column+1] if split_negative and value.startswith('-') else [column],label_column=0)))
    body=dict(version=VERSION,document_hash=document_hash,cik=identity[document_hash],scope='reviewed_observations',facts=selected)
    return dict(body,plan_hash=digest(body))
