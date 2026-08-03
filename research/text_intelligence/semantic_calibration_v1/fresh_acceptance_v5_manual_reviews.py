from __future__ import annotations


REVIEW_CONTRACT = "news_fresh_acceptance_v5_manual_comparison_review_v1"

# One reviewer-authored disposition per final audit Markdown.  These rows are
# certification evidence only and are never imported by deterministic V9.
# Format: sample_id|gold_status|v9_status|review finding
REVIEW_ROWS = """
N1501|pass|repair|V9 misses issuer-direct provenance and overreads earnings/ownership while missing operations
N1502|pass|repair|V9 drops a title-only maintain-rating and lower-target analyst opinion
N1503|pass|repair|V9 treats a non-issuer ETF watch list as issuer history
N1504|pass|repair|V9 lets regain-compliance language reverse a current minimum-bid deficiency and misstates provenance
N1505|pass|repair|V9 treats a thematic leveraged-ETF article as issuer history and misstates provenance
N1506|pass|repair|V9 misses a confirmed issuer ticker-change and its identity/listing context
N1507|repair|repair|Gold origin should be editorial aggregation of a 13G filing; V9 also misses target-positive ownership direction and eligibility
N1508|pass|repair|V9 mistakes a retrospective why-moving explanation for the underlying analyst event and omits financing/guidance
N1509|pass|repair|V9 recognizes the award but incorrectly suppresses forecast and reaction eligibility
N1510|pass|repair|V9 misses regulatory-report role and the contextual negative ownership disposal
N1511|pass|repair|Corrupt provider identity is correctly rejected; V9 uses the mismatched body instead of failing identity integrity
N1512|pass|repair|V9 misses issuer-direct governance provenance and the management-governance concept
N1513|pass|repair|V9 recognizes both issuers but misses target/acquirer M&A semantics direction and trigger eligibility
N1514|repair|repair|Gold should classify the tickerless sector article as non-issuer market content; V9 also misstates syndicated provenance
N1515|pass|repair|V9 drops an explicit title-only downgrade and its analyst subject
N1516|pass|repair|V9 direction and analyst role are correct but provenance should remain analyst research
N1517|pass|repair|V9 misses an issuer-announced expanded financing facility and its positive trigger semantics
N1518|pass|repair|V9 semantics match but issuer-direct buyback provenance is missed
N1519|pass|pass|Contract award identity role direction concepts and eligibility all agree
N1520|pass|repair|V9 mistakes editorial competitive analysis for an issuer event and loses three explicitly discussed companies
N1521|pass|repair|V9 misses transaction context and issuer-specific mixed versus negative analyst conclusions
N1522|pass|repair|V9 issuer semantics agree but a templated earnings wire is not issuer-direct prose
N1523|pass|repair|V9 issuer semantics agree but analyst-research provenance is missed
N1524|pass|repair|V9 fails to identify a retrospective mover recap and its warning-driven negative context
N1525|pass|repair|V9 fails point-in-time Roche share-class identity and therefore loses the confirmed regulatory product event
N1526|repair|repair|Gold omits legitimate HPE and CASY scheduled-earnings context; V9 also over/under-assigns preview issuer passages
N1527|repair|repair|Gold incorrectly rejects a complete provider article because a separate external lane is a bot challenge; V9 should ignore boilerplate and scope contextual issuers
N1528|pass|repair|V9 detects templating but confuses a valuation explainer with earnings and loses mixed valuation semantics
N1529|pass|repair|V9 over-promotes analyst framing and misses NIO product context plus Tesla competitor context
N1530|pass|repair|V9 role and concepts agree but loses the positive analyst-rating distribution
N1531|repair|repair|Gold assigns the SPAC IPO unit event to parent ticker CHEC instead of explicitly announced CHECU; V9 then misroles the listing as a follow-up
N1532|repair|repair|Gold wrongly rejects a valid issuer editorial analysis; V9 should retain HDP context but suppress trigger eligibility
N1533|repair|repair|Gold wrongly rejects a valid syndicated analyst downgrade; V9 should retain ALGN while treating peer tickers as contextual only
N1534|pass|repair|V9 mistakes analyst opinion ahead of earnings for a generic preview and loses mixed operating evidence
N1535|pass|repair|V9 misses an issuer-direct completed Navy delivery and its contract/operations trigger
N1536|pass|repair|V9 gets analyst role and neutral direction but misses valuation concepts and incidental benchmark context
N1537|pass|repair|V9 misses an explicit signed five-thousand-vehicle sales contract
N1538|pass|repair|V9 overreads positive clinical/commercial evidence and misses deprioritization in mixed issuer results
N1539|pass|repair|V9 emits issuer units from a macro jobs-report article that has no issuer-specific event
N1540|pass|repair|V9 retains the analyst subject but misses analyst-research provenance and operations context
N1541|pass|repair|V9 correctly emits no issuer but misclassifies non-issuer syndicated political reporting
N1542|pass|repair|V9 direction and eligibility agree but order concepts are replaced by an unrelated earnings concept
N1543|pass|repair|V9 drops an explicit neutral-rating analyst opinion with offsetting fundamentals and M&A speculation
N1544|pass|repair|V9 correctly emits no issuer but misses macro market-roundup classification
N1545|pass|repair|V9 converts routine planned retirement into a negative regulatory trigger
N1546|pass|repair|V9 correctly emits no issuer but misclassifies state gaming statistics as an issuer event
N1547|pass|repair|V9 semantics agree but editorial aggregation provenance is missed
N1548|repair|repair|Gold incompletely annotates a multi-issuer market update; V9 also needs precise passage ownership and complete contextual issuer coverage
N1549|pass|repair|V9 loses the settlement's offsetting certainty benefit and overstates purely negative legal sentiment
N1550|pass|repair|V9 correctly rejects issuer units but misses the regulatory-policy role and syndicated origin
N1551|repair|repair|Gold covers only selected legitimate mover passages; V9 likewise has inconsistent issuer coverage and scoped concepts
N1552|pass|repair|V9 misses the positive long-dated refinancing event and issuer-direct provenance
N1553|pass|repair|V9 drops all three issuer-specific Ebola treatment contexts from a thematic mover article
N1554|pass|repair|V9 treats templated earnings reporting as issuer direct and misses the beat-versus-decline mixed result
N1555|pass|repair|V9 loses target and acquirer transaction roles and overfocuses analyst framing while dropping peer context
N1556|pass|repair|V9 mistakes a retrospective price-move explanation for the original analyst event
N1557|pass|pass|Title-only hold initiation identity role direction concepts and eligibility all agree
N1558|pass|pass|Title-only downgrade and target-cut semantics agree
N1559|repair|repair|Gold incompletely labels a comprehensive scheduled-earnings list; V9 coverage is broader but automated provenance is wrong
N1560|pass|repair|V9 misses reverse-split listing semantics direction and trigger eligibility
N1561|pass|repair|V9 misses first-subject clinical advancement and issuer-direct provenance
N1562|pass|repair|V9 misses an explicit intended exchange delisting and invents transaction semantics
N1563|pass|repair|V9 misses the patent grant regulatory/product event and trigger eligibility
N1564|pass|repair|V9 correctly rejects issuer units but overstates an interview story as a primary event
N1565|repair|repair|Gold wrongly rejects a valid syndicated bearish analyst article; V9 should retain PANW and scope peers as contextual
N1566|pass|repair|V9 analyst conclusion agrees but omits the improving-demand operations concept
N1567|repair|repair|Gold wrongly rejects valid trader-opinion issuer passages; V9 must classify them as contextual editorial opinion rather than issuer triggers
N1568|pass|repair|V9 issuer result agrees but templated dual-miss earnings wire is not issuer-direct prose
N1569|pass|repair|V9 misses a regulatory-primary SEC inquiry into accounting practices and its negative trigger
N1570|pass|repair|V9 direction agrees but substitutes earnings for the analyst's operating-capex rationale
N1571|pass|repair|V9 correctly rejects issuer units but misstates syndicated provenance
N1572|pass|repair|V9 correctly rejects issuer units but a curated fintech digest is editorial analysis not a generic market roundup
N1573|pass|repair|V9 misses the issuer-filed arbitration claim legal event and its mixed trigger semantics
N1574|pass|repair|V9 over-promotes the counterparty partnership passage to trigger eligibility
N1575|pass|pass|Maintained-buy and lowered-target analyst opinion is correctly mixed and contextual
N1576|repair|repair|Gold labels only two of many legitimate mover explanations; V9 also has incomplete and imprecise passage ownership
N1577|pass|repair|V9 misses mixed buyback-positive and debt-negative capital allocation
N1578|pass|repair|V9 misses contract/open-season trigger semantics for both joint participants
N1579|pass|repair|V9 creates issuer histories from a crime and review-bombing story that contains no investable issuer event
N1580|pass|repair|V9 semantics mostly agree but misses analyst-research provenance and guidance context
N1581|pass|repair|V9 misses the positive clinical trial-start trigger for ARAV
N1582|pass|repair|V9 misses non-issuer automated macro-stat content for durable goods
N1583|pass|repair|V9 captures the JPM analyst action but misclassifies provenance
N1584|pass|repair|V9 captures the PMEC listing-compliance event but misclassifies provenance
N1585|pass|repair|V9 needs point-in-time TSXV share-class identity rather than collapsing MERC.P into MERC
N1586|pass|repair|V9 misses the positive analyst-defense direction for UBNT and its research provenance
N1587|pass|repair|V9 misses non-issuer automated natural-gas market statistics
N1588|pass|repair|V9 overstates analyst authority in HUM valuation analysis and loses UNH peer context
N1589|pass|repair|V9 misses non-issuer automated banking-statistics content
N1590|pass|repair|V9 misclassifies DSTI financing as M&A and mixed rather than positive financing
N1591|pass|repair|V9 matches RHP earnings semantics but misclassifies a templated result as issuer-direct
N1592|pass|repair|V9 misses CEO-transition governance semantics for NRG and misclassifies provenance
N1593|pass|repair|V9 misses non-issuer automated public-health statistics
N1594|gold_incomplete|repair|Gold omits legitimate CBRL and ZION issuer passages from the volume-movers article; V9 also scopes the recap imprecisely
N1595|pass|repair|V9 overstates analyst authority in subprime-lending editorial analysis and loses peer context
N1596|pass|repair|V9 captures the European macro article but misclassifies research provenance
N1597|pass|repair|V9 treats a reported KKR acquisition as issuer-direct and misses the M&A concept
N1598|pass|repair|V9 misses operations evidence accompanying the AGCO hold rating and target increase
N1599|pass|repair|V9 underweights the negative dilution evidence in KAVL's proposed offering
N1600|pass|repair|V9 reverses the bullish KNTE analyst thesis and emits unsupported concepts
N1601|pass|repair|V9 misses positive direction in the CMC retrospective-return summary
N1602|pass|repair|V9 misses the negative CVNA short-seller thesis and its research provenance
N1603|pass|repair|V9 misses operations evidence accompanying PFE's downgrade and target cut
N1604|pass|repair|V9 recognizes both counterparties but misclassifies ArcelorMittal's collaboration as editorial and suppresses its positive trigger
N1605|pass|repair|V9 matches the contextual HTOO mover recap but misclassifies automated provenance
N1606|pass|repair|V9 misses AMZN competitor context and the growth and valuation evidence in the MSFT analyst thesis
N1607|gold_incomplete|repair|Gold covers only five of a multi-issuer Barron's roundup; V9 also confuses roundup provenance and emits an unsupported ZM unit
N1608|pass|none|V9 matches the mixed MTN issuer event and eligibility
N1609|pass|repair|V9 misclassifies a templated worsening EPS result as issuer-direct primary reporting
N1610|pass|repair|V9 rejects an explicit multi-million AVNW contract despite direct issuer evidence
N1611|pass|repair|V9 misses non-issuer Federal Reserve regulatory content and primary-source provenance
N1612|pass|repair|V9 rejects contextual positive EV-policy analysis for F GM and TSLA and misclassifies research provenance
N1613|gold_identity_uncertain|repair|Gold rejects an identifiable Visa versus Mastercard analysis; V9 over-emits loosely mentioned issuers and needs evidence-scoped identity
N1614|gold_identity_uncertain|repair|Gold rejects a point-in-time SPAC security that V9 maps to TWNT without explicit share-class evidence
N1615|pass|none|V9 correctly treats the political article as non-issuer editorial content
N1616|pass|repair|V9 rejects supported Facebook legal and regulatory context and over-relies on unrelated provider tickers AAPL and GOOG
N1617|pass|repair|V9 captures ITW's mixed earnings but misclassifies templated reporting as issuer-direct and misses guidance
N1618|pass|repair|V9 suppresses the positive earnings trigger in an automated SCM results summary and adds unsupported guidance
N1619|pass|repair|V9 treats an explicit UBS CEO transition as editorial and misses governance semantics
N1620|pass|none|V9 matches the SPGI analyst action and contextual eligibility
N1621|pass|repair|V9 misses the preview role and fabricates mixed guidance semantics for an expectations-only INVE article
N1622|pass|repair|V9 collapses CLSN's liquidity benefit and dilution harm into purely negative sentiment
N1623|pass|repair|V9 matches the LULU analyst action but omits operating evidence supporting the upgrade
N1624|pass|repair|V9 treats a retrospective MSBI mover explanation as a new issuer event and loses negative earnings direction
N1625|pass|none|V9 matches the ASML upgrade and target raise
N1626|pass|repair|V9 misses positive retrospective performance and emits unsupported earnings and capital-return concepts for KKR
N1627|pass|none|V9 correctly treats the Acorns promotion as non-issuer editorial content
N1628|gold_incomplete|repair|Gold rejects an identifiable Zacks downgrade list; V9 finds candidates but over-broadly treats every provider ticker as supported issuer history
N1629|pass|repair|V9 matches non-issuer cannabis-policy content but misclassifies original versus aggregated provenance
N1630|pass|repair|V9 misclassifies a templated CTVA sales miss as issuer-direct primary reporting
N1631|gold_incomplete|repair|Gold rejects an identifiable insurance analyst article; V9 finds candidate issuers but over-scopes metadata and misclassifies the analyst role
N1632|pass|repair|V9 should identify stablecoin commentary as non-issuer market content rather than unsupported issuer content
N1633|pass|repair|V9 rejects a neutral TSN earnings-call status notice and misclassifies its automated provenance
N1634|pass|repair|V9 loses PINS ownership context, invents ARKW issuer semantics and misclassifies an Ark-trade article as automated
N1635|pass|repair|V9 misses the positive direction and analyst-research provenance of DRUG coverage initiation
N1636|gold_incomplete|repair|Gold captures only ARQL although the title contains a distinct Foot Locker options alert; V9 also mistakes market activity for issuer news
N1637|pass|repair|V9 turns an explicitly positive MSFT analyst thesis mixed and emits unsupported earnings and guidance concepts
N1638|pass|repair|V9 matches non-issuer content but misclassifies aggregated reporting provenance
N1639|pass|repair|V9 misses non-issuer sovereign-credit commentary and research provenance
N1640|pass|repair|V9 misclassifies an NKTR trading halt and omits listing and market-structure semantics
N1641|gold_incomplete|repair|Gold labels only HUM and ETP from a broad morning summary; V9 over-emits metadata tickers but misses supported analyst concepts
N1642|pass|repair|V9 correctly excludes issuer units but misclassifies geopolitical sanctions reporting as a roundup
N1643|pass|repair|V9 omits TEVA from a joint Phase II enrollment announcement and suppresses the positive clinical trigger for both parties
N1644|pass|repair|V9 matches the ATK contract but omits product-development semantics
N1645|pass|repair|V9 treats a newly priced dilutive MLYS offering as a follow-up and suppresses its negative trigger
N1646|pass|repair|V9 reverses the positive legal resolution in EAR's why-moving follow-up and emits unsupported earnings
N1647|pass|repair|V9 correctly excludes issuer units but misclassifies an automated Japan GDP release as a why-moving follow-up
N1648|pass|repair|V9 matches the OII why-moving context but misclassifies aggregated provenance
N1649|pass|repair|V9 treats a full JACK conference-call transcript as a fresh issuer trigger and emits six unsupported concept families
N1650|gold_incomplete|repair|Gold identifies CTHR despite missing provider metadata but omits other supported mover passages; V9 over-trusts provider ticker VG and misses text-grounded CTHR
N1651|pass|repair|V9 misses non-issuer regulatory policy content in the opioid emergency declaration
N1652|gold_incomplete|repair|Gold rejects an identifiable multi-issuer television picks article; V9 only partially recovers its issuer contexts and misclassifies it as primary
N1653|pass|repair|V9 reverses a positive PFG upgrade because of target removal and emits unsupported earnings and guidance concepts
N1654|pass|repair|V9 recognizes both branch-sale parties but suppresses ISBC's positive regulatory-approval trigger and misclassifies provenance
N1655|pass|repair|V9 correctly excludes issuer units but treats geopolitical commodity reporting as a primary event
N1656|pass|repair|V9 misses non-issuer automated payroll statistics
N1657|gold_incomplete|repair|Gold rejects the entire mover recap rather than retaining supported contextual issuer passages; V9 emits only JAGX and misclassifies provenance
N1658|pass|repair|V9 misses the price-only mover role and automated provenance for MNST
N1659|gold_incomplete|repair|Gold rejects an identifiable pipeline analyst article; V9 finds candidates but over-scopes identities and misclassifies the analyst role and provenance
N1660|pass|repair|V9 misses the KORS price-and-rumor follow-up role and aggregated provenance
N1661|pass|repair|V9 drops GM and neutralizes positive forward-EPS ranking evidence for all four automakers
N1662|pass|repair|V9 treats an IPHI options-flow alert as an issuer event and loses its bullish market-activity direction
N1663|pass|repair|V9 incorrectly maps a general dental-CBD study to Colgate and makes it a regulatory issuer trigger
N1664|pass|repair|V9 should retain identity_not_found for Reddit's pre-listing IPO article rather than classify it as non-issuer content
N1665|pass|repair|V9 fabricates ENGT from Engadget-like text in a non-issuer Bitcoin product article
N1666|pass|none|V9 matches EBS's neutral governance update and contextual eligibility
N1667|pass|repair|V9 misses the negative Citron short thesis on MSI and misclassifies research provenance
N1668|pass|repair|V9 misses non-issuer geopolitical content and aggregated-wire provenance
N1669|pass|repair|V9 mines six unsupported concepts and mixed sentiment from a full FFAI transcript that should remain neutral history
N1670|pass|repair|V9 loses ROKU's sympathy decline and NFLX's mixed beat guidance and profit-taking context
N1671|pass|repair|V9 mostly captures SENS guidance and FDA context but substitutes unsupported earnings and clinical concepts for operations
N1672|pass|repair|V9 loses the mixed JWN analyst thesis and omits guidance and operating evidence
N1673|pass|repair|V9 rejects supported negative ride-sharing disruption analysis for CAR and HTZ
N1674|pass|repair|V9 neutralizes CHTR's downgrade and omits operating pressure for both cable issuers
N1675|pass|repair|V9 incorrectly emits ETF issuer units for a macroeconomic Philippines GDP article
N1676|pass|repair|V9 suppresses MSI's positive product trigger and omits competitive DGLY and TASR context
N1677|pass|repair|V9 treats a historical AMZN earnings-reaction study as a new trigger rather than contextual history
N1678|pass|repair|V9 misses non-issuer geopolitical content and aggregated-wire provenance
N1679|pass|none|V9 correctly classifies the political legal article as non-issuer editorial content
N1680|pass|repair|V9 captures the SYMC downgrade but omits guidance and M&A context
N1681|pass|repair|V9 misclassifies an automated crypto price check as a primary event
N1682|pass|repair|V9 suppresses the positive COLM earnings trigger and adds unsupported guidance
N1683|pass|repair|V9 matches the CMG target raise but omits the earnings evidence supporting it
N1684|gold_incomplete|repair|Gold selects only WMB ETE and DIS from a broad market update; V9 over-scopes provider tickers and misses supported M&A and mixed semantics
N1685|pass|repair|V9 rejects a supported BHAT trading-halt history unit and misclassifies automated provenance
N1686|pass|none|V9 matches USCR's balanced maintained-buy and lowered-target analyst action
N1687|gold_incomplete|repair|Gold rejects an identifiable earnings-rich market preview; V9 emits every provider ticker without passage-level support
N1688|pass|repair|V9 suppresses the positive AAPL FDA-enabled product-launch trigger and misclassifies provenance
N1689|pass|repair|V9 drops UAL catalyst and AAL DAL peer context while attributing guidance to LUV
N1690|pass|repair|V9 rejects an explicit exclusive CAR partnership and misses contract and product-commercial semantics
N1691|pass|repair|V9 rejects positive HVT comparable-sales results and fails to propagate the issuer event across its valid share classes
N1692|pass|repair|V9 misses non-issuer geopolitical contract reporting and aggregated-wire provenance
N1693|pass|repair|V9 neutralizes PDD's outperform initiation and misclassifies research provenance
N1694|pass|repair|V9 matches non-issuer FX movement but misclassifies a market update as generic editorial analysis
N1695|pass|repair|V9 rejects supported rumored M&A context for FEYE PANW and SYMC
N1696|pass|repair|V9 treats a cash-and-warrant settlement as a follow-up and suppresses its negative financing trigger
N1697|pass|repair|V9 mostly captures CRKN regulatory support but substitutes product-commercial for operating evidence
N1698|pass|repair|V9 rejects contextual positive MRNS sympathy movement and SAGE clinical catalyst history
N1699|pass|repair|V9 turns positive MAT management analysis mixed and drops governance operations and WMT commercial context
N1700|pass|repair|V9 neutralizes KALA's mixed ownership and dilution context, emits unsupported concepts and drops NBY context
N1701|pass|none|V9 matches the GIS downgrade
N1702|pass|repair|V9 misclassifies a five-stock attention recap as automated, drops KMX and invents positive event semantics
N1703|pass|repair|V9 turns OSK's balanced neutral initiation positive and emits unsupported financial concepts
N1704|pass|repair|V9 confuses defunct QTWW with QMCO, drops QTWW's contract trigger and loses UPS counterparty semantics
N1705|pass|repair|V9 collapses CRAI's mixed profitability and declining revenue into negative and adds unsupported operations
N1706|pass|repair|V9 treats AAPL price-speculation analysis as a primary event and loses mixed pricing-versus-demand semantics
N1707|gold_incomplete|repair|Gold retains only HDSN from a ten-stock after-hours recap; V9 over-emits unsupported earnings across metadata tickers
N1708|gold_incomplete|repair|Gold retains only four analyst actions from a larger upgrades and downgrades roundup; V9 over-scopes metadata and misclassifies the article as a single analyst event
N1709|pass|repair|V9 misclassifies templated WTBA results as issuer-direct and loses the mixed miss versus year-over-year improvement
N1710|pass|repair|V9 rejects supported contextual FSLR and GM project evidence and misses the why-moving role
N1711|gold_incomplete|repair|Gold retains only three issuers from a twelve-stock loser recap; V9 over-emits metadata but still misstates selected issuer directions and concepts
N1712|pass|repair|V9 misclassifies an automated EIA inventory release as generic editorial analysis
N1713|pass|repair|V9 collapses PCG's contested restructuring progress into negative and loses financing and valuation context
N1714|pass|repair|V9 suppresses SPGI's positive confirmed acquisition trigger and misclassifies aggregated provenance as issuer-direct
N1715|pass|repair|V9 suppresses PLD's positive completed acquisition trigger, misses M&A for both units and emits unsupported earnings
N1716|pass|repair|V9 matches non-issuer labor statistics but misclassifies the market-update role and provenance
N1717|pass|repair|V9 rejects supported positive bank TARP-repayment history for BAC C and WFC
N1718|gold_incomplete|repair|Gold rejects an identifiable QSII earnings-miss analyst article; V9 over-scopes peer tickers and misclassifies role and provenance
N1719|pass|repair|V9 matches negative DXLG guidance but misclassifies issuer-direct provenance
N1720|pass|repair|V9 reverses the positive CIEN selloff-overdone thesis and omits guidance and valuation context
N1721|pass|repair|V9 rejects Apple's explicit Arcade product launch and misclassifies issuer-direct provenance
N1722|gold_incomplete|repair|Gold retains only CTHR from a twelve-stock after-hours recap; V9 over-emits unsupported earnings across provider tickers and neutralizes CTHR
N1723|pass|repair|V9 treats a multi-analyst AMZN post-earnings article as a why-moving follow-up, understates negativity and adds unsupported product context
N1724|pass|repair|V9 loses WOK's mixed investment-versus-structure risk and emits five unsupported concepts
N1725|gold_incomplete|repair|Gold retains only four issuers from a broad morning-loser recap; V9 over-scopes metadata and misses selected issuer directions and earnings context
N1726|pass|repair|V9 rejects IDXX's explicit positive cancer-diagnostics launch and misses clinical and product semantics
N1727|pass|repair|V9 rejects a valid neutral NEOS price-and-volume follow-up and misclassifies its role
N1728|pass|repair|V9 treats a TSO options alert as an issuer event and loses bullish market-activity direction
N1729|pass|repair|V9 matches MS management commentary but omits operations and valuation concepts and misclassifies provenance
N1730|pass|repair|V9 should classify the Bitcoin debate as non-issuer content rather than unsupported issuer content
N1731|pass|repair|V9 neutralizes AAPL's positive tablet-shipment forecast and emits unsupported earnings and analyst concepts
N1732|pass|repair|V9 suppresses ERF's mixed asset-sale trigger and misses operating and earnings implications
N1733|pass|repair|V9 matches non-issuer cotton movement but misclassifies the market-update role and provenance
N1734|pass|repair|V9 misclassifies an automated wholesale-inventories release as generic editorial analysis
N1735|pass|repair|V9 collapses CVX cost savings versus layoffs into negative, adds unsupported earnings and fabricates NYT as issuer
N1736|pass|repair|V9 suppresses MSFT's neutral bond-financing trigger and misses financing semantics
N1737|pass|repair|V9 reverses positive FIGR call context, mines seven unsupported concepts and fabricates TSCC from transcript text
N1738|pass|repair|V9 misses FLR's preview role and fabricates mixed guidance semantics from estimates
N1739|pass|repair|V9 matches negative TNDM financing but misclassifies provenance
N1740|pass|repair|V9 matches DVN's target raise but misses earnings context and misclassifies research provenance
N1741|pass|repair|V9 treats EPM's future earnings announcement as a current primary event rather than a preview
N1742|pass|repair|V9 suppresses AMZN's mixed shareholder-vote trigger and misses governance and regulatory semantics
N1743|gold_incomplete|repair|Gold retains only SINO GEMP and RXDX from a fifteen-stock gainer recap; V9 over-emits provider tickers and invents clinical events
N1744|pass|repair|V9 matches positive AMCR earnings but misclassifies the automated result as issuer-direct
N1745|pass|repair|V9 misses AMZN's price-only mover role, positive observation and automated provenance
N1746|pass|repair|V9 neutralizes favorable AMP analyst context and omits operating evidence
N1747|pass|repair|V9 misses non-issuer political content and treats it as a primary event
N1748|pass|repair|V9 neutralizes mixed semiconductor analyst theses and drops analyst-action and operating evidence for all three issuers
N1749|pass|repair|V9 suppresses DG's neutral bond-financing trigger and misses financing semantics
N1750|pass|repair|V9 treats a fresh WMT addition and AZN removal as a follow-up and suppresses opposite listing triggers
N1751|pass|repair|V9 neutralizes CRM's negative strategic-fit analysis, drops M&A for both issuers and misclassifies research provenance
N1752|pass|repair|V9 neutralizes NVO's downgrade from strong buy to buy
N1753|pass|repair|V9 treats Macy's uncertain bid analysis as a primary event and loses positive M&A context
N1754|pass|repair|V9 matches the ICLR downgrade but omits weak bookings and sales operations evidence
N1755|pass|repair|V9 suppresses THTX's positive clinical-publication trigger and fabricates a separate TH issuer from the TSX symbol
N1756|pass|repair|V9 misses non-issuer automated sovereign-yield statistics
N1757|pass|repair|V9 adds unsupported earnings semantics to contextual REGN short-interest history
N1758|pass|repair|V9 matches positive CBNA results but misclassifies the automated result as issuer-direct
N1759|pass|repair|V9 collapses H's maintained hold and lower target into negative rather than mixed
N1760|pass|none|V9 correctly treats the unionization article as non-issuer editorial content
N1761|pass|repair|V9 suppresses VREX's positive automated earnings trigger and overstates mixed evidence
N1762|pass|none|V9 matches the FVRR maintained-overweight and target raise
N1763|pass|repair|V9 matches BG's preview but misclassifies provenance and adds unsupported analyst action
N1764|gold_incomplete|repair|Gold retains only BTAI CBL and FPI from a broad market update; V9 over-scopes provider tickers and neutralizes selected gainers
N1765|pass|repair|V9 neutralizes mixed CRM and ORCL M&A analysis and omits analyst and transaction concepts
N1766|pass|repair|V9 suppresses INVE's negative below-consensus guidance trigger and misclassifies the issuer event
N1767|pass|repair|V9 turns neutral reaffirmed ZD guidance positive and adds unsupported earnings
N1768|gold_incomplete|repair|Gold retains only DAL and NMRX from a broad market update; V9 over-scopes metadata and distorts selected directions
N1769|pass|repair|V9 misses non-issuer geopolitical reporting and aggregated-wire provenance
N1770|pass|repair|V9 neutralizes MIND's positive confirmed equipment orders and substitutes capital-return for contract semantics
N1771|pass|repair|V9 rejects VSME's reverse-split compliance trigger and misclassifies it as a follow-up
N1772|pass|repair|V9 misses non-issuer European liquidity-policy reporting and aggregated provenance
N1773|pass|repair|V9 suppresses HOLI's positive strategic-sale trigger and misses M&A and governance semantics
N1774|pass|repair|V9 rejects positive SUNE financing and capacity history even while recognizing the follow-up role
N1775|pass|none|V9 matches GPS's neutral rating and target raise
N1776|pass|repair|V9 neutralizes a negative MNK short-seller allegation and misses legal semantics and research provenance
N1777|pass|repair|V9 treats AJG's commercial partnership as a follow-up and rejects its positive trigger
N1778|pass|repair|V9 matches positive HOCPY earnings but misclassifies the automated result as issuer-direct
N1779|pass|repair|V9 matches PBM's negative delisting event but misclassifies provenance
N1780|pass|repair|V9 promotes RH's retrospective beat-and-raise mover follow-up into a fresh forecast trigger
N1781|pass|repair|V9 treats aggregated search-share reporting as a primary event, neutralizes BIDU and emits unsupported GOOG concepts
N1782|pass|repair|V9 matches EW's reiteration but omits earnings evidence
N1783|gold_incomplete|repair|Gold rejects an identifiable premarket roundup; V9 emits all provider tickers without passage-level completeness certification
N1784|pass|repair|V9 rejects TEVA's negative FDA shortage trigger and misclassifies regulatory-primary provenance
N1785|pass|repair|V9 loses BCO's mixed beats versus year-over-year EPS decline and misclassifies automated results
N1786|pass|repair|V9 drops KORS peer context and misclassifies COH analyst operating evidence as earnings
N1787|gold_incomplete|repair|Gold retains only three issuers from an eight-stock 52-week-high list; V9 inconsistently emits other metadata names while missing selected ones
N1788|pass|repair|V9 treats BHP's fresh exploration-program expansion as a follow-up and suppresses positive operating and valuation context
N1789|pass|repair|V9 neutralizes bearish AMAT options activity and adds unsupported earnings
N1790|pass|repair|V9 matches WEST's sales miss but misclassifies the automated result as issuer-direct
N1791|gold_incomplete|repair|Gold retains only LZB from a broad market update; V9 over-scopes provider tickers and does not certify passage-level issuer completeness
N1792|pass|repair|V9 neutralizes uniformly positive ALIT analyst-history context
N1793|pass|repair|V9 neutralizes AIH's positive overweight initiation and omits demand-growth operations evidence
N1794|gold_incomplete|repair|Gold retains only Visa from a multi-item fintech roundup; V9 emits unrelated provider tickers and drops the supported Visa partnership
N1795|pass|repair|V9 neutralizes a negative UONE and UONEK shelf-registration trigger and misses financing and regulatory-primary provenance
N1796|pass|repair|V9 suppresses TSN's positive beat-and-execution trigger, substitutes unsupported concepts and drops incidental IWM context
N1797|pass|repair|V9 neutralizes AAPL's positive analyst thesis, omits valuation and analyst semantics and drops TSLA peer context
N1798|pass|repair|V9 matches NWL's guidance cut but omits retailer-inventory operating pressure and misclassifies provenance
N1799|pass|repair|V9 neutralizes low-PEG screen results and substitutes earnings for valuation across all four issuers
N1800|pass|repair|V9 matches non-issuer FX analysis but misclassifies aggregated provenance
N1801|pass|repair|V9 rejects supported negative GRUB and UBER failed-deal context and M&A semantics
N1802|pass|repair|V9 over-trusts unrelated metadata tickers, drops META and neutralizes SNAP's regulatory and operating risk
N1803|pass|repair|V9 rejects SNAP's negative withdrawn-guidance trigger and misclassifies provenance
N1804|pass|repair|V9 mistakes TSLA's go-private process for an analyst event, suppresses mixed trigger semantics and drops GS adviser context
N1805|pass|repair|V9 matches IGOI's positive compliance extension but misclassifies provenance
N1806|pass|repair|V9 rejects supported SUNE takeover-target and GE bidder rumor context as non-issuer content
N1807|pass|repair|V9 matches KNTK's negative secondary offering but omits ownership semantics
N1808|pass|repair|V9 neutralizes TSLA's mixed profitability-versus-grid-leadership analysis and loses operations and valuation evidence
N1809|pass|repair|V9 suppresses AAPL's positive India sales-reorganization trigger and substitutes unsupported earnings
N1810|gold_incomplete|repair|Gold retains only RUN and ARRY from a twelve-stock after-hours recap; V9 over-scopes earnings metadata and drops selected movers
N1811|pass|repair|V9 drops supported AMZN infrastructure context, adds unrelated NTRS and Visa metadata issuers, and misstates editorial provenance
N1812|pass|repair|V9 weakens a target cut with lower forecasts from negative to mixed and omits guidance evidence
N1813|pass|repair|V9 treats a templated mixed EPS-miss and revenue-beat wire as issuer-direct, neutralizes it, and suppresses trigger eligibility
N1814|repair|repair|Gold wrongly rejects an explicit five-issuer profitability screen; V9 also mistakes editorial screening for analyst research and substitutes earnings
N1815|pass|repair|V9 correctly emits no issuer but mistakes non-issuer public-health commentary for a why-moving follow-up
N1816|gold_incomplete|repair|Gold retains only TUP from a broad intraday mover roundup; V9 over-scopes provider tickers and neutralizes the supported positive earnings mover
N1817|pass|repair|V9 correctly rejects issuer scope but overstates a one-line commodity move as a market roundup and misstates provenance
N1818|pass|repair|V9 correctly emits no issuer but converts unsupported freight editorial into generic non-issuer market content and misstates provenance
N1819|pass|repair|V9 drops six explicit conference presenters and misses the neutral scheduled-preview role
N1820|pass|repair|V9 adds unsupported earnings while omitting supported operations evidence from otherwise correct analyst context
N1821|pass|none|V9 correctly classifies non-issuer political market-policy commentary
N1822|pass|repair|V9 matches the positive double beat but misclassifies the automated earnings wire as issuer-direct
N1823|pass|repair|V9 neutralizes a positive expanded commercial relationship, omits contract and product concepts, and suppresses trigger eligibility
N1824|pass|repair|V9 recognizes an automated positive earnings recap but incorrectly suppresses forecast and reaction eligibility
N1825|pass|repair|V9 neutralizes a negative sympathy-move follow-up and misstates aggregated provenance
N1826|pass|repair|V9 recognizes the buyback extension but mistakes a regulatory filing event for generic issuer prose and adds redundant regulatory concept
N1827|pass|repair|V9 makes a preliminary derivative settlement purely negative instead of mixed, adds unsupported regulatory semantics and misstates provenance
N1828|pass|repair|V9 drops explicit TRP regulatory-document context and misstates aggregated provenance
N1829|gold_incomplete|repair|Gold retains only JELD and PTX from a broad market update; V9 emits unsupported provider tickers and concepts without passage completeness
N1830|pass|repair|V9 neutralizes a positive long-term supply agreement, substitutes earnings for contract semantics and suppresses trigger eligibility
N1831|pass|repair|V9 correctly rejects issuer scope but mistakes an automated inventory-statistics release for editorial analysis
N1832|pass|repair|V9 weakens a positive adjusted-profit beat and revenue growth into mixed and misclassifies the earnings recap provenance
N1833|pass|repair|V9 neutralizes mixed promotional listing and earnings context, omits capital-structure and listing semantics and adds unsupported product context
N1834|pass|repair|V9 neutralizes a positive debt repayment, drops financing and solvency concepts, misroles the issuer announcement and suppresses eligibility
N1835|pass|repair|V9 weakens an explicit guidance cut into mixed, adds unsupported earnings and financing, misroles it as follow-up and suppresses eligibility
N1836|pass|repair|V9 neutralizes positive product-linked bullish options context and misstates aggregated provenance
N1837|pass|repair|V9 makes a mixed analyst-scoreboard summary negative and adds unsupported earnings
N1838|pass|repair|V9 makes maintained-Buy versus lowered-estimate context purely positive and omits operations evidence
N1839|pass|repair|V9 matches positive Phase 3 results but adds unsupported product-commercial semantics
N1840|pass|repair|V9 correctly rejects the automated mover list but misstates its provenance as editorial aggregation
N1841|repair|repair|Gold wrongly rejects a market roundup containing six explicit issuer passages; V9 finds them but duplicates APAGF identity and incompletely assigns transaction semantics
N1842|gold_incomplete|repair|Gold retains only BPI and BCEI from a broad market update; V9 drops both while emitting unsupported provider tickers and concepts
N1843|pass|repair|V9 neutralizes an explicit conviction-list Buy initiation
N1844|pass|repair|V9 makes neutral capital-spending guidance negative, adds unsupported earnings, misroles it as editorial and suppresses eligibility
N1845|pass|repair|V9 makes a mixed analyst-scoreboard summary negative and adds unsupported earnings and guidance
N1846|pass|none|V9 correctly captures the negative analyst downgrade
N1847|gold_incomplete|repair|Gold retains only three initiations from a larger automated initiation roundup; V9 over-scopes metadata tickers yet neutralizes selected positive actions and omits analyst concepts
N1848|repair|repair|Gold wrongly rejects an explicitly resolved Atlantic Power editorial; V9 correctly finds the issuer but fails its positive analyst and technical context
N1849|pass|repair|V9 neutralizes explicit double misses, misclassifies the automated earnings wire and suppresses trigger eligibility
N1850|pass|repair|V9 matches negative guidance but adds unsupported earnings and misstates provenance
N1851|pass|repair|V9 neutralizes an explicit downgrade from Strong Buy to Market Perform
N1852|pass|repair|V9 falsely promotes incidental Pfizer context in a non-issuer CDC leadership report and invents issuer concepts
N1853|pass|repair|V9 recognizes ICLK but treats its controlling-stake expansion as editorial, omits operations, adds unsupported concepts and suppresses eligibility
N1854|repair|repair|Gold wrongly rejects a bank-sector editorial with explicit BAC GS JPM and WFC subjects; V9 finds them but misroles it as regulatory and overclaims earnings triggers
N1855|gold_incomplete|repair|Gold retains only TSLA META and NVDA from a broader market recap; V9 over-scopes provider tickers, drops selected issuers and invents clinical semantics
N1856|pass|repair|V9 correctly rejects issuer scope but mistakes geopolitical commentary for a primary event and misstates provenance
N1857|gold_incomplete|repair|Gold omits legitimate roundup issuer passages; V9 partly scopes the roundup but adds unsupported metadata issuers and misassigns concepts
N1858|pass|repair|V9 neutralizes an explicit Overweight initiation
N1859|pass|none|V9 correctly rejects non-issuer political reporting
N1860|pass|repair|V9 neutralizes mixed control-acquisition and relisting semantics, substitutes ownership for listing, and suppresses trigger eligibility
N1861|pass|repair|V9 emits no issuer but treats geopolitical policy commentary as a primary event rather than non-issuer market content
N1862|pass|repair|V9 correctly rejects the automated mover list but misstates provenance
N1863|pass|repair|V9 makes a mixed IPO financing purely negative, omits listing semantics, misroles it as follow-up and suppresses eligibility
N1864|pass|repair|V9 neutralizes a positive cash asset sale, omits transaction and financing semantics, misstates provenance and suppresses eligibility
N1865|pass|repair|V9 recognizes negative results but mistakes an earnings recap for analyst research, omits guidance and suppresses eligibility
N1866|pass|repair|V9 neutralizes positive regulatory authorization and omits operations evidence in an otherwise correct why-moving follow-up
N1867|pass|repair|V9 falsely promotes incidental BlackRock context from a non-issuer monetary-policy article
N1868|pass|repair|V9 matches maintained Buy but omits supported positive earnings-preannouncement evidence
N1869|pass|repair|V9 neutralizes mixed earnings and unchanged-deal context, omits M&A and misroles aggregated executive commentary as primary
N1870|pass|none|V9 correctly rejects non-issuer market commentary
N1871|pass|repair|V9 mistakes an editorial five-stock attention roundup for an automated summary, drops three supported issuers and misstates directions and concepts
N1872|pass|repair|V9 finds all four downgrade subjects but misclassifies an automated roundup as a single analyst event and neutralizes TROW
N1873|repair|repair|Gold wrongly uses identity-not-found for an industry outlook naming resolvable issuers; V9 instead drops all issuer history as non-issuer content
N1874|pass|repair|V9 matches negative earnings but misclassifies the automated recap as editorial primary prose
N1875|repair|repair|Gold wrongly rejects an explicit BB&T Bear-of-the-Day analyst editorial; V9 finds the issuer but misses negative analyst semantics
N1876|gold_incomplete|repair|Gold retains only three issuers from a broad automated ratings roundup; V9 over-scopes metadata tickers and inconsistently omits analyst concepts
N1877|pass|repair|V9 falsely promotes incidental ETF metadata from a non-issuer consumer-economy editorial and invents earnings
N1878|pass|repair|V9 correctly rejects issuer scope but mistakes trade-policy analysis for a primary event
N1879|pass|repair|V9 makes a mixed carrier acquisition purely positive, adds unsupported ownership and emits a spurious share-class issuer
N1880|pass|repair|V9 matches positive acquisition direction but replaces M&A with unsupported earnings and product concepts
N1881|gold_incomplete|repair|Gold retains only three issuers from an eight-issuer biotech catalyst recap; V9 over-scopes provider tickers, neutralizes AVTX and inconsistently omits clinical concepts
N1882|pass|repair|V9 reverses positive India sales context into negative analyst sentiment, substitutes analyst action for operations and misstates provenance
N1883|pass|repair|V9 matches positive acquisition but adds unsupported earnings
N1884|pass|repair|V9 neutralizes a positive double beat, misclassifies the automated earnings wire and suppresses trigger eligibility
N1885|pass|repair|V9 correctly rejects issuer scope but mistakes an automated economic release for editorial analysis
N1886|pass|repair|V9 weakens two explicit monthly sales declines from negative to mixed and misstates provenance
N1887|pass|repair|V9 captures the negative downgrade but omits weak-recovery operations evidence
N1888|pass|repair|V9 recognizes the issuer and neutrality but fails to distinguish an earnings preview from an automated result summary
N1889|pass|repair|V9 correctly rejects issuer scope but mistakes a market wrap for generic editorial analysis and misstates provenance
N1890|pass|repair|V9 incorrectly treats Credit Suisse as a negatively affected issuer, substitutes earnings/guidance for analyst action and misstates research provenance
N1891|pass|none|V9 correctly captures the maintained Buy and raised target analyst opinion
N1892|pass|repair|V9 makes mixed IPO listing and financing purely negative, omits listing semantics and suppresses eligibility
N1893|pass|none|V9 correctly rejects non-issuer crypto commentary
N1894|pass|repair|V9 neutralizes the acquisition target and makes acquirer impact purely positive, adds unsupported earnings and misstates provenance
N1895|pass|repair|V9 neutralizes speculative promotional blockchain context, substitutes clinical for product and valuation concepts and misroles the mover follow-up
N1896|pass|repair|V9 neutralizes positive Kroger analyst context, substitutes earnings for analyst action and drops Walmart competitor context
N1897|pass|repair|V9 converts a neutral earnings preview into positive analyst action and misstates provenance
N1898|pass|repair|V9 matches both acquirers but misassigns NOG operations and adds unsupported contract and product concepts
N1899|pass|none|V9 correctly rejects non-issuer crypto technical analysis
N1900|pass|repair|V9 emits no issuer but weakens explicit crypto market content into generic no-supported-event
N1901|pass|none|V9 correctly captures positive issuer-reported quarterly improvement
N1902|pass|repair|V9 drops a positive IPO opening-price mover recap and its financing and listing context
N1903|pass|repair|V9 drops both Berkshire share classes, substitutes earnings for AAPL ownership and valuation context, and incompletely scopes issuer identity
N1904|pass|repair|V9 neutralizes negative valuation-risk commentary and substitutes unsupported earnings and financing
N1905|pass|repair|V9 correctly rejects issuer scope but mistakes an automated inventory release for editorial analysis
N1906|pass|repair|V9 neutralizes a positive four-stock EPS screen, substitutes earnings for valuation-screen semantics and misstates provenance
N1907|pass|repair|V9 falsely promotes a crypto symbol as an issuer and mistakes a crypto market roundup for an analyst event
N1908|pass|none|V9 correctly captures the negative dilutive public offering
N1909|pass|repair|V9 neutralizes positive index inclusion, omits listing semantics, misroles it and suppresses eligibility
N1910|pass|repair|V9 drops an explicit positive repurchase authorization and misstates provenance
N1911|pass|none|V9 correctly captures positive monthly comparable-sales growth
N1912|pass|repair|V9 weakens sharply improved quarterly EPS into mixed and adds unsupported operations semantics
N1913|pass|repair|V9 emits no issuer but weakens explicit crypto market content into generic no-supported-event
N1914|gold_incomplete|repair|Gold retains only GIS BBY and UTHR from a broad morning summary; V9 emits unsupported provider issuers and misassigns selected concepts and directions
N1915|pass|repair|V9 makes a mixed waiver-and-amendment financing event purely negative, omits legal semantics and misstates provenance
N1916|pass|repair|V9 correctly rejects issuer scope but mistakes eurozone investment analysis for a market roundup
N1917|pass|repair|V9 correctly rejects issuer scope but misstates original public-health editorial as aggregation
N1918|pass|repair|V9 captures the raised target but omits constructive operating-outlook evidence
N1919|pass|repair|V9 drops a positive issuer joint-venture expansion, misroles it as follow-up and suppresses eligibility
N1920|pass|repair|V9 captures mixed earnings and guidance but misclassifies the automated result wire as issuer-direct
N1921|pass|repair|V9 correctly rejects an automated mover list but misstates provenance
N1922|pass|repair|V9 correctly identifies a neutral earnings preview but misstates its automated provenance
N1923|pass|repair|V9 falsely promotes crypto and incidental Nvidia context from a non-issuer market roundup and invents earnings
N1924|pass|repair|V9 drops three of four supported regional banks, makes KEY purely negative and substitutes analyst action for regulatory and valuation evidence
N1925|pass|repair|V9 makes a neutral capital-structure reverse split negative and misroles the event
N1926|pass|none|V9 correctly captures the mixed maintained-Buy and lowered-target opinion
N1927|pass|none|V9 correctly rejects non-issuer gold and inflation analysis
N1928|pass|none|V9 correctly rejects non-issuer cannabis lifestyle reporting
N1929|pass|repair|V9 incorrectly emits TMUS earnings context from an automated mover list and misstates provenance
N1930|pass|none|V9 correctly captures the mixed maintained-Overweight and lowered-target opinion
N1931|repair|repair|Gold wrongly rejects issuer financing context embedded in the World-token why-moving article; V9 finds OCTO but misroles the follow-up and incompletely scopes the catalyst
N1932|pass|repair|V9 neutralizes negative currency-driven tax burden uncertainty, omits operations, misroles issuer guidance as editorial and suppresses eligibility
N1933|pass|repair|V9 recognizes negative annual results but misclassifies an automated result wire as a follow-up and suppresses eligibility
N1934|pass|repair|V9 captures mixed beat-and-miss results but misclassifies the automated earnings recap as issuer-direct
N1935|pass|repair|V9 matches the positive engineering award but adds unsupported earnings
N1936|pass|repair|V9 correctly rejects issuer scope but mistakes forex analysis for a market roundup and misstates provenance
N1937|pass|none|V9 correctly treats the automated ex-dividend explainer as issuer history rather than a fresh trigger
N1938|pass|repair|V9 neutralizes a strong swing to profitability, misroles the earnings recap and suppresses eligibility
N1939|pass|repair|V9 neutralizes positive analyst defense and growth evidence, omits operations and misstates research provenance
N1940|pass|repair|V9 falsely promotes incidental Salesforce location context from non-issuer protest reporting
N1941|gold_incomplete|repair|Gold retains only three scheduled earnings subjects from a broad week-ahead preview; V9 emits unsupported metadata issuers and drops selected events
N1942|pass|repair|V9 neutralizes positive seasonal capacity expansion, omits operations and governance semantics and suppresses eligibility
N1943|repair|repair|Gold wrongly rejects a Zacks bull-and-bear article naming resolvable issuers; V9 emits a spurious EPS ticker and only one partial historical alias
N1944|pass|none|V9 correctly captures the negative proposed common-stock offering
N1945|pass|repair|V9 captures guidance but drops neutral forecast direction and operating context, misstates provenance and suppresses eligibility
N1946|pass|repair|V9 emits no issuer but weakens explicit energy-policy market content into generic no-supported-event and misroles it
N1947|pass|repair|V9 captures mixed analyst and earnings context but omits weak-guidance semantics
N1948|pass|repair|V9 mistakes a neutral earnings preview for a mixed result summary and adds unsupported guidance
N1949|repair|repair|Gold wrongly rejects explicit AMD price-action context; V9 finds the issuer but adds unsupported earnings and misstates provenance
N1950|pass|repair|V9 neutralizes a positive second-day IPO mover, omits listing and financing context and misroles provenance
N1951|pass|repair|V9 correctly rejects issuer scope but mistakes a one-line cattle futures update for generic editorial analysis and misstates provenance
N1952|pass|repair|V9 drops explicit TEVA and CHD regulatory product-market context despite correctly identifying the regulatory article role
N1953|pass|none|V9 correctly captures maintained Buy and raised target
N1954|pass|none|V9 correctly captures the negative downgrade and severe target cut
N1955|gold_incomplete|repair|Gold retains only TAP AAPL and TSLA from a broader Barrons roundup; V9 over-scopes provider tickers and misassigns selected directions and concepts
N1956|pass|repair|V9 neutralizes bullish options activity, invents earnings as the event, misroles the automated alert and misstates provenance
N1957|pass|repair|V9 correctly rejects issuer scope but mistakes a crude-futures update for generic editorial analysis and misstates provenance
N1958|pass|repair|V9 neutralizes mixed no-catalyst mover context and adds unsupported contract and operations concepts
N1959|pass|repair|V9 weakens a positive EPS beat and raised lower guidance bound into mixed and adds unsupported capital return
N1960|pass|repair|V9 neutralizes a negative service outage, omits operations, misroles aggregated spokesperson reporting and suppresses eligibility
N1961|pass|repair|V9 matches a negative inline loss but misclassifies the automated earnings wire as issuer-direct
N1962|pass|repair|V9 neutralizes positive year-over-year EPS and sales growth, misclassifies the automated earnings wire and suppresses eligibility
N1963|gold_incomplete|repair|Gold retains only three issuers from a ten-stock ex-dividend schedule; V9 emits all metadata tickers but misroles the automated list
N1964|pass|repair|V9 correctly emits no issuer for a prelisting foreign IPO but mistakes the primary transaction report for generic non-issuer editorial and misstates provenance
N1965|pass|repair|V9 finds NOC and LMT but neutralizes counterparty benefit, omits product context, misroles NOC's production order and suppresses eligibility
N1966|pass|none|V9 correctly captures the negative downgrade from Buy to Hold
N1967|pass|repair|V9 makes a mixed analyst-scoreboard summary negative and adds unsupported earnings and guidance
N1968|pass|repair|V9 makes a neutral earnings preview directional and invents analyst and M&A semantics for CTAS and UNF
N1969|pass|repair|V9 drops a positive regulatory authorization, omits regulatory and product context, misroles it and suppresses eligibility
N1970|pass|repair|V9 drops EA from shared acquisition rumor context, substitutes earnings for M&A on MSFT and misstates provenance
N1971|pass|none|V9 correctly captures maintained Outperform and raised target
N1972|pass|repair|V9 emits no issuer but mistakes an automated crypto price check for a primary event and misstates provenance
N1973|pass|none|V9 correctly captures maintained Neutral and lowered target
N1974|pass|repair|V9 neutralizes mixed expanded authorization and new safety-reporting requirements, omits product context, misstates provenance and suppresses eligibility
N1975|pass|repair|V9 weakens bearish options activity into mixed and invents analyst and earnings concepts
N1976|pass|repair|V9 neutralizes positive Seagate analyst context, drops Toshiba peer context and misstates research provenance
N1977|gold_incomplete|repair|Gold retains only three issuers from a six-stock earnings preview; V9 over-scopes metadata issuers and makes scheduled context directional
N1978|repair|repair|Gold omits Silver Bull as the financed target; V9 emits it but makes CDE's mixed strategic investment negative, substitutes regulatory for ownership and misstates provenance
N1979|pass|repair|V9 captures neutral guidance but drops forecast direction, misstates provenance and suppresses eligibility
N1980|pass|repair|V9 neutralizes mixed convertible refinancing, omits capital-structure risk, misstates provenance and suppresses eligibility
N1981|pass|repair|V9 correctly finds neutral unexplained price action but fails to classify the why-moving follow-up and misstates provenance
N1982|pass|repair|V9 neutralizes positive referral-program demand context and omits product-commercial semantics
N1983|pass|repair|V9 drops explicit AT&T and T-Mobile spectrum-auction context and misroles the regulatory report
N1984|pass|repair|V9 neutralizes negative stalled buyout talks and misroles the aggregated rumor as a primary event
N1985|pass|repair|V9 incorrectly emits seven sector ETFs as issuers and misclassifies an automated market-statistics update
N1986|pass|repair|V9 neutralizes mixed operational analyst coverage and omits operations evidence
N1987|gold_incomplete|repair|Gold retains only BIIB MRK and PFE from a massive biotech preview; V9 emits a subset of unsupported metadata issuers and misassigns concepts
N1988|pass|repair|V9 emits no issuer but weakens explicit trade-policy market content into generic no-supported-event and misroles provenance
N1989|pass|repair|V9 emits no issuer but weakens explicit sovereign funding market content into generic no-supported-event and misroles provenance
N1990|pass|repair|V9 reverses a positive Buy thesis into negative and omits long-running market-share operations evidence
N1991|pass|repair|V9 promotes an unconfirmed dividend possibility into a fresh trigger and adds unsupported earnings
N1992|pass|none|V9 correctly captures the negative dilutive share offering
N1993|pass|repair|V9 neutralizes a mixed contested-proxy governance event, omits governance semantics, misroles issuer communication and suppresses eligibility
N1994|pass|repair|V9 reverses positive Target operating and earnings context into negative analyst sentiment, drops supported concepts and misroles the editorial
N1995|pass|repair|V9 weakens a positive beat-and-raised-outlook follow-up into mixed and adds unsupported product context
N1996|gold_incomplete|repair|Gold retains only NWY and IOC from a broad market update; V9 over-scopes metadata issuers, substitutes guidance for NWY earnings and reverses ZEUS direction
N1997|pass|repair|V9 falsely promotes volatility-ETF context from a non-issuer market-risk editorial and misroles it as an analyst event
N1998|gold_incomplete|repair|Gold retains only ATHN and ZEUS from a broad market update; V9 over-scopes metadata issuers and reverses ZEUS positive direction
N1999|pass|repair|V9 falsely promotes incidental PLTR and prelisting SpaceX as issuer history in a non-issuer futures and IPO market article
N2000|pass|repair|V9 captures mixed EPS-beat and sales-miss results but misclassifies the automated earnings wire as issuer-direct
""".strip()
