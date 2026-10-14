"""Original-document catalog observations with exact decimals and explicit contexts."""
from . import facts,sec_share_context
from .contracts import digest


def extract(payload,expected_hash,cik,*,maximum_bytes=16*1024**2,maximum_facts=10000):
    metrics=facts.catalog()
    tags=tuple(sorted({tag for metric in metrics for tag in metric.tags}))
    result=sec_share_context.extract(payload,expected_hash,cik,maximum_bytes=maximum_bytes,
        maximum_facts=maximum_facts,catalog_tags=tags,include_security_cover=True)
    result.update(catalog_hash=facts.catalog_hash(metrics),original_vintage_qualified=False,certified=False)
    return dict(result,extraction_hash=digest(result))
