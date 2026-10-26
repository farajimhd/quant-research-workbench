"""Coalesced durable commit notifications; gateways never run a backfill inline."""
from datetime import datetime, timezone
from pathlib import Path
import json, os, platform, threading, uuid

_lock=threading.Lock()


def root():
    default=(r'D:/TradingML/runtimes/fundamentals-forward-gateway-v1' if platform.node().upper()=='DESKTOP-SAAI85T'
             else r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes\fundamentals-forward-gateway-v1')
    return Path(os.environ.get('FUNDAMENTALS_HANDOFF_ROOT',default))


def notify(source, *, detail='', directory=None):
    if source not in ('sec','reference'):
        raise ValueError('Unknown source commit notification')
    directory=root() if directory is None else Path(directory)
    # No implicitly enabled writer. A reviewed controller request must exist.
    if not (directory/'controller-request.json').is_file():
        return False
    directory.mkdir(parents=True,exist_ok=True)
    body=dict(source=source,generation=uuid.uuid4().hex,committed_at=datetime.now(timezone.utc).isoformat(),detail=detail)
    with _lock:
        target=directory/(source+'-commit.json');temporary=target.with_suffix('.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(body,sort_keys=True),encoding='utf-8');temporary.replace(target)
    return True
