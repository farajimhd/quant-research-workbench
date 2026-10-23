from io import BytesIO
from hashlib import sha256
import pytest
from pipelines.certified_research.sec_submission_stream import read
from pipelines.sec.edgar.sec_filing_text_extract_parts import parse_filing


@pytest.mark.parametrize('chunk',[3,11,1024,65536])
def test_selected_documents_match_full_parser_and_complete_submission_digest(chunk):
    header=b'<ACCESSION-NUMBER>0000320193-25-000079\n<CIK>320193\n<TYPE>10-K\n'
    def doc(n,name,text):return f'<DOCUMENT>\n<TYPE>10-K\n<SEQUENCE>{n}\n<FILENAME>{name}\n<TEXT>{text}</TEXT>\n</DOCUMENT>\n'.encode()
    raw=header+doc(1,'image.htm','x'*10000)+doc(2,'financial.htm','<html>Original \u20ac 123\r\n</html>')+doc(3,'tail.htm','y'*20000)
    result,size,checksum=read(BytesIO(raw),'filing.nc',{(2,'financial.htm','10-K')},chunk_bytes=chunk)
    full=parse_filing(raw,'filing.nc')
    assert result['documents']==[full['documents'][1]]
    assert result['header_text']==full['header_text'] and size==len(raw) and checksum==sha256(raw).hexdigest()


def test_streaming_never_truncates_or_hides_encoding_changes():
    raw=b'<CIK>320193\n<DOCUMENT><TYPE>10-K\n<SEQUENCE>1\n<FILENAME>x\n<TEXT>'+b'a'*100+b'</TEXT></DOCUMENT>'
    with pytest.raises(ValueError,match='memory bound'):read(BytesIO(raw),'a',{(1,'x','10-K')},maximum_document_bytes=20)
    with pytest.raises(ValueError,match='framing'):read(BytesIO(raw[:-5]),'a',{(1,'x','10-K')})
    with pytest.raises(ValueError,match='encoding'):read(BytesIO(raw+b'\xff'),'a',{(1,'x','10-K')})
