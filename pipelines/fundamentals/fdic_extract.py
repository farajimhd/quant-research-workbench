"""Selected bank statements from PDF geometry, with explicit row/period evidence.

No OCR guesses, inferred totals, currency conversion, or subsidiary aggregation.
Ambiguous headers and conflicting duplicate observations fail closed.
"""
from datetime import date
from decimal import Decimal
import calendar
import re

VERSION = 'fdic-selected-geometry-v1'
MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
LABELS = {
 'cash and cash equivalents': 'cash', 'total cash and cash equivalents': 'cash', 'total assets': 'assets',
 'total liabilities': 'liabilities',
 'total equity': 'bank_equity_consolidated',
 'noncontrolling interest': 'bank_noncontrolling_interest',
 "total stockholders' equity": 'equity_parent', "total shareholders' equity": 'equity_parent',
 'total deposits': 'bank_deposits', 'total borrowings': 'debt_total',
 'net interest income': 'bank_net_interest_income',
 'net interest income before provision for credit losses': 'bank_net_interest_income',
 'total noninterest income': 'bank_noninterest_income', 'total non-interest income': 'bank_noninterest_income',
 'net income': 'net_income_parent', 'net income available to common shareholders': 'net_income_common',
 'net income and comprehensive income': 'net_income_parent',
 'net income available to common stockholders': 'net_income_common',
 'net income attributable to townebank': 'net_income_parent',
 'earnings attributable to noncontrolling interest': 'bank_noncontrolling_income_as_presented',
 'net cash provided by operating activities': 'operating_cash_flow',
 'cash provided by operating activities': 'operating_cash_flow',
 'net cash provided by (used for) operating activities': 'operating_cash_flow',
 'net cash used for operating activities': 'operating_cash_flow',
 'net cash from operating activities': 'operating_cash_flow',
 'net cash (used for) from operating activities': 'operating_cash_flow',
 'net cash from (used for) operating activities': 'operating_cash_flow',
 'net cash provided by (used in) operating activities': 'operating_cash_flow',
 'net cash (used in) provided by operating activities': 'operating_cash_flow',
 'net cash used in operating activities': 'operating_cash_flow',
 'purchases of premises and equipment': 'capital_expenditure',
 'purchase of premises and equipment': 'capital_expenditure',
 'purchase of bank premises and equipment': 'capital_expenditure',
 'purchases of bank premises and equipment': 'capital_expenditure',
 'additions to premises and equipment': 'capital_expenditure',
 'capital expenditures': 'capital_expenditure',
}
INSTANT = {'cash', 'assets', 'liabilities', 'equity_parent', 'bank_deposits', 'debt_total',
           'bank_equity_consolidated','bank_noncontrolling_interest'}
NUMBER = re.compile(r'\(?-?\d[\d,]*(?:\.\d+)?\)?|[—–-]')


def lines(words):
    """Group a bounded statement page by baseline, retaining column positions."""
    rows = []
    for w in sorted(words, key=lambda x: (x[3], x[0])):
        if not w[4].strip(): continue
        if not rows or abs(w[3] - rows[-1][0][3]) > 2.5:
            rows.append([w])
        else: rows[-1].append(w)
    result=[]
    for row in rows:
        merged=[]
        for w in sorted(row,key=lambda x:x[0]):
            w=list(w[:5]);w[4]=re.sub('[\u200b\u200c\u200d\ufeff]','',w[4])
            if not w[4].strip():continue
            if merged and 0<=w[0]-merged[-1][2]<1.5 and merged[-1][4].isalpha() and w[4].isalpha():
                merged[-1][2]=w[2];merged[-1][4]+=w[4]
            else:merged.append(w)
        if merged:result.append(merged)
    return result


def clean(text):
    return ' '.join(text.replace('\u200b', '').replace('’', "'").replace('‐','-').split()).strip(':').lower()


