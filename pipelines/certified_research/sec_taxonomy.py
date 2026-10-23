"""Exact standard taxonomy identities; namespace aliases never change evidence."""
import re
from urllib.parse import urlsplit


def identity(concept,*,allow_extension=False):
    patterns = (
        ('us-gaap', r'http://fasb.org/us-gaap/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?'),
        ('dei', r'http://xbrl.sec.gov/dei/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?'),
        ('srt', r'http://fasb.org/srt/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?'),
        ('ecd', r'http://xbrl.sec.gov/ecd/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?'),
        ('ifrs-full', r'https?://xbrl.ifrs.org/taxonomy/[0-9]{4}-[0-9]{2}-[0-9]{2}/ifrs-full'),
    )
    for taxonomy, namespace in patterns:
        match = re.fullmatch(r'\{'+namespace+r'\}([A-Za-z_][A-Za-z0-9_]*)', concept)
        if match:
            return taxonomy, match[1]
    if allow_extension:
        match=re.fullmatch(r'\{(https?://[^\s{}]{1,500})\}([A-Za-z_][A-Za-z0-9_]*)',concept)
        if match:
            namespace,tag=match.groups();parsed=urlsplit(namespace)
            if (parsed.hostname and not parsed.username and not parsed.password
                    and parsed.hostname not in ('fasb.org','www.fasb.org','xbrl.sec.gov','xbrl.ifrs.org','www.xbrl.org','xbrl.org')):
                # This is an opaque original issuer concept, never an alias for
                # a standard metric with the same local name.
                return 'extension|'+namespace,tag
    raise ValueError('Unsupported extracted fundamental taxonomy')
