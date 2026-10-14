from copy import deepcopy
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import sec_fundamental_publication as publication,sec_fundamental_readback as readback
from tests.test_certified_sec_fundamental_publication import source


@pytest.mark.parametrize('index,field,value',[(0,'context_hash','a'*64),(1,'value','999'),
    (2,'status','value_mismatch'),(3,'available_at','2025-11-01 05:00:00.000000')])
def test_mutated_normalized_rows_cannot_reconstruct_seals(index,field,value):
    groups=deepcopy(publication.records(*source()));key=groups[-1][0]['reconciliation_hash']
    groups[index][0][field]=value
    catalog=publication.tables()
    def query(sql):
        table=sql.split(' FROM ')[1].split()[0].split('.')[1]
        if sql.startswith('SELECT count()'):return [dict(n=1,bytes=100)]
        return list(groups[next(i for i,t in enumerate(catalog) if t.name==table)])
    with patch.object(readback.sec_share_capture,'verify'),pytest.raises(ValueError):
        readback.verify(query,'q_live',key,Mock(),Mock())
