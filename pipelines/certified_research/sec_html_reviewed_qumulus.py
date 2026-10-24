"""QumulusAI actual consolidated statements, exact retained June 2026 filing."""
from .contracts import digest
from .sec_html_context import VERSION

HASH='f5aaa45db640281de4fe417c4081f07087309e1cee393215f9cc3d6aeea216ca'


def plan_for(document_hash,cik):
    if document_hash!=HASH or str(int(cik)).zfill(10)!='0002084026':return None
    z='\u200b'
    headers={4:[[0,[z,z,'June 30, 2026',z,z,'December 31, 2025',z]]],
        5:[[0,[z,z,'Three Months Ended June 30,',z,z,'Six Months Ended June 30,',z]],
           [1,[z,z,'2026',z,z,'2025',z,z,'2026',z,z,'2025',z]]],
        7:[[0,[z,z,'For the Six Months Ended June 30,',z]],[1,[z,z,'2026',z,z,'2025',z]]]}
    headings={4:'QUMULUSAI, INC. AND SUBSIDIARIES CONDENSED CONSOLIDATED BALANCE SHEET',
        5:'QUMULUSAI, INC. (FORMERLY GLOBAL DIGITAL HOLDINGS, INC.) AND SUBSIDIARIES CONDENSED CONSOLIDATED STATEMENTS OF OPERATIONS (Unaudited)',
        7:'QUMULUSAI, INC. AND SUBSIDIARIES CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS (Unaudited)'}
    definitions=[
        (4,8,'Total current assets','AssetsCurrent',('53204470','12905233')),
        (4,18,'Total assets','Assets',('215002216','91734493')),
        (4,32,'Total current liabilities','LiabilitiesCurrent',('66507183','12140082')),
        (4,41,'Total liabilities','Liabilities',('215783480','26442362')),
        (4,49,'Total shareholders’ equity (deficit) attributable to QumulusAI shareholders','StockholdersEquity',('-3567675','62172216')),
        (5,6,'Total revenue','Revenues',('6712805','3085439','10132723','4956691')),
        (5,15,'Operating loss','OperatingIncomeLoss',('-7670591','-2181533','-13197322','-3110694')),
        (5,43,'Net income (loss) per share, diluted','EarningsPerShareDiluted',('-0.72','0.46','-2.28','0.43')),
        (5,46,'Weighted-average common stock outstanding, diluted','WeightedAverageNumberOfDilutedSharesOutstanding',('31740634','26486792','31680098','24239377')),
        (7,41,'Net cash provided by (used in) operating activities','NetCashProvidedByUsedInOperatingActivities',('22305214','-795882')),
    ]
    facts=[]
    for table,row,label,tag,values in definitions:
        for i,value in enumerate(values):
            year=2026 if i%2==0 else 2025
            start=None if table==4 else f'{year}-'+('04-01' if table==5 and i<2 else '01-01')
            end=('2026-06-30' if i==0 else '2025-12-31') if table==4 else f'{year}-06-30'
            unit='USD/shares' if tag=='EarningsPerShareDiluted' else 'shares' if tag=='WeightedAverageNumberOfDilutedSharesOutstanding' else 'USD'
            col=3+4*i
            facts.append(dict(concept='{http://fasb.org/us-gaap/2026}'+tag,period_start=start,period_end=end,
                unit=unit,scale=0,decimals='2' if '.' in value else '0',expected_value=value,label=label,
                evidence=['QUMULUSAI, INC.','presented in U.S. dollars','accounting principles generally accepted in the United States of America'],
                table_proof=dict(heading=headings[table],headers=headers[table]),
                locator=dict(kind='cells',table=table,row=row,columns=[col,col+1] if value.startswith('-') else [col],label_column=0)))
    body=dict(version=VERSION,document_hash=document_hash,cik='0002084026',scope='reviewed_observations',facts=facts)
    return dict(body,plan_hash=digest(body))
