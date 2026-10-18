"""Exact original enriched certificate verification across Python versions."""
import json
from pathlib import Path
from research.vectorized_backtest.v27 import prepare_enriched as producer
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from .certificates import extractor_hash

def verify(folder,input_hash,role):
    c=json.loads((folder/'complete.json').read_bytes());expected=producer.definition()
    expected['extractor_sha256']=extractor_hash(producer.__file__)
    if c['status']!='complete' or c['definition']!=expected or c['input_sha256']!=input_hash or c['validation_opened']!=(role=='validation'):
        raise ValueError('Original enriched certificate identity changed')
    for name,h in c['files'].items():
        if Path(name).name!=name or file_hash(folder/name)!=h:raise ValueError('Enriched certified file changed')
    return c
