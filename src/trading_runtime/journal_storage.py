"""Versioned lossless storage for large backtest JSON; logical hashes stay raw."""
import base64
import hashlib
import json
import zlib

MARKER='$journal_storage_v1'
PREFIX='{"'+MARKER+'":'
MAX_BYTES=1024**3


def pack(raw):
    if len(raw)<4096:return raw
    source=raw.encode('utf-8')
    if len(source)<16384 or len(source)>MAX_BYTES:return raw
    compressed=zlib.compress(source,1)
    if len(compressed)*1.4>=len(source):return raw
    return json.dumps({MARKER:dict(codec='zlib-json-1',bytes=len(source),
        sha256=hashlib.sha256(source).hexdigest(),data=base64.b64encode(compressed).decode('ascii'))},separators=(',',':'))


def unpack_value(value):
    if not isinstance(value,dict) or MARKER not in value or len(value)!=1:return value
    return json.loads(unpack(json.dumps(value,separators=(',',':'))))


def unpack(raw):
    if not raw.startswith(PREFIX):return raw
    try:
        envelope=json.loads(raw)
        if set(envelope)!={MARKER}:return raw
        record=envelope[MARKER];size=record['bytes']
        if record['codec']!='zlib-json-1' or type(size) is not int or not 0<=size<=MAX_BYTES:
            raise ValueError('Invalid journal storage contract')
        decoder=zlib.decompressobj()
        data=decoder.decompress(base64.b64decode(record['data'],validate=True),size+1)
        if len(data)!=size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Invalid journal storage length')
        if hashlib.sha256(data).hexdigest()!=record['sha256']:
            raise ValueError('Invalid journal storage checksum')
        return data.decode('utf-8')
    except Exception as exc:
        raise ValueError('Corrupt compressed journal evidence') from exc
