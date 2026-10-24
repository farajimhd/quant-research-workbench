"""Selected v1 gap mappings reviewed against exact retained financial statements."""
from decimal import Decimal
from .contracts import digest
from .sec_html_context import VERSION

ADRX_HASH='35240239f807dbf3bd6470d272f165abc78c7303919d6781d3c290a1cdde3801'


def plan_for(document_hash,cik):
    if document_hash!=ADRX_HASH or str(int(cik)).zfill(10)!='0001802369':return None
    # Actual consolidated statements only. Do not select offering pro-forma
    # columns, duplicate summary tables, or infer zero from missing markers.
    rows=(
        (663,8,'Cash and cash equivalents','CashAndCashEquivalentsAtCarryingValue',('66517','14034')),
        (663,12,'Total current assets','AssetsCurrent',('493375','225667')),
        (663,17,'Total assets','Assets',('507655','233454')),
        (663,28,'Total current liabilities','LiabilitiesCurrent',('30485','8937')),
        (663,33,'Total liabilities','Liabilities',('352288','12977')),
        (701,8,'Cash and cash equivalents','CashAndCashEquivalentsAtCarryingValue',('58484',)),
        (701,12,'Total current assets','AssetsCurrent',('441543',)),
        (701,17,'Total assets','Assets',('456332',)),
        (701,28,'Total current liabilities','LiabilitiesCurrent',('34013',)),
        (701,33,'Total liabilities','Liabilities',('349762',)),
        (664,9,'Research and development','ResearchAndDevelopmentExpense',('71512','43357')),
        (664,14,'Loss from operations','OperatingIncomeLoss',('-88365','-58233')),
        (664,22,'Net loss','NetIncomeLoss',('-73088','-46360')),
        (702,9,'Research and development','ResearchAndDevelopmentExpense',('48319','29965')),
        (702,14,'Loss from operations','OperatingIncomeLoss',('-56947','-39010')),
        (702,22,'Net loss','NetIncomeLoss',('-48426','-33644')),
        (666,21,'Net cash provided by (used in) operating activities','NetCashProvidedByUsedInOperatingActivities',('262288','-48563')),
        (705,21,'Net cash (used in) provided by operating activities','NetCashProvidedByUsedInOperatingActivities',('-49871','300739')),
    )
    headings={663:'Consolidated balance sheets',701:'Condensed consolidated balance sheets',
        664:'Consolidated statements of operations and comprehensive loss',702:'Condensed consolidated statements of operations and comprehensive loss',
        666:'Consolidated statements of cash flows',705:'Condensed consolidated statements of cash flows'}
    selected=[]
    for table,row,label,tag,values in rows:
        annual=table in (663,664,666);balance=table in (663,701)
        scale_label='(in thousands)' if table in (666,705) else '(in thousands, except share and per share data)'
        if annual:
            headers=[(2,['','','December 31,' if balance else 'Year ended December 31,','']),
                (3,[scale_label,'','2025','','','2024',''])]
        elif balance:
            headers=[(2,[scale_label,'','June 30, 2026','','','December 31, 2025','']),
                (3,['','','(unaudited)','','','',''])]
        else:
            headers=[(2,[scale_label,'','Six months ended June 30,','']),
                (3,['(unaudited)','','2026','','','2025',''])]
        for offset,value in enumerate(values):
            year=(2025-offset) if annual else (2026-offset)
            end=f'{year}-12-31' if annual else f'{year}-06-30'
            start=None if balance else f'{year}-01-01'
            column=3+4*offset
            columns=[column,column+1] if value.startswith('-') else [column]
            selected.append(dict(concept='{http://fasb.org/us-gaap/2026}'+tag,period_start=start,period_end=end,
                unit='USD',scale=3,label=label,expected_value=str(Decimal(value)*1000),decimals='-3',
                evidence=['ADARx Pharmaceuticals, Inc.',headings[table],
                    'consolidated financial statements are presented in U.S. dollars',
                    scale_label],
                table_proof=dict(heading=headings[table],headers=headers),
                locator=dict(kind='cells',table=table,row=row,columns=columns,label_column=0)))
    body=dict(version=VERSION,document_hash=document_hash,cik='0001802369',scope='reviewed_observations',facts=selected)
    return dict(body,plan_hash=digest(body))
