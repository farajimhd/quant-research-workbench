"""Reviewed original HTML observations, pinned to source bytes and issuer.

These are observation-level mappings, not statements that a filing contains
only these facts. Do not generalize a mapping to another filing or issuer.
"""
from .contracts import digest
from .sec_html_context import VERSION

# CIK, original document hash, as-of date, exact reported count, exact quote.
# Reviewed against retained original cover text; no bulk fact supplies a value.
SHARES=(
 ('0000744452','0b01b232b9aa76ed8491235755a18a7301df62a0ed363ed558b7f0625f07544a','2018-12-14','30,112,057',
  'As of December 14, 2018, the Registrant had outstanding 30,112,057 shares of common stock, par value $0.001 per share.'),
 ('0001465128','34813598fd18c1c23db0bdee122235244dd9774475b657f20e591c8fd52f975a','2018-11-05','275,351,911',
  'The number of shares of the issuer’s common stock, $0.01 par value, outstanding as of November 5, 2018 was 275,351,911.'),
 ('0001065280','b7833001118c0418e268561cc967fdcc8651caa99db45785a44f01cea79ef676','2018-12-31','436,598,597',
  'As of December 31, 2018 , there were 436,598,597 shares of the registrant’s common stock, par value $0.001, outstanding.'),
 ('0000030625','51e59160b24056937e72a020746f454d65ef720a941d4c278718a292f3caf2e4','2019-02-13','130,982,978',
  'Number of the registrant’s common shares outstanding as of February 13, 2019 was 130,982,978 .'),
 ('0001435064','38d315949456cf0e0f704239583c44b0deadf33c0d4a60bc682a2597aada6301','2019-01-21','13,327,160',
  'As of January 21, 2019, the registrant had 13,327,160 shares of common stock outstanding.'),
 ('0001434316','ec3d9b058394c31bb12071be25a7bb4caaa6c2dc966d638ee7975a7eb53510a9','2018-10-30','64,518,813',
  'As of October 30, 2018, 64,518,813 shares of the registrant’s common stock, par value $0.001 per share, were issued and outstanding.'),
 ('0001167419','0404e080bad561b24ca4e36010747d259d44b984c3314853801d7a9d0dd0897b','2019-01-10','14,519,058',
  'The number of shares of no par value common stock outstanding as of January 10, 2019 was 14,519,058.'),
 ('0000004127','d2beb075a97aeb860efd9ff475ed1a6d04d05f1881d8a9bea5c70fd78ab7a7b6','2019-01-15','174,059,816',
  'The number of outstanding shares of the registrant’s common stock, par value $0.25 per share, as of January 15, 2019, was 174,059,816.'),
 ('0000821483','9b9475fd9cfd58438752812fc8791d09e7820ea7b18c5e854894071a03d17b02','2018-11-02','45,876,894',
  '45,876,894 shares of Common Stock, $0.01 par value, were outstanding as of November 2, 2018'),
 ('0001604821','4476844d68043967d539a6a2af80dbeec6abb9b659c704868ea26cc95f8f4ac3','2018-05-03','54,512,645',
  'As of May 3, 2018, the number of outstanding shares of the registrant’s common stock, par value $0.0001 per share, was 54,512,645.'),
 ('0001527590','d7160256e7e51ac29daa6ef235b655dc829ffd35670f51c86cab75f5ee6bb98f','2019-02-11','32,105,112',
  'On February 11, 2019, the registrant had a total of 32,105,112 shares of common stock, $0.0001 par value, outstanding.'),
 ('0001615063','8d9403171a67303716ed34e8f74432fe3d6d7f1b821a294706c250df8eefe93d','2019-01-15','21,494,513',
  'As of January 15, 2019, there were 21,494,513 shares of the registrant’s common stock, par value $0.0001 per share, outstanding.'),
)


def plan_for(document_hash,cik):
    from .sec_html_reviewed_spac_gaps import plan_for as spac_gaps
    reviewed=spac_gaps(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_caliber import plan_for as caliber
    reviewed=caliber(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_icici import plan_for as icici
    reviewed=icici(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_braiin_roze import plan_for as braiin_roze
    reviewed=braiin_roze(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_liberty import plan_for as liberty
    reviewed=liberty(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_moove import plan_for as moove
    reviewed=moove(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_optimi import plan_for as optimi
    reviewed=optimi(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_xjet_empro import plan_for as xjet_empro
    reviewed=xjet_empro(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_origin_liftoff import plan_for as origin_liftoff
    reviewed=origin_liftoff(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_medical import plan_for as medical
    reviewed=medical(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_fort import plan_for as fort
    reviewed=fort(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_aya import plan_for as aya
    reviewed=aya(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_btq import plan_for as btq
    reviewed=btq(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_qumulus import plan_for as qumulus
    reviewed=qumulus(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_registration import plan_for as registration
    reviewed=registration(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_gaps import plan_for as gaps
    reviewed=gaps(document_hash,cik)
    if reviewed is not None:return reviewed
    from .sec_html_reviewed_2019 import plan_for as remaining
    reviewed=remaining(document_hash,cik)
    if reviewed is not None:return reviewed
    if document_hash=='02e3c758d608b4130b69b9fbef02e08f726b4b81608782d689bd0199f51f036a' and str(int(cik)).zfill(10)=='0001576873':
        # Oroplata 2018-12-31 original: physical TD coordinates, not inferred
        # column offsets. No dashes, per-share ambiguity or narrative values.
        selected=[]
        rows=(
            (8,8,'Total current assets','AssetsCurrent',('133717','308769')),
            (8,12,'Total assets','Assets',('168967','308769')),
            (8,23,'Total current liabilities','LiabilitiesCurrent',('3774203','2741281')),
            (9,19,'Net loss','NetIncomeLoss',('-2569806','-2517977')),
            (10,20,'Net Cash Used In Operating Activities','NetCashProvidedByUsedInOperatingActivities',('-518802','-141932')),
        )
        for table,row,label,tag,values in rows:
            for column,value in enumerate(values,1):
                end=('2018-12-31','2018-09-30')[column-1] if table==8 else ('2018-12-31','2017-12-31')[column-1]
                start=None if table==8 else ('2018-10-01','2017-10-01')[column-1]
                header=(('December 31, 2018','September 30, 2018')[column-1] if table==8 else
                    ('For the three months ended December 31, 2018','For the three months ended December 31, 2017')[column-1])
                selected.append(dict(concept='{http://fasb.org/us-gaap/2018-01-31}'+tag,
                    period_start=start,period_end=end,unit='USD',scale=0,label=label,
                    evidence=[header,'are expressed in U.S. dollars.'],expected_value=value,decimals='0',
                    locator=dict(kind='cells',table=table,row=row,columns=[column],label_column=0)))
        body=dict(version=VERSION,document_hash=document_hash,cik='0001576873',scope='reviewed_observations',facts=selected)
        return dict(body,plan_hash=digest(body))
    for issuer,source,end,value,quote in SHARES:
        if source!=document_hash or issuer!=str(int(cik)).zfill(10):continue
        item=dict(concept='{http://xbrl.sec.gov/dei/2018-01-31}EntityCommonStockSharesOutstanding',
            period_start=None,period_end=end,unit='shares',scale=0,label='shares',evidence=[quote],
            expected_value=value.replace(',',''),decimals='0',locator=dict(kind='quote',quote=quote,value_text=value))
        body=dict(version=VERSION,document_hash=source,cik=issuer,scope='reviewed_observations',facts=[item])
        return dict(body,plan_hash=digest(body))
    return None