def numeric_row(row):
    """Return a label and complete numeric tail; no extraction from prose."""
    first = next((i for i, w in enumerate(row) if w[4] == '$' or NUMBER.fullmatch(w[4])), None)
    if first is None or first == 0: return None
    label = clean(' '.join(w[4] for w in row[:first]))
    tokens = row[first:]
    values = []
    for w in tokens:
        token = w[4].replace('$', '')
        if not token: continue
        if not NUMBER.fullmatch(token): return None
        if token in ('—', '–', '-'):
            values.append(None)
        else:
            if token.startswith('(') != token.endswith(')'): return None
            values.append(Decimal(token.replace(',', '').replace('(', '-').replace(')', '')))
    return label, values


def header_periods(rows, first_y, instant, columns, cover_period=None):
    headers = [r for r in rows if r[0][1] < first_y]
    year_rows = []
    for row in headers:
        years = [w for w in row if re.fullmatch(r'20\d{2}', w[4])]
        if len(years) == columns: year_rows.append(years)
    if not year_rows: raise ValueError('No aligned year header')
    years = year_rows[-1]
    year_y = years[0][1]
    header = [r for r in headers if r[0][1] <= year_y + 2]
    dates, durations = [], []
    for row in header:
        for i, w in enumerate(row):
            if i+1<len(row) and w[4].lower() in MONTHS and re.fullmatch(r'\d{1,2},?', row[i+1][4]):
                dates.append(((w[0] + row[i+1][2])/2, w[1], MONTHS[w[4].lower()], int(row[i+1][4].strip(','))))
            joined=re.fullmatch(r'(Three|Six|Nine)MonthsEnded',w[4],re.I)
            if joined:
                durations.append(((w[0]+w[2])/2,w[1],{'three':3,'six':6,'nine':9}[joined[1].lower()]))
            if w[4].lower() in ('three', 'six', 'nine', 'year', 'years'):
                following = ' '.join(x[4].lower() for x in row[i:i+3])
                if 'ended' in following:
                    months = {'three':3,'six':6,'nine':9,'year':12,'years':12}[w[4].lower()]
                    durations.append(((w[0]+row[min(i+2,len(row)-1)][2])/2,w[1],months))
    if not dates:
        if not instant and cover_period:
            end=date.fromisoformat(cover_period)
            dates=[(0,0,end.month,end.day)]
        else:raise ValueError('No explicit period-end header')
    # Only the nearest date header row above years belongs to this table.
    dates = [d for d in dates if d[1] >= max(d[1] for d in dates)-2]
    if not instant:
        if not durations: raise ValueError('No explicit duration header')
        durations = [d for d in durations if d[1] >= max(d[1] for d in durations)-2]
    result = []
    for year in years:
        x=(year[0]+year[2])/2
        _, _, month, day = min(dates, key=lambda d:abs(d[0]-x))
        end = date(int(year[4]),month,day)
        if instant: start = None
        else:
            months=min(durations,key=lambda d:abs(d[0]-x))[2]
            if day != calendar.monthrange(end.year,month)[1] or month<months:
                raise ValueError('Unsupported non-calendar statement period')
            start=str(date(end.year,month-months+1,1))
        result.append((start,str(end)))
    return result


