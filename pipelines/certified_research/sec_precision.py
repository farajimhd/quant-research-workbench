"""Exact bounded XBRL numeric interval assessment; never selects a reported value."""
from decimal import Decimal,InvalidOperation,localcontext
from fractions import Fraction
import re
from collections import defaultdict,Counter

from .contracts import digest


def assess(rows,*,mode):
    if mode not in ('round-to-nearest','truncation') or not isinstance(rows,(tuple,list)) or not 1<=len(rows)<=10000:
        raise ValueError('Explicit rounding mode and bounded numeric witnesses are required')
    intervals=[];seen=set()
    for row in rows:
        value=row['value'];decimals=row['decimals'];position=row['source_position']
        if type(position) is not int or position<0 or position in seen:raise ValueError('Invalid or duplicate precision source position')
        seen.add(position)
        if not isinstance(value,str) or not 1<=len(value)<=512:raise ValueError('Precision value exceeds decimal bound')
        try:number=Decimal(value)
        except InvalidOperation as error:raise ValueError('Invalid precision decimal value') from error
        if not number.is_finite() or abs(number.as_tuple().exponent)>128:raise ValueError('Nonfinite or excessive precision exponent')
        number=Fraction(number)
        if decimals=='INF':lower=upper=number;left=right=True
        else:
            if not isinstance(decimals,str) or not re.fullmatch('[+-]?[0-9]{1,3}',decimals) or abs(int(decimals))>128:
                raise ValueError('Missing or unsupported declared decimals')
            places=int(decimals);quantum=Fraction(1,10**places) if places>=0 else Fraction(10**(-places))
            if (number/quantum).denominator!=1:raise ValueError('Digits exceed declared precision')
            if mode=='round-to-nearest':lower=number-quantum/2;upper=number+quantum/2;left=right=True
            elif number>0:lower=number;upper=number+quantum;left=True;right=False
            elif number<0:lower=number-quantum;upper=number;left=False;right=True
            else:lower=-quantum;upper=quantum;left=right=False
        intervals.append((lower,upper,left,right))
    lower=max(r[0] for r in intervals);upper=min(r[1] for r in intervals)
    left=all(r[2] for r in intervals if r[0]==lower);right=all(r[3] for r in intervals if r[1]==upper)
    compatible=lower<upper or (lower==upper and left and right)
    def text(number):
        with localcontext() as context:
            context.prec=2048
            return format(Decimal(number.numerator)/Decimal(number.denominator),'f')
    result=dict(version='xbrl-numeric-interval-assessment-v1',mode=mode,source_positions=tuple(r['source_position'] for r in rows),
        witnesses_hash=digest(rows),status='compatible_intervals' if compatible else 'disjoint_intervals',
        intersection=dict(lower=text(lower),upper=text(upper),lower_inclusive=left,upper_inclusive=right) if compatible else None,
        certified=False,value_selected=False)
    return dict(result,assessment_hash=digest(result))


def inventory(extraction):
    """Assess every original semantic group; dimensional facts remain included."""
    body=dict(extraction);sealed=body.pop('extraction_hash',None)
    if sealed!=digest(body) or len(extraction['observations'])+len(extraction['rejected'])>10000:
        raise ValueError('Invalid bounded original precision extraction')
    grouped=defaultdict(list);positions=set()
    for row in extraction['observations']:
        position=row['source_position']
        if position in positions:raise ValueError('Original precision inventory has duplicate positions')
        positions.add(position)
        key=(row['concept'],row['unit'],row['period_start'],row['period_end'],tuple(tuple(v) for v in row['dimensions']))
        grouped[key].append(dict(source_position=position,value=row['value'],decimals=row['decimals']))
    groups=[];counts={mode:Counter() for mode in ('round-to-nearest','truncation')}
    for key,rows in grouped.items():
        modes={}
        for mode in counts:
            try:result=assess(tuple(rows),mode=mode)
            except ValueError as error:result=dict(status='unqualified_precision',reason=str(error),certified=False)
            modes[mode]=result;counts[mode][result['status']]+=1
        groups.append(dict(context_hash=digest(key),positions=tuple(r['source_position'] for r in rows),
            dimensioned=bool(key[-1]),observations=len(rows),modes=modes))
    groups.sort(key=lambda row:row['context_hash'])
    result=dict(version='original-sec-precision-inventory-v1',extraction_hash=sealed,groups=tuple(groups),
        observations=len(positions),semantic_groups=len(groups),duplicate_groups=sum(g['observations']>1 for g in groups),
        dimensioned_groups=sum(g['dimensioned'] for g in groups),rejected_numeric_facts=len(extraction['rejected']),
        mode_status_counts={mode:dict(sorted(values.items())) for mode,values in counts.items()},certified=False)
    # Explicit, empty xsi:nil is a retained missing-data declaration, never a
    # numeric value or an unresolved conversion. Older extraction contracts do
    # not validate nil content and cannot use this qualification rule.
    if extraction.get('version')=='sec-inline-fundamental-context-v4':
        nils=tuple(r['source_position'] for r in extraction['rejected'] if r['reason']=='nil_share_fact')
        if nils:
            result.update(version='original-sec-precision-inventory-v2',declared_nil_positions=nils,
                rejected_numeric_facts=len(extraction['rejected'])-len(nils))
    return dict(result,inventory_hash=digest(result))


