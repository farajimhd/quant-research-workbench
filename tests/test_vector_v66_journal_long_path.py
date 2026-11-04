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
