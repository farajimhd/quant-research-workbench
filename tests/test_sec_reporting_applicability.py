from hashlib import sha256

import pytest

from pipelines.certified_research.sec_reporting_applicability import review


def result(text):
    source = '<html><body>' + text + '</body></html>'
    return review(source, sha256(source.encode()).hexdigest())


@pytest.mark.parametrize('punctuation', ['.', ' .', ''])
@pytest.mark.parametrize('disposition', ['Not Applicable', 'None'])
def test_explicit_item8_disposition_is_filing_scoped(punctuation, disposition):
    out = result(f'Item 8{punctuation} Financial Statements and Supplementary Data{punctuation} '
                 f'{disposition}. Item 9. Changes')
    assert out['status'] == 'corporate_statements_not_provided'
    assert out['scope'] == 'this_filing_only'


def test_instruction_j_must_explicitly_include_item8():
    prefix = 'PART II The following Items have been omitted in accordance with General Instruction J to Form 10-K: '
    item8 = 'Item 8. Financial Statements and Supplementary Data. Item 9. Changes. '
    assert result(prefix + item8 + 'PART III Other')['status'] == 'corporate_statements_not_provided'
    assert result(prefix + 'Item 7. Discussion. PART III ' + item8)['status'] == 'unresolved'
    assert result(item8 + 'PART III Other')['status'] == 'unresolved'


def test_generic_none_or_missing_table_is_not_an_exemption():
    assert result('Financial Statements None. Item 8. See appendix')['status'] == 'unresolved'
    assert result('Item 8. Financial Statements and Supplementary Data. Balance sheet')['status'] == 'unresolved'
    with pytest.raises(ValueError, match='hash'):
        review('<html>changed</html>', '0' * 64)
