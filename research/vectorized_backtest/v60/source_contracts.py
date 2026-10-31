"""Producer-source identity compatibility; financial/data bytes remain exact."""
from hashlib import sha256
from pathlib import Path


def producer_hashes(path):
    """Only LF/CRLF variants of the same local mathematical source are accepted."""
    text=Path(path).read_bytes().replace(b'\r\n',b'\n')
    return frozenset((sha256(text).hexdigest(),sha256(text.replace(b'\n',b'\r\n')).hexdigest()))
