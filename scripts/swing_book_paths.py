"""Explicit operational destinations for historical swing-book builders."""
from pathlib import Path, PureWindowsPath
import re

from src.runtime_paths import LAPTOP_RUNTIME_ROOT, WORKSTATION_RUNTIME_ROOT

WORKSTATION_ENV_FILE = WORKSTATION_RUNTIME_ROOT.parent / 'secrets' / '.env'


def ticker_directory(ticker: str, *, lowercase=False) -> str:
    """Keep existing safe paths; encode unsafe Windows names without collisions."""
    if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,19}', ticker):
        raise ValueError('Unsupported canonical ticker syntax')
    if PureWindowsPath(ticker).is_reserved() or ticker.endswith('.'):
        return '_ticker_' + ticker.encode('ascii').hex()
    return ticker.lower() if lowercase else ticker


def validate_runtime_root(destination: Path) -> Path:
    """Accept only documented roots; never redirect an unavailable destination."""
    resolved = destination.resolve()
    for authority in (WORKSTATION_RUNTIME_ROOT, LAPTOP_RUNTIME_ROOT):
        if resolved.is_relative_to(authority.resolve()):
            if not authority.is_dir():
                raise ValueError(f'Required runtime root unavailable: {authority}')
            return resolved
    raise ValueError(
        f'Runtime must be under {WORKSTATION_RUNTIME_ROOT} or {LAPTOP_RUNTIME_ROOT}'
    )
