"""Original-document catalog observations with exact decimals and explicit contexts."""
from . import facts,sec_share_context
from .contracts import digest


def extract(payload,expected_hash,cik,*,maximum_bytes=16*1024**2,maximum_facts=10000,all_numeric=False):
    from . import sec_html_reviewed,sec_html_context
    plan=sec_html_reviewed.plan_for(expected_hash,cik)
    if plan is not None:
        if all_numeric:
            raise ValueError('Reviewed HTML observations do not establish complete numeric filing coverage')
        return sec_html_context.extract(payload,expected_hash,cik,plan,maximum_bytes=maximum_bytes)
    metrics=facts.catalog()
    tags=tuple(sorted({tag for metric in metrics for tag in metric.tags}))
    result=sec_share_context.extract(payload,expected_hash,cik,maximum_bytes=maximum_bytes,
        maximum_facts=maximum_facts,catalog_tags=tags,include_security_cover=True,all_numeric=all_numeric)
    result.update(catalog_hash=facts.catalog_hash(metrics),original_vintage_qualified=False,certified=False)
    return dict(result,extraction_hash=digest(result))
