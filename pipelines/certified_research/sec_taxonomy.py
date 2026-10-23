"""Exact standard taxonomy identities; namespace aliases never change evidence."""
import re


def identity(concept):
    patterns = (
        ('us-gaap', r'http://fasb.org/us-gaap/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?'),
        ('dei', r'http://xbrl.sec.gov/dei/[0-9]{4}(?:-[0-9]{2}-[0-9]{2})?'),
        ('ifrs-full', r'https?://xbrl.ifrs.org/taxonomy/[0-9]{4}-[0-9]{2}-[0-9]{2}/ifrs-full'),
    )
    for taxonomy, namespace in patterns:
        match = re.fullmatch(r'\{'+namespace+r'\}([A-Za-z_][A-Za-z0-9_]*)', concept)
        if match:
            return taxonomy, match[1]
    raise ValueError('Unsupported extracted fundamental taxonomy')
