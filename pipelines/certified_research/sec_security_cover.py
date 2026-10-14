"""Original SEC listed-security cover evidence; no issuer-class completeness claim."""
from collections import defaultdict
from . import sec_share_context
from .contracts import digest


def extract(payload,expected_hash,cik):
    parsed=sec_share_context.extract(payload,expected_hash,cik,include_security_cover=True)
    cover=parsed['security_cover'];groups=defaultdict(list)
    for row in cover['observations']:groups[(row['dimensions'],row['period_start'] or '',row['period_end'])].append(row)
    candidates=[]
    for (dimensions,period_start,period_end),rows in sorted(groups.items()):
        fields={name:tuple(sorted({r['value'] for r in rows if r['concept'].split('}')[-1]==name}))
            for name in ('Security12bTitle','TradingSymbol','SecurityExchangeName')}
        reason=('competing_cover_fields' if any(len(values)>1 for values in fields.values()) else
            'incomplete_cover_fields' if any(not values for values in fields.values()) else '')
        candidates.append(dict(dimensions=dimensions,period_start=period_start or None,period_end=period_end,
            fields=fields,status='candidate' if not reason else 'unresolved',reason=reason,
            positions=tuple(r['source_position'] for r in rows)))
    result=dict(version='sec-original-listed-security-cover-v1',source_text_hash=expected_hash,cik=str(int(cik)),
        observations=cover['observations'],rejected=cover['rejected'],candidates=tuple(candidates),
        original_vintage_qualified=False,class_completeness_proven=False,allocation_certified=False)
    return dict(result,cover_hash=digest(result))