def gate(extraction,*,mode):
    """One report-wide mode and equal-accuracy agreement; not full XBRL certification."""
    if mode not in ('round-to-nearest','truncation'):raise ValueError('A single explicit report-wide precision mode is required')
    return _gate(extraction,inventory(extraction),mode)


def report(extraction,*,include_projections=False):
    """Share one inventory computation across both diagnostic mode gates."""
    observed=inventory(extraction)
    result=dict(inventory=observed,gates={mode:_gate(extraction,observed,mode) for mode in ('round-to-nearest','truncation')})
    if include_projections:
        result['projections']={mode:_select_reported(extraction,mode,result) if value['ready'] else None
            for mode,value in result['gates'].items()}
    return result


def _gate(extraction,observed,mode):
    by_position={r['source_position']:r for r in extraction['observations']}
    failures=[]
    for group in observed['groups']:
        status=group['modes'][mode]['status']
        reason='' if status=='compatible_intervals' else status
        if not reason:
            accuracies=defaultdict(set)
            for position in group['positions']:
                row=by_position[position];accuracy='INF' if row['decimals']=='INF' else int(row['decimals'])
                accuracies[accuracy].add(Decimal(row['value']))
            if any(len(values)>1 for values in accuracies.values()):reason='disagreeing_equal_accuracy_values'
        if reason:failures.append(dict(context_hash=group['context_hash'],positions=group['positions'],reason=reason))
    result=dict(version='original-sec-report-precision-gate-v1',mode=mode,extraction_hash=extraction['extraction_hash'],
        inventory_hash=observed['inventory_hash'],observations=observed['observations'],semantic_groups=observed['semantic_groups'],
        failures=tuple(failures),rejected_numeric_facts=observed['rejected_numeric_facts'],
        ready=bool(observed['observations']) and not failures and not observed['rejected_numeric_facts'],
        certified=False,value_selected=False)
    return dict(result,gate_hash=digest(result))


def select_reported(extraction,*,mode):
    """Choose the most accurate actual report value; retain every source locator."""
    if mode not in ('round-to-nearest','truncation'):raise ValueError('Explicit reported-value mode is required')
    return _select_reported(extraction,mode,report(extraction))


def _select_reported(extraction,mode,precision_report):
    verified=precision_report['gates'][mode]
    if not verified['ready']:raise ValueError('Original report precision gate failed; no values selected')
    grouped=defaultdict(list)
    for row in extraction['observations']:
        key=(row['concept'],row['unit'],row['period_start'],row['period_end'],tuple(tuple(v) for v in row['dimensions']))
        grouped[key].append(row)
    selected=[]
    intervals={group['context_hash']:group['modes'][mode]['intersection'] for group in precision_report['inventory']['groups']}
    for key,rows in grouped.items():
        def accuracy(row):return (row['decimals']=='INF',0 if row['decimals']=='INF' else int(row['decimals']))
        highest=max(accuracy(row) for row in rows)
        best=[row for row in rows if accuracy(row)==highest]
        winner=min(best,key=lambda row:row['source_position'])
        interval=intervals[digest(key)]
        value=Decimal(winner['value']);lower=Decimal(interval['lower']);upper=Decimal(interval['upper'])
        if not (lower<=value<=upper) or (value==lower and not interval['lower_inclusive']) or (value==upper and not interval['upper_inclusive']):
            raise ValueError('Most accurate reported value is outside the validated common interval')
        selected.append(dict(context_hash=digest(key),concept=key[0],unit=key[1],period_start=key[2],period_end=key[3],dimensions=key[4],
            value=winner['value'],decimals=winner['decimals'],source_position=winner['source_position'],
            all_source_positions=tuple(sorted(r['source_position'] for r in rows)),interval=interval))
    selected.sort(key=lambda row:row['context_hash'])
    result=dict(version='original-sec-most-accurate-reported-values-v1',mode=mode,extraction_hash=extraction['extraction_hash'],
        gate_hash=verified['gate_hash'],groups=tuple(selected),observations=len(extraction['observations']),
        inferred_values=False,certified=False)
    return dict(result,projection_hash=digest(result))