def extract_page(page):
    rows=lines(page['words'])
    title_rows=[r for r in rows if re.fullmatch(
        r'(?:condensed)?consolidated(?:balancesheets|statements?(?:of)?(?:financialcondition|income|netincome|operations|earnings|cashflows?)(?:andcomprehensiveincome)?|incomestatements?andcomprehensiveincome)(?:\((?:continued|concluded|unaudited)\))?',
        clean(' '.join(w[4] for w in r)).replace(' ',''))]
    if len(title_rows)!=1:return []
    title=clean(' '.join(w[4] for w in title_rows[0]))
    compact=title.replace(' ','')
    instant='balancesheets' in compact or 'financialcondition' in compact
    cashflow='cashflow' in compact
    candidates=[];section=''
    parent_label=any(any(s in clean(' '.join(w[4] for w in r)) for s in
        ('net income attributable to townebank','earnings attributable to noncontrolling interest')) for r in rows)
    equity_parent=any('equity before noncontrolling interest' in clean(' '.join(w[4] for w in r)) for r in rows)
    for row in rows:
        text=clean(' '.join(w[4] for w in row))
        if 'earningsper' in text.replace(' ','') or 'incomeper' in text.replace(' ',''):section='eps'
        elif 'weightedaverage' in text.replace(' ','').replace('-',''):section='shares'
        parsed=numeric_row(row)
        if not parsed:continue
        label,values=parsed;metric=next((v for k,v in LABELS.items() if k.replace(' ','')==label.replace(' ','')),None)
        if label=="total stockholders' equity before noncontrolling interest":metric='equity_parent'
        if equity_parent and label=="total stockholders' equity":metric='bank_equity_consolidated'
        if (page.get('pdf_sha256')=='50a73cd487ba1724730d8cb16b9ef4a7aa82056fba1875b1759859d08b833a46'
                and page['page']==3 and label=='total liabilities'):
            # Render-verified invisible PDF text artifact between the two cells.
            if values!=[Decimal(27862415),Decimal(2),Decimal(22965630)]:raise ValueError('Reviewed OZK row changed')
            values=[values[0],values[2]]
        if parent_label and label=='net income':metric='bank_net_income_consolidated'
        if label in ('diluted','diluted earnings','diluted earnings per share','diluted earnings per common share','earnings per common share - diluted'):
            metric='shares_diluted_weighted' if section=='shares' else 'eps_diluted' if section=='eps' or label!='diluted' else None
        if metric is None:continue
        if instant != (metric in INSTANT or metric=='bank_equity_consolidated'):continue
        if cashflow != (metric in ('operating_cash_flow','capital_expenditure')):continue
        candidates.append((row,label,metric,values))
    if not candidates:return []
    first_y=min(row[0][1] for row,_,_,_ in candidates)
    header=' '.join(w[4] for r in rows if r[0][1]<first_y for w in r).lower()
    if 'thousands' not in header:raise ValueError('Statement scale is not explicitly thousands')
    periods=header_periods(rows,first_y,instant,len(candidates[0][3]),page.get('cover_period'))
    result=[]
    for row,label,metric,values in candidates:
        if len(values)!=len(periods):raise ValueError('Numeric row/header column mismatch')
        unit='USD/shares' if metric=='eps_diluted' else 'shares' if metric=='shares_diluted_weighted' else 'USD'
        scale=Decimal(1) if metric=='eps_diluted' else Decimal(1000)
        if metric=='shares_diluted_weighted' and re.search(r'except share(?: and per share)? (?:data|amounts)',header):scale=Decimal(1)
        for value,(start,end) in zip(values,periods):
            if value is None:continue  # A dash is not invented zero.
            if metric=='capital_expenditure':
                value=-value
            result.append(dict(metric=metric,period_start=start,period_end=end,unit=unit,
                value=str(value*scale),page=page['page'],label=label,statement=title,
                row_evidence=' '.join(w[4] for w in row),period_evidence=periods,
                scale=str(scale)))
    return result


def extract_document(pages):
    observations=[];issues=[]
    for page in pages:
        try: observations.extend(extract_page(page))
        except ValueError as exc:issues.append(dict(page=page['page'],reason=str(exc)))
    keyed={}
    for row in observations:
        key=tuple(row[k] for k in ('metric','period_start','period_end','unit'))
        if key in keyed and keyed[key]['value']!=row['value']:
            issues.append(dict(page=row['page'],reason='Conflicting repeated statement value',key=key))
        else:keyed[key]=row
    values=list(keyed.values())
    for end in {o['period_end'] for o in values if o['period_start'] is None}:
        balance={o['metric']:Decimal(o['value']) for o in values if o['period_start'] is None and o['period_end']==end}
        if {'assets','liabilities','equity_parent'}<=balance.keys():
            equity=balance.get('bank_equity_consolidated',balance['equity_parent'])
            if balance['assets']!=balance['liabilities']+equity:
                issues.append(dict(reason='Balance sheet does not reconcile',period_end=end))
            if 'bank_noncontrolling_interest' in balance and 'bank_equity_consolidated' in balance:
                if equity!=balance['equity_parent']+balance['bank_noncontrolling_interest']:
                    issues.append(dict(reason='Parent and noncontrolling equity do not reconcile',period_end=end))
    return dict(version=VERSION,observations=values,issues=issues)


