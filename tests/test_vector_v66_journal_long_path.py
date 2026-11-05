import json
import numpy as np
import polars as pl
from research.vectorized_backtest.v66.order_journal import Journal,windows_path


def test_long_path_journal_page_and_receipt(tmp_path):
    root=tmp_path/('a'*70)/('b'*70)/('c'*70)
    journal=Journal(root,1,{'scope':'training'})
    values=np.array([[1,1,5,5,50,0.1,1,2,3]],dtype=float)
    journal.submit(np.array([0]),np.array([1]),values)
    receipts=journal.finish()
    receipt=json.loads(windows_path(receipts[0]['receipt']).read_bytes())
    assert receipt['counts']==[1]
    page=windows_path(journal.root/receipt['pages'][0]['file'])
    assert pl.read_parquet(page)['filled_quantity'].to_list()==[5.]


def test_pages_coalesce_without_changing_candidate_hash_or_order(tmp_path):
    from hashlib import sha256
    journal=Journal(tmp_path,2,{'scope':'training'})
    chunks=[]
    for clock in range(40):
        values=np.array([[1,1,5,5,50,0.1,clock,clock+1,clock+2]],dtype=float)
        chunks.append(values)
        journal.submit(np.array([clock%2]),np.array([1]),values)
    refs=journal.finish()
    receipt=json.loads(windows_path(refs[0]['receipt']).read_bytes())
    assert len(receipt['pages'])==1 and receipt['counts']==[20,20]
    frame=pl.read_parquet(windows_path(journal.root/receipt['pages'][0]['file']))
    assert frame['submitted_clock'].to_list()==list(range(40))
    for candidate in range(2):
        expected=sha256(b''.join(v.tobytes(order='C') for v in chunks[candidate::2])).hexdigest()
        assert refs[candidate]['sha256']==expected
