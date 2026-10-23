"""Hash complete SEC submissions while retaining only selected documents."""
import codecs,re
from hashlib import sha256
from pipelines.sec.edgar.sec_filing_text_extract_parts import parse_filing,tag_value


def read(reader,member_name,selected,*,check=lambda:None,chunk_bytes=1024**2,
         maximum_document_bytes=64*1024**2,maximum_selected_bytes=256*1024**2):
    if not 1<=chunk_bytes<=8*1024**2:raise ValueError('Invalid streaming chunk bound')
    selected=set(selected);checksum=sha256();decoder=codecs.getincrementaldecoder('utf-8')('strict')
    header=bytearray();buffer=b'';inside=False;seen_start=False;ordinal=0;total=0
    chunks=[];prefix=bytearray();keep=None;kept=0;document_bytes=0;documents=[]
    def consume(data):
        nonlocal keep,document_bytes,kept
        if not inside:
            if not seen_start:
                header.extend(data)
                if len(header)>1024**2:raise ValueError('SEC submission header exceeds bound')
            return
        document_bytes+=len(data)
        if keep is None:
            prefix.extend(data)
            if b'<TEXT>' not in prefix.upper() and len(prefix)<65536:return
            probe=parse_filing(bytes(header)+b'<DOCUMENT>'+bytes(prefix)+b'</DOCUMENT>',member_name)['documents']
            if len(probe)!=1:raise ValueError('SEC streaming document header is ambiguous')
            meta=probe[0]
            if not tag_value(prefix.decode('utf-8',errors='replace'),'SEQUENCE'):meta['sequence_number']=ordinal
            keep=(meta['sequence_number'],meta['document_name'],meta['document_type']) in selected
            if keep:chunks.append(bytes(prefix));kept+=len(prefix)
            prefix.clear()
        elif keep:chunks.append(data);kept+=len(data)
        if keep and (document_bytes>maximum_document_bytes or kept>maximum_selected_bytes):
            raise ValueError('Selected SEC document bytes exceed memory bound')
    while chunk:=reader.read(chunk_bytes):
        check();total+=len(chunk);checksum.update(chunk)
        try:decoder.decode(chunk)
        except UnicodeDecodeError as error:raise ValueError('Large SEC submission requires explicit encoding reconciliation') from error
        buffer+=chunk
        while True:
            marker=b'</DOCUMENT>' if inside else b'<DOCUMENT>'
            found=re.search(re.escape(marker),buffer,re.I)
            if found is None:
                if len(buffer)>len(marker):consume(buffer[:-len(marker)]);buffer=buffer[-len(marker):]
                break
            consume(buffer[:found.start()]);buffer=buffer[found.end():]
            if inside:
                if keep:
                    parsed=parse_filing(bytes(header)+b'<DOCUMENT>'+b''.join(chunks)+b'</DOCUMENT>',member_name)['documents']
                    if len(parsed)!=1:raise ValueError('Selected SEC document parse differs')
                    if not tag_value(b''.join(chunks[:1]).decode('utf-8',errors='replace'),'SEQUENCE'):parsed[0]['sequence_number']=ordinal
                    documents.extend(parsed)
                inside=False;chunks=[];prefix=bytearray();keep=None;document_bytes=0
            else:inside=True;seen_start=True;ordinal+=1
    decoder.decode(b'',final=True);consume(buffer);check()
    if inside or not seen_start:raise ValueError('Incomplete SEC submission document framing')
    filing=parse_filing(bytes(header),member_name);filing['documents']=documents
    return filing,total,checksum.hexdigest()
