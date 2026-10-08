"""Explicitly inherit profiling only when the execution implementation is identical."""
from hashlib import sha256
from pathlib import Path
import json
ALLOWED={'stability.py','staged.py','staged_search.py','staged_audit.py','staged_validation.py','staged_dashboard.py','staged_observe.py','inactivity.py','qualification_reuse.py'}
def source_hash(root):
    h=sha256()
    for path in sorted(root.rglob('*.py')):h.update(path.relative_to(root).as_posix().encode());h.update(path.read_bytes())
    return h.hexdigest()
def verify(old,current,qualification):
    old,current=Path(old),Path(current)
    if source_hash(old)!=qualification['code_hash']:raise ValueError('Original qualified source changed')
    report=Path(qualification['report'])
    if sha256(report.read_bytes()).hexdigest()!=qualification['report_sha256']:raise ValueError('Qualification report changed')
    if json.loads(report.read_text())['status']!='passed':raise ValueError('Qualification did not pass')
    files=set(x.relative_to(old).as_posix() for x in old.rglob('*.py'))|set(x.relative_to(current).as_posix() for x in current.rglob('*.py'))
    verified=[]
    for name in sorted(files):
        if name.startswith('tests/') or name in ALLOWED:continue
        if not (old/name).is_file() or not (current/name).is_file() or (old/name).read_bytes()!=(current/name).read_bytes():
            raise ValueError('Qualified execution changed: '+name)
        verified.append(name)
    if not verified:raise ValueError('No execution source verified')
    return dict(original_code_hash=qualification['code_hash'],report_sha256=qualification['report_sha256'],unchanged_execution_files=verified,profiling_repeated=False)
