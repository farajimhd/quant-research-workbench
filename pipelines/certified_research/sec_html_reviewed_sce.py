"""Exact 2018 trust income table in the retained 2019 joint annual filing."""
from .contracts import digest
from .sec_html_native import definition

DOCUMENT = 'aa26cc5be9a3cba7bc6e1cbe7d7828f37183f1998d0c071cf43a9b4b7f7fa12f'
CIK = '0000827052'
SUBJECTS = {'Trust II':'0001122392', 'Trust VI':'0001709317'}


def plan_for(document_hash,cik):
    if document_hash != DOCUMENT or str(int(cik)).zfill(10) != CIK:return None
    facts=[]
    headers=[['(in millions)','Trust I','','Trust II','','Trust III','','Trust IV','','Trust V','','Trust VI']]
    # Physical cells reviewed separately for each row; no offset inference.
    for scope, year, income_row, income_col, distribution_row, distribution_col, amount in (
        ('Trust II',2018,5,5,6,4,'20'),('Trust II',2017,8,6,9,4,'20'),
        ('Trust II',2016,11,6,12,4,'20'),('Trust VI',2018,5,21,6,16,'24'),
        ('Trust VI',2017,8,22,9,16,'12')):
        for label,row,col in [('Dividend income',income_row,income_col),('Dividend distributions',distribution_row,distribution_col)]:
            native=definition(CIK,label,scope)
            facts.append(dict(concept=native['concept'],native=native,period_start=f'{year}-01-01',
                period_end=f'{year}-12-31',unit='source:$',scale=6,decimals='-6',
                expected_value=str(int(amount)*1000000),label=label,
                evidence=[scope,'Years ended December 31,','(in millions)',str(year)],
                table_proof=dict(heading=scope,headers=[[3,headers[0]],[income_row-1,[str(year)]+['']*11]]),
                locator=dict(kind='cells',table=191,row=row,label_column=0,columns=[col]),
                unit_locator=dict(table=191,row=5,column=4)))
    body=dict(version='sec-reviewed-html-context-v1',document_hash=DOCUMENT,cik=CIK,
              scope='reviewed_observations',facts=facts)
    return dict(body,plan_hash=digest(body))
