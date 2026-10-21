"""Reproduce the producer's Python 3.13 AST dump on Python 3.12 exactly."""
import ast
from hashlib import sha256
from pathlib import Path

def dump313(node):
    if isinstance(node,ast.AST):
        values=[]
        for name,value in ast.iter_fields(node):
            # Python 3.13 ast.dump(show_empty=False) omits optional None and []
            # except the mandatory empty positional argument sequence.
            if value is None or value==[]:continue
            values.append(name+'='+dump313(value))
        return type(node).__name__+'('+', '.join(values)+')'
    if isinstance(node,list):return '['+', '.join(map(dump313,node))+']'
    return repr(node)

def extractor_hash(path):
    names={'nan_scratch','release_scratch','requested_rows','prepare_day','snapshot_scope'}
    nodes=[n for n in ast.parse(Path(path).read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name in names]
    if {n.name for n in nodes} not in (names,names-{'snapshot_scope'}):raise ValueError('Incomplete certified producer')
    return sha256(dump313(ast.Module(body=nodes,type_ignores=[])).encode()).hexdigest()
