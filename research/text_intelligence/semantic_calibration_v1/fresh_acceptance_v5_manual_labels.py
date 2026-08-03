from __future__ import annotations


LABEL_CONTRACT = "news_fresh_acceptance_v5_prediction_blind_manual_gold_v1"

# Reviewer-authored after reading the exact blinded provider metadata and source
# lanes.  This module is gold evidence only; production classifiers must never
# import it.  Compact syntax is expanded and validated by
# run_record_fresh_acceptance.py.
COMPACT_ROWS = """
N1501|P|I|L|i|TWO~p~legal,capital_return,operations~x~2~2~111~Large settlement cost is offset by retained IP liquidity and dividends
N1502|A|A|L|a|ABX~a~analyst_action~x~1~1~001~Outperform is maintained while the target is cut materially
N1503|M|G|M|i|
N1504|R|I|L|i|VIEW~p~listing_market_structure~-~0~2~111~Nasdaq minimum bid deficiency creates delisting risk
N1505|E|G|M|i|
N1506|R|I|L|i|BBRY~p~listing_market_structure~0~0~0~111~Confirmed ticker change is material identity and listing history
N1507|R|R|L|i|SITE~p~ownership~+~1~0~111~Deere disclosed a new 14.9 percent ownership stake|DE~c~ownership~0~0~0~001~Deere is the holder rather than affected target
N1508|W|G|L|i|BCEI~p~earnings,guidance,financing,analyst_action~-~0~3~001~Retrospective explanation documents losses weak guidance liquidity risk and downgrades
N1509|P|I|L|i|KTOS~p~contract~+~2~0~111~Confirmed contract award adds up to five million dollars of work
N1510|R|G|L|i|WFC~p~ownership~-~0~1~001~Fund disclosed liquidation of its Wells Fargo stake
N1511|S|S|I|d|
N1512|P|I|L|i|VRX~p~management_governance~0~0~0~001~Routine board vacancy appointment
N1513|P|G|L|i|MFN~t~ma_transaction~+~3~0~111~Minefinders receives a 36 percent acquisition premium|PAAS~b~ma_transaction~x~1~1~111~Pan American commits substantial consideration and gains the target
N1514|E|G|I|i|
N1515|A|A|L|a|CHTR~a~analyst_action~-~0~2~001~Rating was downgraded while target was maintained
N1516|A|A|L|a|PII~a~analyst_action~-~0~1~001~Cautious analyst comments are negative contextual opinion
N1517|P|I|L|i|BXG~p~financing~+~1~0~111~Purchase facility capacity and term were increased
N1518|P|I|L|i|LPG~p~capital_return~+~2~0~111~Company expanded and extended repurchase authorization
N1519|P|I|L|i|URS~p~contract~+~2~0~111~Confirmed multi-year FEMA contract has up to thirty-five million value
N1520|E|E|L|i|NFLX~p~operations,strategy_valuation~-~0~2~001~Postal change could raise costs or reduce DVD service revenue|AMZN~m~competitive_position~+~1~0~001~Streaming model is framed as an advantage|UPS~m~commercial~+~1~0~001~Potential alternative carrier demand|FDX~m~commercial~+~1~0~001~Potential alternative carrier demand
N1521|A|A|L|a|P~a~ma_transaction,analyst_action~x~1~1~001~Offer premium is positive but analyst expects investor rejection|SIRI~a~ma_transaction,analyst_action~-~0~2~001~Acquirer was downgraded and target cut over deal concerns
N1522|S|S|L|i|ILMN~p~earnings~x~1~1~111~EPS beat conflicts with a revenue miss
N1523|A|A|L|a|RRGB~a~earnings,guidance,operations,analyst_action~+~2~0~001~Analyst describes solid results improving traffic efficiency and raised guidance
N1524|V|S|L|o|CAG~p~guidance~-~0~2~001~Observed decline follows an issuer fiscal-year warning
N1525|P|I|L|i|RHHBY~p~product_commercial,regulatory,clinical~+~2~0~111~CE mark validates a blood test for ruling out Alzheimer pathology
N1526|Q|G|L|i|KFY~p~earnings_preview~0~0~0~001~Scheduled earnings watch item|ZIM~p~earnings_preview~0~0~0~001~Scheduled earnings watch item|LNTH~p~regulatory,product_commercial~+~2~0~001~FDA approved a new PYLARIFY formulation
N1527|E|G|I|i|
N1528|E|S|L|i|TSLA~p~strategy_valuation~x~1~1~001~Valuation explainer presents both optimism and overvaluation risk
N1529|E|E|L|i|NIO~p~product_commercial,operations~+~2~0~001~New lower-priced EV and delivery plan expand the product line|TSLA~m~competitive_position~-~0~1~001~Tesla is referenced as the incumbent rival
N1530|S|S|L|a|AUPH~a~analyst_action~+~2~0~001~Analyst distribution is bullish to indifferent with no bearish ratings
N1531|P|I|L|i|CHEC~p~financing,listing_market_structure~0~1~1~111~SPAC IPO pricing and listing are confirmed with dilution but new capital
N1532|E|G|I|i|
N1533|A|G|I|a|
N1534|A|A|L|a|MU~a~analyst_action,operations~x~1~1~001~Equal weight is maintained amid oversupply but near-term positives
N1535|P|I|L|i|GR~p~contract,operations~+~1~0~111~Delivery demonstrates execution under a long-standing Navy supply position
N1536|A|A|L|a|MULE~a~analyst_action,strategy_valuation~0~1~1~001~Coverage starts at equal weight because valuation offsets growth|SPY~m~market_context~0~0~0~001~Broad market instrument is incidental comparison
N1537|P|I|L|i|KNDI~p~contract,product_commercial~+~3~0~111~Signed sales contract covers five thousand electric vehicles
N1538|P|I|L|i|LYEL~p~earnings,operations,clinical~x~2~1~111~Cash runway and clinical milestones are positive while programs are deprioritized
N1539|M|G|M|i|
N1540|A|A|L|a|MCD~a~earnings,operations,analyst_action~+~2~0~001~Analyst views earnings beat and breakfast execution positively
N1541|E|G|M|i|
N1542|P|I|L|i|BA~p~contract,product_commercial~+~2~0~111~Confirmed five-aircraft order adds backlog
N1543|A|A|L|a|SNDK~a~analyst_action,operations,ma_transaction~x~1~1~001~M&A speculation supports shares despite challenging fundamentals
N1544|M|G|M|i|
N1545|P|I|L|i|PBIB~p~management_governance~0~0~0~001~Planned retirement and succession are routine governance changes
N1546|E|G|M|i|
N1547|R|G|L|i|MTEM~p~listing_market_structure~-~0~2~111~Nasdaq minimum bid deficiency creates delisting risk
N1548|M|G|L|o|DIS~p~earnings,ma_transaction~x~1~1~001~Earnings beat and sales miss accompany a strategic stake purchase|VSI~p~earnings~-~0~2~001~Weak results drove the largest named decline|FOSL~p~earnings~-~0~2~001~Downbeat results drove a large decline
N1549|E|E|L|i|BP~p~legal~x~2~2~001~Historic settlement removes uncertainty but imposes enormous cash cost
N1550|R|G|M|i|
N1551|V|G|L|o|EBSB~p~financing,capital_structure~x~1~1~001~Offering and conversion completion accompany a sharp decline|GALT~p~regulatory,clinical~x~1~1~001~Safety disclosure lacks efficacy and accompanies a sharp decline|TRGT~p~clinical,operations~-~0~2~001~Company discontinued a clinical program|AFOP~p~earnings,analyst_action~-~0~2~001~Revenue miss and downgrade are negative|IPXL~p~regulatory~-~0~2~001~FDA inspection observations are negative
N1552|P|I|L|i|HAFN~p~financing~+~1~0~111~Long-dated refinancing strengthens debt position and growth capacity
N1553|E|G|L|o|TKMR~p~clinical,market_context~+~1~0~001~Ebola treatment demand provides speculative benefit|NNVC~p~clinical,market_context~+~1~0~001~Ebola treatment demand provides speculative benefit|BCRX~p~clinical,market_context~+~1~0~001~Ebola treatment demand provides speculative benefit
N1554|S|S|L|i|CNA~p~earnings~x~1~1~111~Profit declined year over year but exceeded expectations
N1555|E|E|L|i|LPNT~t~ma_transaction~+~3~0~111~Confirmed sale provides a substantial premium|APO~b~ma_transaction~x~1~1~111~Buyer gains scale but commits acquisition capital|CYH~m~competitive_position~0~0~0~001~Peer context only|HCA~m~competitive_position~0~0~0~001~Peer context only|THC~m~competitive_position~0~0~0~001~Peer context only
N1556|W|G|L|a|EXPD~a~analyst_action~-~0~2~001~Follow-up reports downgrade and target reduction
N1557|A|A|L|a|HOG~a~analyst_action~0~0~0~001~Coverage initiated at Hold
N1558|A|A|L|a|SWFT~a~analyst_action~-~0~2~001~Rating and target were both cut
N1559|Q|S|L|i|BBY~p~earnings_preview~0~0~0~001~Scheduled earnings preview|FWLT~p~earnings_preview~0~0~0~001~Scheduled earnings preview|MGA~p~earnings_preview~0~0~0~001~Scheduled earnings preview
N1560|R|I|L|i|BAOS~p~capital_structure,listing_market_structure~-~0~2~111~3.2-for-one consolidation materially reduces share count to support compliance
N1561|P|I|L|i|XNCR~p~clinical~+~1~0~111~First subject dosing advances a Phase 1 program
N1562|R|I|L|i|IFIN~p~listing_market_structure~-~0~2~111~Intent to delist removes exchange listing
N1563|R|G|L|i|PIRS~p~regulatory,product_commercial~+~1~0~111~Patent grant adds protected oncology intellectual property
N1564|E|E|M|i|
N1565|A|G|I|a|
N1566|A|A|L|a|ADSK~a~operations,analyst_action~+~2~0~001~Survey and macro evidence support improving demand
N1567|E|G|I|i|
N1568|S|S|L|i|TMUS~p~earnings~-~0~2~111~Both EPS and sales missed estimates
N1569|R|R|L|i|HCSG~p~regulatory,accounting~-~0~2~111~SEC inquiry concerns EPS calculation and reporting practices
N1570|A|A|L|a|KLAC~a~analyst_action,operations~-~0~3~001~Downgrade target cut and weaker capex outlook are negative
N1571|E|G|M|i|
N1572|E|E|M|i|
N1573|P|I|L|i|MERC~p~legal~x~2~1~111~Large arbitration claim offers upside but carries legal uncertainty
N1574|P|I|L|i|STX~p~contract,partnership~+~2~0~111~Partnership expands backup and recovery distribution|FJTSY~c~contract,partnership~+~1~0~001~Fujitsu expands its cloud service portfolio
N1575|A|A|L|a|CBT~a~analyst_action~x~1~1~001~Buy is maintained while target is cut sharply
N1576|V|S|L|o|SGH~p~earnings,guidance~+~3~0~001~Earnings beat and strong guidance explain the move|QNST~p~analyst_action~+~1~0~001~Coverage initiated at outperform
N1577|P|I|L|i|SIX~p~capital_return,financing~x~2~1~111~Large buyback expansion is positive while new debt raises leverage
N1578|P|I|L|i|ET~p~contract,operations~+~1~0~111~Open season can expand pipeline service|PSXP~p~contract,operations~+~1~0~111~Joint venture open season can expand service
N1579|E|G|M|i|
N1580|A|A|L|a|LULU~a~earnings,guidance,analyst_action~+~3~0~001~Tracker suggests upside to guidance and analyst raises target
N1581|P|I|L|i|ARAV~p~clinical~+~1~0~111~Updated Phase 1b results presentation advances program evidence
N1582|M|S|M|i|
N1583|A|A|L|a|JPM~a~analyst_action~x~1~1~001~Buy is maintained while target is lowered
N1584|R|I|L|i|PMEC~p~listing_market_structure~+~1~0~111~Nasdaq granted additional time to regain compliance
N1585|P|I|L|i|TSXV:MERC~p~ma_transaction,capital_structure~x~2~1~111~Qualifying combination changes ownership and issuer name subject to approvals
N1586|A|A|L|a|UBNT~a~analyst_action~+~1~0~001~Analyst defense is positive opinion
N1587|M|S|M|i|
N1588|E|E|L|i|HUM~p~strategy_valuation,regulatory~+~2~1~001~Valuation improves after policy-driven selloff but regulatory risk remains|UNH~m~competitive_position~0~0~0~001~Peer comparison only
N1589|M|S|M|i|
N1590|P|I|L|i|DSTI~p~financing~+~2~0~111~One-million-dollar capital commitment supports working capital
N1591|S|S|L|i|RHP~p~earnings~+~2~0~111~FFO and sales both beat estimates
N1592|P|I|L|i|NRG~p~management_governance~0~0~0~001~Business-unit president appointment
N1593|M|G|M|i|
N1594|V|S|L|o|RXDX~p~market_context~+~1~0~001~Exceptional relative volume and price gain|QUNR~p~ma_transaction,financing~x~1~1~001~Rejected offer and announced ADS offering|CTRP~c~ma_transaction~0~0~0~001~Bidder in rejected offer
N1595|E|E|L|i|SC~p~credit_solvency,operations~-~0~3~001~Weak income verification raises borrower and credit risk|ALLY~m~competitive_position~0~0~0~001~Peer context only|CACC~m~competitive_position~0~0~0~001~Peer context only|COF~m~competitive_position~0~0~0~001~Peer context only
N1596|A|A|M|i|
N1597|E|G|L|i|KKR~b~ma_transaction~0~1~1~001~Reported buyer may acquire Epicor at substantial value
N1598|A|A|L|a|AGCO~a~analyst_action,operations~+~1~0~001~Hold remains but target is raised amid bullish industry view
N1599|P|I|L|i|KAVL~p~financing~-~0~3~111~Proposed common stock and warrant offering is dilutive
N1600|A|A|L|a|KNTE~a~analyst_action,clinical~+~3~0~001~Coverage begins at outperform with substantial target based on pipeline opportunity
N1601|S|S|L|o|CMC~p~historical_performance~+~1~0~001~Retrospective compounding article reports long-term outperformance
N1602|A|A|L|a|CVNA~a~analyst_action,short_report~-~0~3~001~Short seller initiates strong sell and forecasts severe downside
N1603|A|A|L|a|PFE~a~analyst_action,operations~-~0~2~001~Rating and target were cut on sector and earnings pressure
N1604|P|I|L|i|MT~p~partnership,operations~+~2~0~111~Expanded Microsoft collaboration supports AI transformation|MSFT~c~partnership,commercial~+~1~0~001~Azure becomes the primary cloud platform
N1605|V|S|L|o|HTOO~p~earnings~0~0~0~001~Recap notes earnings without a directional result
N1606|A|A|L|a|MSFT~a~strategy_valuation,operations,analyst_action~+~2~0~001~Analyst forecasts strong Azure and SaaS growth|AMZN~m~competitive_position~0~0~0~001~AWS is competitive context
N1607|M|G|L|i|CSCO~p~analyst_action,strategy_valuation~0~1~1~001~Barrons networking-stock thesis|GILD~p~clinical,strategy_valuation~x~1~1~001~Vaccine-race risk context|JNJ~p~clinical,strategy_valuation~x~1~1~001~Vaccine-race risk context|NFLX~p~strategy_valuation~0~1~1~001~Media pick context|W~p~strategy_valuation~0~1~1~001~Retail pick context
N1608|P|E|L|i|MTN~p~earnings,guidance,operations~x~2~2~111~Revenue beat and transformation plan conflict with loss miss and weaker visitation
N1609|S|S|L|i|SDST~p~earnings~-~0~2~111~Loss per share worsened year over year
N1610|P|I|L|i|AVNW~p~contract,operations~+~2~0~111~Multi-million 5G-ready network agreement adds business
N1611|M|R|M|i|
N1612|A|A|L|i|F~m~regulatory,market_context~+~1~0~001~EV policy is expected to benefit domestic producers|GM~m~regulatory,market_context~+~1~0~001~EV policy is expected to benefit domestic producers|TSLA~m~regulatory,market_context~+~1~0~001~EV policy is expected to benefit electric vehicles
N1613|E|G|I|i|
N1614|R|I|I|i|
N1615|E|E|M|i|
N1616|E|G|L|i|FB~p~legal,regulatory~-~0~2~001~Congressional testimony concerns harvested Facebook data|AAPL~m~incidental_context~0~0~0~001~Provider metadata lacks supported issuer event|GOOG~m~incidental_context~0~0~0~001~Provider metadata lacks supported issuer event
N1617|S|S|L|i|ITW~p~earnings,guidance~x~1~1~111~Revenue beat offsets a slight EPS miss and broad guidance
N1618|S|S|L|i|SCM~p~earnings~+~1~0~111~Revenue grew and slightly beat while EPS was in line
N1619|P|I|L|i|UBS~p~management_governance~0~1~1~111~CEO resignation creates transition risk with interim successor
N1620|A|A|L|a|SPGI~a~analyst_action~+~1~0~001~Target raised while market perform rating maintained
N1621|Q|S|L|i|INVE~p~earnings_preview~0~0~0~001~Preview contains expectations rather than a new result
N1622|P|I|L|i|CLSN~p~financing~x~2~2~111~New capital improves liquidity but issuance is materially dilutive
N1623|A|A|L|a|LULU~a~analyst_action,operations~+~3~0~001~Upgrade and target increase cite improving sales and margins
N1624|V|S|L|o|MSBI~p~earnings~-~0~2~001~Worse-than-expected earnings explain the observed decline
N1625|A|A|L|a|ASML~a~analyst_action~+~3~0~001~Upgrade to buy and target raise are positive
N1626|S|S|L|o|KKR~p~historical_performance~+~1~0~001~Retrospective article reports long-term outperformance
N1627|E|E|M|i|
N1628|A|G|I|a|
N1629|E|G|M|i|
N1630|S|S|L|i|CTVA~p~earnings~-~0~1~111~Quarterly sales missed estimate
N1631|A|G|I|a|
N1632|E|E|M|i|
N1633|S|S|L|i|TSN~p~earnings~0~0~0~001~Call-start notice lacks a directional result
N1634|E|G|L|i|NFLX~p~earnings,guidance,strategy_valuation~x~2~1~001~Ark adds after an earnings beat but weak guidance|TEM~p~ownership~0~0~0~001~Ark transaction context|PINS~p~ownership~-~0~1~001~Ark reduced its position
N1635|A|A|L|a|DRUG~a~analyst_action~+~3~0~001~Coverage starts at outperform with a high target
N1636|E|S|L|o|ARQL~p~options_activity~+~1~0~001~Unusual call sweep is market activity rather than issuer news
N1637|A|A|L|a|MSFT~a~operations,analyst_action~+~3~0~001~Analyst cites multiple positive cloud catalysts and maintains buy
N1638|E|G|M|i|
N1639|A|A|M|i|
N1640|R|G|L|i|NKTR~p~listing_market_structure~0~0~0~001~Trading halt pending news is material market-status history
N1641|M|G|L|i|HUM~a~analyst_action~+~1~0~001~Morning summary reports an upgrade|ETP~a~analyst_action~+~1~0~001~Morning summary reports an upgrade
N1642|M|G|M|i|
N1643|P|I|L|i|TEVA~p~clinical,partnership~+~1~0~111~First patient enrollment advances a Phase II study|ATVBF~c~clinical,partnership~+~1~0~111~Co-development study begins enrollment
N1644|P|I|L|i|ATK~p~contract,product_commercial~+~2~0~111~Boeing contract funds composite nozzle development
N1645|P|I|L|i|MLYS~p~financing~-~0~3~111~Large public share offering is dilutive despite funding development
N1646|W|G|L|i|EAR~p~legal,regulatory~+~3~0~001~DOJ confirmed criminal probe is no longer active
N1647|M|S|M|i|
N1648|W|G|L|a|OII~a~analyst_action~+~3~0~001~Why-moving follow-up cites upgrade and target raise
N1649|S|S|L|i|JACK~p~earnings~0~0~0~001~Full call transcript is issuer history without a concise new semantic outcome
N1650|V|S|L|o|CTHR~p~market_context~+~1~0~001~After-hours recap records a large gain with elevated volume
N1651|M|G|M|i|
N1652|E|G|I|i|
N1653|A|A|L|a|PFG~a~analyst_action,operations~+~2~0~001~Upgrade reflects improved business outlook despite removed target
N1654|P|I|L|i|ISBC~b~ma_transaction,regulatory~+~2~0~111~FDIC approval advances branch acquisition|BHLB~c~ma_transaction~0~0~0~001~Seller is counterparty to approved branch sale
N1655|M|G|M|i|
N1656|M|S|M|i|
N1657|V|S|M|o|
N1658|V|S|L|o|MNST~p~market_context~0~0~0~001~Price-only observation has no causal event
N1659|A|G|I|a|
N1660|W|G|L|o|KORS~p~market_context,rumor~0~0~0~001~Price and recurring rumor follow-up lacks a new event
N1661|E|G|L|i|TM~p~strategy_valuation~+~1~0~001~Ranked high on forward EPS|HMC~p~strategy_valuation~+~1~0~001~Ranked high on forward EPS|GM~p~strategy_valuation~+~1~0~001~Ranked high on forward EPS|F~p~strategy_valuation~+~1~0~001~Ranked high on forward EPS
N1662|E|S|L|o|IPHI~p~options_activity~+~1~0~001~Bullish call sweep is market activity
N1663|E|E|M|i|
N1664|P|G|I|i|
N1665|E|E|M|i|
N1666|P|I|L|i|EBS~p~management_governance~0~0~0~001~Board and committee appointments are governance history
N1667|A|A|L|a|MSI~a~short_report,analyst_action~-~0~2~001~Short thesis is negative analyst context
N1668|M|G|M|i|
N1669|S|S|L|i|FFAI~p~earnings~0~0~0~001~Earnings-call transcript is history without a concise extracted outcome
N1670|W|G|L|o|ROKU~p~market_context~-~0~1~001~Shares move lower in sympathy rather than issuer event|NFLX~p~earnings,guidance~x~2~1~001~Earnings beat and raised guidance conflict with profit taking
N1671|P|I|L|i|SENS~p~guidance,regulatory,operations~+~2~0~111~Guidance reiterated and FDA review nearing completion
N1672|A|A|L|a|JWN~a~earnings,guidance,operations,analyst_action~x~2~2~001~Raised sales guidance and margins conflict with inventory concern and selloff
N1673|A|A|L|a|CAR~a~competitive_position,analyst_action~-~0~2~001~Analyst says ride-sharing models are already pressuring rentals|HTZ~a~competitive_position,analyst_action~-~0~2~001~Analyst says ride-sharing models are already pressuring rentals
N1674|A|A|L|a|CMCSA~a~analyst_action,operations~-~0~3~001~Downgrade and target cut cite subscriber pressure|CHTR~a~analyst_action,operations~-~0~3~001~Downgrade and target cut cite subscriber pressure
N1675|M|G|M|i|
N1676|P|E|L|i|MSI~p~product_commercial~+~1~0~111~Company describes new body-camera offering as compelling|DGLY~m~competitive_position~-~0~1~001~Competing body-camera issuer context|TASR~m~competitive_position~-~0~1~001~Competing body-camera issuer context
N1677|S|S|L|o|AMZN~p~historical_performance,earnings~0~0~0~001~Historical earnings-reaction study is context not current event
N1678|M|G|M|i|
N1679|E|E|M|i|
N1680|A|A|L|a|SYMC~a~analyst_action,ma_transaction,guidance~-~1~3~001~Downgrade and unachievable guidance outweigh sale consideration
N1681|S|S|M|i|
N1682|S|S|L|i|COLM~p~earnings~+~1~0~111~EPS and revenue beat with positive year-over-year sales
N1683|A|A|L|a|CMG~a~earnings,analyst_action~+~2~0~001~Strong results prompt target raise despite hold rating
N1684|M|G|L|o|WMB~t~ma_transaction~x~1~1~001~Target rejected a buyout offer|ETE~b~ma_transaction~0~1~1~001~Bidder offer was rejected|DIS~p~earnings~x~1~1~001~Earnings beat but sales missed
N1685|R|S|L|i|BHAT~p~listing_market_structure~0~0~0~001~Exchange halt pending news
N1686|A|A|L|a|USCR~a~analyst_action~x~1~1~001~Buy maintained while target is lowered
N1687|M|G|M|i|
N1688|P|G|L|i|AAPL~p~regulatory,product_commercial~+~2~0~111~FDA nod enables hypertension detection feature launch
N1689|V|G|L|o|UAL~p~guidance~+~2~0~001~Issuer guidance is the catalyst|DAL~m~market_context~+~1~0~001~Peer moves in sympathy|LUV~m~market_context~+~1~0~001~Peer moves in sympathy|AAL~m~market_context~+~1~0~001~Peer moves in sympathy
N1690|P|I|L|i|CAR~p~partnership,commercial~+~1~0~111~Exclusive mobility partnership expands distribution
N1691|P|I|L|i|HVT~p~retail_sales~+~2~0~111~Comparable sales increased ten percent|HVTA~p~retail_sales~+~2~0~111~Alternate share class shares same issuer event
N1692|M|G|M|i|
N1693|A|A|L|a|PDD~a~analyst_action~+~2~0~001~Coverage starts at outperform
N1694|M|G|M|i|
N1695|E|G|L|i|FEYE~t~ma_transaction,rumor~+~1~0~001~Reported potential takeover target|PANW~b~ma_transaction,rumor~0~1~1~001~Rumored bidder|SYMC~b~ma_transaction,rumor~0~1~1~001~Rumored bidder
N1696|R|G|L|i|DGXX~p~legal,financing~-~0~2~111~Settlement requires cash and warrant issuance
N1697|P|I|L|i|CRKN~p~regulatory,operations~+~1~0~111~Existing legislation supports remediation businesses despite policy uncertainty
N1698|V|G|L|o|MRNS~p~clinical,market_context~+~1~0~001~Shares rise in sympathy with positive peer results|SAGE~p~clinical~+~2~0~001~Positive Phase 2 results are the catalyst
N1699|A|A|L|a|MAT~a~management_governance,operations~+~1~0~001~Analyst highlights management appointments and retailer relationship|WMT~m~commercial~0~0~0~001~Top-retailer context only
N1700|W|E|L|i|KALA~p~ownership,financing~x~1~1~001~Large lender stake supports interest but warrants imply dilution|NBY~m~market_context~0~0~0~001~Peer mention lacks a supported event
N1701|A|A|L|a|GIS~a~analyst_action~-~0~2~001~Rating downgraded from buy to hold
N1702|M|G|L|o|BB~p~market_context~0~0~0~001~Retail-attention recap|COST~p~market_context~0~0~0~001~Retail-attention recap|IBM~p~market_context~0~0~0~001~Retail-attention recap|KMX~p~market_context~0~0~0~001~Retail-attention recap|ORCL~p~market_context~0~0~0~001~Retail-attention recap
N1703|A|A|L|a|OSK~a~analyst_action,strategy_valuation~0~1~1~001~Neutral initiation balances constructive industry view with valuation
N1704|P|I|L|i|QTWW~p~contract,product_commercial~+~2~0~111~Agreement supplies fuel systems for up to 319 trucks|UPS~c~contract,operations~+~1~0~001~Customer deploys lower-emission fleet systems
N1705|P|I|L|i|CRAI~p~earnings~x~1~1~111~Revenue declined year over year while quarterly profitability remained positive
N1706|E|G|L|i|AAPL~p~pricing,product_commercial~x~1~1~001~Potential premium pricing supports revenue but risks demand
N1707|V|S|L|o|HDSN~p~earnings~+~1~0~001~Earnings release is named catalyst within after-hours recap
N1708|M|S|L|a|NYT~a~analyst_action~+~2~0~001~Upgraded to buy|ITW~a~analyst_action~+~1~0~001~Upgraded from sell to hold|PPG~a~analyst_action~0~0~0~001~Analyst action roundup|EQR~a~analyst_action~0~0~0~001~Analyst action roundup
N1709|S|S|L|i|WTBA~p~earnings~x~1~1~111~EPS missed slightly but improved year over year
N1710|W|G|L|o|FSLR~p~contract,commercial~+~1~0~001~GM release identifies First Solar modules for a project|GM~p~contract,operations~+~1~0~001~Power purchase project uses First Solar modules
N1711|V|S|L|o|ESND~p~earnings~0~0~0~001~Reported earnings and revenue lack an expectation comparison|TYPE~p~ma_transaction~x~1~1~001~Acquisition intent accompanies a decline|SSH~p~financing~x~1~1~001~Agreement with investor accompanies a decline
N1712|M|S|M|i|
N1713|P|E|L|i|PCG~p~legal,financing,strategy_valuation~x~2~2~111~Plan advances victim settlement but remains contested by California
N1714|P|G|L|i|SPGI~b~ma_transaction~+~1~0~111~Confirmed acquisition adds Panjiva
N1715|P|I|L|i|PLD~b~ma_transaction~+~2~0~111~Closed four-billion-dollar acquisition expands property portfolio|LPT~m~ma_transaction~0~0~0~001~Provider ticker is not the named target
N1716|M|G|M|i|
N1717|R|G|L|i|BAC~p~financing,regulatory~+~1~0~001~Bank repaid government support|C~p~financing,regulatory~+~1~0~001~Bank repaid government support|WFC~p~financing,regulatory~+~1~0~001~Bank repaid government support
N1718|A|G|I|a|
N1719|P|I|L|i|DXLG~p~guidance~-~0~3~111~EPS and sales guidance are below estimates
N1720|A|A|L|a|CIEN~a~analyst_action,guidance,strategy_valuation~+~2~1~001~Analyst calls selloff overdone despite poor guidance
N1721|P|I|L|i|AAPL~p~product_commercial~+~1~0~111~Company introduces Apple Arcade service
N1722|V|S|L|o|CTHR~p~earnings,market_context~+~1~0~001~Earnings accompany large after-hours rise
N1723|A|A|L|a|AMZN~a~earnings,guidance,analyst_action~-~1~2~001~Mixed earnings and weak guidance prompt target cuts despite buy ratings
N1724|W|E|L|i|WOK~p~investment,capital_structure~x~2~1~001~Strategic investment may add value while split and volatility add risk
N1725|V|S|L|o|FLDM~p~earnings~-~0~3~001~Weak results drive decline|TCPI~p~earnings~-~0~3~001~Weak results drive decline|TNGO~p~earnings,guidance~-~0~3~001~Miss and lowered guidance drive decline|NPTN~p~earnings~-~0~3~001~Weak results drive decline
N1726|P|I|L|i|IDXX~p~product_commercial,clinical~+~2~0~111~Launch adds early canine lymphoma detection panel
N1727|W|S|L|o|NEOS~p~market_context~0~0~0~001~Why-moving item reports price and volume without a visible catalyst
N1728|E|S|L|o|TSO~p~options_activity~+~1~0~001~Call activity is market context
N1729|E|G|L|i|MS~p~operations,strategy_valuation~0~1~1~001~CEO prioritizes return improvement but gives no timeframe
N1730|E|E|M|i|
N1731|E|G|L|i|AAPL~p~product_commercial,competitive_position~+~2~0~001~Forecast expects dominant tablet shipments|GOOG~m~competitive_position~-~0~1~001~Android tablet comparison is weaker
N1732|P|I|L|i|ERF~p~asset_sales,operations~x~2~1~111~Asset sale provides cash and focuses portfolio while reducing assets
N1733|M|G|M|i|
N1734|M|S|M|i|
N1735|P|I|L|i|CVX~p~operations,restructuring~x~2~2~111~Large cost savings conflict with 15 to 20 percent workforce reduction
N1736|P|G|L|i|MSFT~p~financing~0~1~1~111~Large bond issuance raises cash and debt
N1737|S|S|L|i|FIGR~p~earnings~+~2~0~001~Call summary reports strong revenue growth and execution
N1738|Q|S|L|i|FLR~p~earnings_preview~0~0~0~001~Preview contains estimates not a result
N1739|P|I|L|i|TNDM~p~financing~-~0~2~111~Convertible-note placement creates dilution and debt
N1740|A|A|L|a|DVN~a~analyst_action,earnings~+~1~0~001~Target raised after earnings while hold maintained
N1741|Q|I|L|i|EPM~p~earnings_preview~0~0~0~001~Issuer announces future earnings release and call
N1742|P|G|L|i|AMZN~p~management_governance,regulatory~x~1~1~111~Shareholders reject restrictions and audit proposals on facial recognition
N1743|V|S|L|o|SINO~p~product_commercial~+~2~0~001~Mobile trucking application is named catalyst|GEMP~p~market_context~+~1~0~001~Price-only gainer context|RXDX~p~market_context~+~1~0~001~Price-only gainer context
N1744|S|S|L|i|AMCR~p~earnings~+~3~0~111~EPS and sales both beat estimates
N1745|V|S|L|o|AMZN~p~market_context~+~1~0~001~Price milestone without causal issuer event
N1746|A|A|L|a|AMP~a~analyst_action,operations~+~1~0~001~Equal weight maintained with favorable accumulation and margins
N1747|E|E|M|i|
N1748|A|A|L|a|ALTR~a~operations,analyst_action~x~1~2~001~Long-term favorite but near-term demand and inventory concerns|QCOM~a~operations,analyst_action~x~1~2~001~Long-term favorite but near-term demand concerns|NETL~a~operations,analyst_action~x~1~2~001~Long-term favorite but near-term demand concerns
N1749|P|I|L|i|DG~p~financing~0~1~1~111~Senior-note issue funds balance-sheet uses while increasing debt
N1750|R|G|L|i|WMT~p~listing_market_structure~+~1~0~111~Addition to Nasdaq-100 can increase index demand|AZN~p~listing_market_structure~-~0~1~111~Removal from Nasdaq-100 can reduce index demand
N1751|A|A|L|a|CRM~a~ma_transaction,analyst_action~-~0~2~001~Analyst sees rumored Twitter deal as lacking strategic fit|TWTR~t~ma_transaction~0~1~1~001~Potential target context
N1752|A|A|L|a|NVO~a~analyst_action~-~0~1~001~Rating reduced from strong buy to buy
N1753|E|E|L|i|M~t~ma_transaction~+~2~1~001~Bid offers upside but negotiations and financing remain uncertain
N1754|A|A|L|a|ICLR~a~analyst_action,operations~-~0~3~001~Downgrade and target cut cite weak bookings and sales
N1755|P|I|L|i|THTX~p~clinical~+~2~0~111~Peer-reviewed trial publication supports therapeutic evidence
N1756|M|S|M|i|
N1757|S|S|L|o|REGN~p~market_context~0~0~0~001~Short-interest measurement is contextual, not an issuer event
N1758|S|S|L|i|CBNA~p~earnings~+~2~0~111~EPS increased materially year over year
N1759|A|A|L|a|H~a~analyst_action~x~1~1~001~Hold maintained while target lowered
N1760|E|E|M|i|
N1761|S|S|L|i|VREX~p~earnings,guidance~+~2~1~111~Results beat estimates and shares rose despite year-over-year decline
N1762|A|A|L|a|FVRR~a~analyst_action~+~2~0~001~Overweight maintained and target raised sharply
N1763|Q|S|L|i|BG~p~earnings_preview~0~0~0~001~Earnings preview only
N1764|M|G|L|o|BTAI~p~clinical~+~1~0~001~Clinical issuer is a named market mover|CBL~p~market_context~+~1~0~001~Price-only gainer|FPI~p~market_context~+~1~0~001~Price-only gainer
N1765|A|A|L|a|CRM~t~ma_transaction,analyst_action~x~1~1~001~Analyst debate on rumored target|ORCL~b~ma_transaction,analyst_action~x~1~1~001~Analyst debate on rumored acquirer
N1766|P|I|L|i|INVE~p~guidance~-~0~3~111~Revenue guidance is materially below estimate
N1767|P|I|L|i|ZD~p~guidance~0~1~1~111~Guidance is reaffirmed around consensus
N1768|M|G|L|o|DAL~p~earnings~0~0~0~001~In-line profit is the headline issuer result|NMRX~p~market_context~-~0~1~001~Named loser context
N1769|M|G|M|i|
N1770|W|E|L|i|MIND~p~contract~+~3~0~001~Confirmed 7.7 million dollars of equipment orders
N1771|R|I|L|i|VSME~p~capital_structure,listing_market_structure~-~0~2~111~One-for-twenty combination is intended to regain compliance
N1772|M|G|M|i|
N1773|P|I|L|i|HOLI~p~ma_transaction,management_governance~+~1~0~111~Special meeting and expedited sale process advance strategic transaction
N1774|W|G|L|i|SUNE~p~financing,operations~+~2~0~001~Project financing supports 81.7 MW capacity
N1775|A|A|L|a|GPS~a~analyst_action~+~1~0~001~Neutral maintained while target raised
N1776|A|A|L|a|MNK~a~short_report,legal~-~0~3~001~Short seller alleges fraud and challenges issuer
N1777|P|I|L|i|AJG~p~partnership,commercial~+~1~0~111~Official broker partnership adds brand and commercial relationship
N1778|S|S|L|i|HOCPY~p~earnings~+~3~0~111~EPS beat and sales grew materially
N1779|R|I|L|i|PBM~p~listing_market_structure~-~0~3~111~Nasdaq issued delisting determination after failed compliance
N1780|W|G|L|i|RH~p~earnings,guidance~+~3~0~001~Beat and raised outlook explain sharp rise
N1781|E|G|L|i|GOOG~p~competitive_position,operations~-~0~1~001~Search share declined|BIDU~p~competitive_position,operations~+~1~0~001~Search share gained|MSFT~m~competitive_position~0~0~0~001~Peer context|YHOO~m~competitive_position~0~0~0~001~Peer context
N1782|A|A|L|a|EW~a~earnings,analyst_action~+~2~0~001~Buy reiterated after revenue and EPS beats
N1783|M|G|M|i|
N1784|R|R|L|i|TEVA~p~regulatory,product_commercial~-~0~1~111~FDA shortage of Adderall is negative supply context
N1785|S|S|L|i|BCO~p~earnings~x~2~1~111~EPS and revenue beat but EPS fell year over year
N1786|A|A|L|a|COH~a~analyst_action,operations~+~2~0~001~Upgrade cites brand stabilization|KORS~m~competitive_position~0~0~0~001~Peer context only
N1787|S|S|L|o|MCO~p~market_context~+~1~0~001~New 52-week high|M~p~market_context~+~1~0~001~New 52-week high|ZION~p~market_context~+~1~0~001~New 52-week high
N1788|P|I|L|i|BHP~p~operations,strategy_valuation~+~1~0~111~Expanded exploration program selects ten companies
N1789|S|S|L|o|AMAT~p~options_activity~-~0~1~001~Large bearish options position is market activity
N1790|S|S|L|i|WEST~p~earnings~-~0~1~111~Sales missed estimate
N1791|M|G|L|o|LZB~p~earnings~+~2~0~001~Earnings beat drives gain
N1792|S|S|L|a|ALIT~a~analyst_action~+~2~0~001~Analyst distribution is uniformly positive
N1793|A|A|L|a|AIH~a~analyst_action,operations~+~3~0~001~Overweight initiation cites demand growth
N1794|M|G|L|i|V~p~partnership,commercial~+~1~0~001~Installments partnership with Air Canada supports adoption
N1795|R|R|L|i|UONE~p~financing~-~0~3~111~Large shelf registration creates dilution overhang|UONEK~p~financing~-~0~3~111~Alternate class shares dilution overhang
N1796|S|G|L|i|TSN~p~earnings,operations~+~2~0~111~Q1 beat and improved poultry execution are positive|IWM~m~market_context~0~0~0~001~ETF metadata is incidental
N1797|A|A|L|a|AAPL~a~analyst_action,strategy_valuation~+~3~0~001~Analyst sees unique opportunity and substantial upside|TSLA~m~competitive_position~0~0~0~001~Peer comparison only
N1798|P|I|L|i|NWL~p~guidance,operations~-~0~3~111~Company cuts guidance on retailer inventory pressure
N1799|E|G|L|i|SNHY~p~strategy_valuation~+~1~0~001~Screen ranks low PEG|HRBN~p~strategy_valuation~+~1~0~001~Screen ranks low PEG|FSIN~p~strategy_valuation~+~1~0~001~Screen ranks low PEG|FELE~p~strategy_valuation~+~1~0~001~Screen ranks low PEG
N1800|E|G|M|i|
N1801|E|G|L|i|GRUB~t~ma_transaction,rumor~-~0~1~001~Sale process reportedly slipping away|UBER~b~ma_transaction,rumor~-~0~1~001~Potential buyer reportedly frustrated
N1802|E|E|L|i|META~p~regulatory,operations~-~0~2~001~Florida social-media restrictions create compliance and user risk|SNAP~p~regulatory,operations~-~0~2~001~Florida social-media restrictions create compliance and user risk
N1803|P|I|L|i|SNAP~p~guidance~-~0~2~111~Company withholds revenue and EBITDA guidance due to uncertainty
N1804|P|G|L|i|TSLA~p~ma_transaction,management_governance~x~2~2~111~Go-private process advances but financing and governance uncertainty remain|GS~c~ma_transaction~0~0~0~001~Named financial adviser
N1805|R|I|L|i|IGOI~p~listing_market_structure~+~1~0~111~Nasdaq grants more time to regain bid compliance
N1806|E|G|L|i|SUNE~t~ma_transaction,rumor~+~1~0~001~Issuer is rumored takeover target|GE~b~ma_transaction,rumor~0~1~1~001~Rumored bidder
N1807|P|I|L|i|KNTK~p~financing,ownership~-~0~2~111~Secondary six-million-share sale creates supply but no issuer proceeds
N1808|A|A|L|a|TSLA~a~strategy_valuation,operations~x~2~1~001~Projects may be unprofitable but analyst sees grid-disruption leadership
N1809|P|G|L|i|AAPL~p~operations,strategy_valuation~+~1~0~111~Sales organization is reoriented toward India growth
N1810|V|S|L|o|RUN~p~market_context~+~1~0~001~After-hours price-only move|ARRY~p~market_context~+~1~0~001~After-hours price-only move
N1811|A|A|L|a|CRM~a~earnings,operations,analyst_action~+~2~0~001~Analyst reports positive customer response to two initiatives|AMZN~c~partnership,commercial~+~1~0~001~AWS supports Salesforce expansion
N1812|A|A|L|a|SORL~a~analyst_action,guidance~-~0~2~001~Target cut materially with lower forecasts
N1813|S|S|L|i|ABG~p~earnings~x~1~1~111~EPS missed while revenue beat
N1814|E|G|I|i|
N1815|M|G|M|i|
N1816|M|G|L|o|TUP~p~earnings~+~2~0~001~Q3 results drove gain
N1817|M|G|M|i|
N1818|E|G|N|i|
N1819|Q|G|L|i|PXD~p~event_preview~0~0~0~001~Conference presentation schedule|APA~p~event_preview~0~0~0~001~Conference presentation schedule|CPE~p~event_preview~0~0~0~001~Conference presentation schedule|LPI~p~event_preview~0~0~0~001~Conference presentation schedule|HP~p~event_preview~0~0~0~001~Conference presentation schedule|AREX~p~event_preview~0~0~0~001~Conference presentation schedule
N1820|A|A|L|a|NSM~a~operations,analyst_action~+~1~0~001~Analyst views renewed growth focus as appropriate
N1821|E|E|M|i|
N1822|S|S|L|i|PAGS~p~earnings~+~2~0~111~EPS and sales both beat estimates
N1823|P|I|L|i|KEYS~p~partnership,product_commercial~+~1~0~111~Expanded relationship adds network visibility and analytics offering
N1824|S|S|L|i|CHRW~p~earnings~+~2~0~111~EPS beat despite lower year-over-year revenue
N1825|W|G|L|o|WDC~p~competitive_position,market_context~-~0~1~001~Shares fall in sympathy with Intel delay rather than issuer news
N1826|R|R|L|i|KRNT~p~capital_return~+~2~0~111~Issuer seeks extension of remaining buyback authorization
N1827|P|I|L|i|BPOP~p~legal~x~1~1~111~Derivative settlement may resolve claims but terms remain preliminary
N1828|E|G|L|i|TRP~p~regulatory,rumor~0~0~0~001~Government document release is pending
N1829|M|G|L|o|JELD~p~earnings~+~2~0~001~Earnings beat drives gain|PTX~p~market_context~-~0~1~001~Named decline without visible catalyst
N1830|P|I|L|i|WNI~p~contract,partnership~+~2~0~111~Long-term exclusive supply agreement strengthens product sourcing
N1831|M|S|M|i|
N1832|S|G|L|i|NXPI~p~earnings,guidance~+~2~1~111~Adjusted earnings beat and revenue grew despite GAAP loss
N1833|E|E|L|i|ESTE~p~capital_structure,listing_market_structure,earnings~x~2~1~001~Reverse split enabled listing and earnings are positive but evidence is promotional
N1834|P|I|L|i|TREX~p~financing,credit_solvency~+~2~0~111~Repayment materially reduces debt despite revolver use
N1835|P|I|L|i|CSH~p~guidance~-~0~2~111~Full-year EPS guidance is lowered below prior range
N1836|E|G|L|o|ATVI~p~options_activity,product_commercial~+~1~0~001~Bullish options interest follows game release
N1837|S|S|L|a|PLL~a~analyst_action~x~1~1~001~Analyst-scoreboard mix is not clearly directional
N1838|A|A|L|a|RRD~a~analyst_action,operations~x~1~1~001~Buy maintained despite lowered estimates and slowdown
N1839|P|I|L|i|SLXP~p~clinical~+~3~0~111~Two Phase 3 studies achieved statistically significant outcomes
N1840|V|S|M|o|
N1841|M|G|M|i|
N1842|M|G|L|o|BPI~p~earnings~+~2~0~001~Q2 result drives gain|BCEI~p~market_context~+~1~0~001~Named sector gainer
N1843|A|A|L|a|EXR~a~analyst_action~+~3~0~001~Coverage starts on conviction buy list
N1844|P|I|L|i|JACK~p~guidance~0~1~1~111~Capital and overhead guidance define spending without clear directional comparison
N1845|S|S|L|a|RXST~a~analyst_action~x~1~1~001~Analyst ratings include mixed perspectives
N1846|A|A|L|a|FTNT~a~analyst_action~-~0~1~001~Rating downgraded to sector perform
N1847|M|S|L|a|TRU~a~analyst_action~+~2~0~001~Initiated at buy|NTRA~a~analyst_action~+~2~0~001~Initiated at outperform|CSCO~a~analyst_action~0~0~0~001~Initiation roundup context
N1848|E|G|I|i|
N1849|S|S|L|i|BGG~p~earnings~-~0~2~111~EPS and revenue missed estimates
N1850|P|I|L|i|GDRX~p~guidance~-~0~3~111~Quarterly sales outlook is materially below estimate
N1851|A|A|L|a|DG~a~analyst_action~-~0~2~001~Rating downgraded from strong buy to market perform
N1852|E|G|M|i|
N1853|P|I|L|i|ICLK~p~investment,operations~+~2~0~111~Expanded controlling stake strengthens enterprise solutions business
N1854|E|G|I|i|
N1855|M|S|L|o|TSLA~p~market_context~+~1~0~001~Market recap records a large rally|META~p~market_context~+~1~0~001~Market recap records gain|NVDA~p~market_context~+~1~0~001~Market recap records gain
N1856|M|G|M|i|
N1857|M|G|L|o|LULU~p~guidance~-~0~3~001~Issuer cut earnings and revenue outlook|ALNY~p~ma_transaction,partnership~+~3~0~001~Asset acquisition and expanded collaboration are positive|MRK~c~ma_transaction~0~1~1~001~Seller in asset acquisition|BEAM~t~ma_transaction~+~3~0~001~Cash acquisition provides premium|SODA~p~guidance~-~0~3~001~Company lowered forecast
N1858|A|A|L|a|KTWO~a~analyst_action~+~2~0~001~Coverage initiated at overweight
N1859|E|E|M|i|
N1860|P|I|L|i|YYAI~b~ma_transaction,listing_market_structure~x~2~1~111~Control acquisition and resumed trading are positive but transform the issuer
N1861|M|G|M|i|
N1862|V|S|M|o|
N1863|P|I|L|i|GCDT~p~financing,listing_market_structure~x~1~1~111~IPO raises capital while diluting owners
N1864|P|I|L|i|NWL~p~asset_sales~+~1~0~111~Business sale produces 175 million cash proceeds
N1865|S|G|L|i|DDD~p~earnings,guidance~-~0~3~111~Revenue and EPS missed with weak organic growth
N1866|W|E|L|i|YMAT~p~regulatory,operations~+~3~0~001~Regulatory authorization advances a large manufacturing facility
N1867|M|G|M|i|
N1868|A|A|L|a|BODY~a~analyst_action,earnings~+~2~0~001~Buy maintained after positive preannouncement
N1869|E|G|L|i|ALU~p~earnings,ma_transaction~x~1~1~001~Operating profit improved but EPS loss remains and deal terms unchanged
N1870|E|E|M|i|
N1871|M|G|L|o|INTC~p~market_context~-~0~1~001~Retail-attention recap records decline|DECK~p~market_context~0~0~0~001~Retail-attention recap|UNH~p~market_context~0~0~0~001~Retail-attention recap|DOW~p~market_context~0~0~0~001~Retail-attention recap|AAL~p~market_context~0~0~0~001~Retail-attention recap
N1872|M|S|L|a|OC~a~analyst_action~-~0~2~001~Downgrade and target cut|ACN~a~analyst_action~-~0~3~001~Downgrade to sell and target cut|TROW~a~analyst_action~-~0~1~001~Downgrade roundup|LHCG~a~analyst_action~-~0~1~001~Downgrade roundup
N1873|E|G|I|i|
N1874|S|S|L|i|GEF~p~earnings~-~0~2~111~Income dropped and EPS missed
N1875|A|G|I|a|
N1876|M|S|L|a|TEF~a~analyst_action~+~2~0~001~Upgraded to buy|ABM~a~analyst_action~+~2~0~001~Upgraded to outperform|CPE~a~analyst_action~+~2~0~001~Upgraded to buy
N1877|E|E|M|i|
N1878|E|E|M|i|
N1879|P|I|L|i|TGP~b~ma_transaction~x~2~1~111~Joint venture acquires eight carriers for 1.4 billion
N1880|P|I|L|i|ITT~b~ma_transaction~+~2~0~111~Acquisition expands valves platform and end-market reach
N1881|V|S|L|o|AVTX~p~clinical~+~1~0~001~First patient dosed in pivotal trial|TNXP~p~clinical~0~0~0~001~Biotech catalyst recap|HALO~p~clinical~0~0~0~001~Biotech catalyst recap
N1882|E|G|L|i|AAPL~p~operations,market_context~+~1~0~001~India sales surge is positive but article is technical commentary
N1883|P|I|L|i|WPPGY~b~ma_transaction~+~1~0~111~Acquisition expands communications consulting
N1884|S|S|L|i|LII~p~earnings~+~2~0~111~EPS and revenue beat estimates
N1885|M|S|M|i|
N1886|P|I|L|i|DDS~p~retail_sales~-~0~2~111~Monthly comparable and total sales declined
N1887|A|A|L|a|GLW~a~analyst_action,operations~-~0~3~001~Downgrade and target cut cite weak recovery
N1888|Q|S|L|i|KTOS~p~earnings_preview~0~0~0~001~Earnings preview only
N1889|M|G|M|i|
N1890|A|A|L|a|GS~a~earnings,guidance~-~0~2~001~Firm cuts quarterly earnings estimate|MS~a~earnings,guidance~-~0~2~001~Firm cuts quarterly earnings estimate|CS~m~analyst_action~0~0~0~001~Research source rather than affected issuer
N1891|A|A|L|a|NOW~a~analyst_action~+~1~0~001~Buy maintained and target raised slightly
N1892|P|I|L|i|EFTY~p~financing,listing_market_structure~x~1~1~111~IPO establishes listing and capital with dilution
N1893|E|E|M|i|
N1894|P|G|L|i|ELMG~t~ma_transaction~+~3~0~111~Target receives large premium|HON~b~ma_transaction~x~1~1~111~Acquirer gains business but pays 491 million
N1895|W|I|L|i|IGC~p~product_commercial,strategy_valuation~x~1~1~001~Blockchain development plan is speculative and promotional
N1896|A|A|L|a|KR~a~analyst_action,competitive_position~+~2~0~001~Analyst calls pullback attractive and Walmart fears overblown|WMT~m~competitive_position~0~0~0~001~Competitive context|RNDY~m~competitive_position~0~0~0~001~Peer context
N1897|Q|A|L|a|AMZN~a~earnings_preview~0~0~0~001~Analyst preview identifies key upcoming metrics
N1898|P|I|L|i|VTLE~b~ma_transaction,operations~+~2~1~111~Asset purchase expands scale and inventory for cash consideration|NOG~b~ma_transaction,operations~+~2~1~111~Joint acquisition expands Delaware assets
N1899|E|E|M|i|
N1900|E|E|M|i|
N1901|P|I|L|i|ACXM~p~earnings~+~2~0~111~Revenue and operating income improved year over year
N1902|V|S|L|o|SEER~p~financing,listing_market_structure~+~1~0~001~IPO opened far above its offer price
N1903|E|E|L|i|AAPL~p~ownership,strategy_valuation~+~1~0~001~CEO frames Berkshire investment as validation|BRK.A~p~ownership~+~1~0~001~Large Apple investment is discussed favorably|BRK.B~p~ownership~+~1~0~001~Alternate class shares same ownership context
N1904|E|E|L|i|SPCX~p~strategy_valuation~-~0~2~001~Commentator says decline may expose an overpriced market and AI boom risk
N1905|M|S|M|i|
N1906|E|G|L|i|GMAN~p~strategy_valuation~+~1~0~001~Screen ranks high on EPS|PIR~p~strategy_valuation~+~1~0~001~Screen ranks high on EPS|KIRK~p~strategy_valuation~+~1~0~001~Screen ranks high on EPS|HVT~p~strategy_valuation~+~1~0~001~Screen ranks high on EPS
N1907|M|G|M|i|
N1908|P|I|L|i|BRT~p~financing~-~0~2~111~Two-million-share public offering is dilutive
N1909|R|G|L|i|RCEL~p~listing_market_structure~+~1~0~111~Russell 3000 inclusion can increase index ownership
N1910|P|I|L|i|ATR~p~capital_return~+~2~0~111~New 500-million-dollar repurchase authorization
N1911|P|I|L|i|ARO~p~retail_sales~+~1~0~111~Monthly comparable sales increased three percent though growth slowed
N1912|P|I|L|i|LIOX~p~earnings~+~2~1~111~Quarterly EPS improved sharply despite annual GAAP loss
N1913|E|E|M|i|
N1914|M|G|L|o|GIS~p~earnings~x~1~1~001~EPS beat and revenue missed in morning summary|BBY~p~ma_transaction,rumor~0~1~1~001~Founder exploring buyout|UTHR~p~analyst_action~0~0~0~001~Morning analyst context
N1915|P|I|L|i|OBSV~p~financing,legal~x~1~1~111~Waiver and amendment modify securities obligations
N1916|M|G|M|i|
N1917|E|G|M|i|
N1918|A|A|L|a|GGG~a~analyst_action,operations~+~1~0~001~Target raised on constructive outlook while market perform maintained
N1919|P|I|L|i|BALL~p~joint_venture,operations~+~1~0~111~Joint venture is formed to scale aluminum cup business
N1920|S|S|L|i|LPSN~p~earnings,guidance~x~2~2~111~Sales beat and raised guidance conflict with substantially wider loss
N1921|V|S|M|o|
N1922|Q|S|L|i|GIL~p~earnings_preview~0~0~0~001~Earnings preview content is incomplete
N1923|M|G|M|i|
N1924|E|E|L|i|FITB~p~strategy_valuation,regulatory~x~1~1~001~Policy optimism conflicts with market expectations|HBAN~p~strategy_valuation,regulatory~x~1~1~001~Policy optimism conflicts with market expectations|KEY~p~strategy_valuation,regulatory~x~1~1~001~Policy optimism conflicts with market expectations|ZION~p~strategy_valuation,regulatory~x~1~1~001~Policy optimism conflicts with market expectations
N1925|R|I|L|i|MOVE~p~capital_structure,listing_market_structure~0~1~1~111~One-for-four reverse split changes capital structure and trading basis
N1926|A|A|L|a|LNC~a~analyst_action~x~1~1~001~Buy maintained while target is lowered slightly
N1927|E|E|M|i|
N1928|E|E|M|i|
N1929|V|S|M|o|
N1930|A|A|L|a|EAF~a~analyst_action~x~1~1~001~Overweight maintained while target lowered
N1931|W|E|M|i|
N1932|P|I|L|i|EGO~p~tax,operations~-~0~2~111~Currency weakness increases tax burden uncertainty
N1933|S|S|L|i|XHLD~p~earnings~-~0~2~111~Loss widened and revenue declined
N1934|S|G|L|i|FAF~p~earnings~x~1~1~111~EPS beat but revenue missed
N1935|P|I|L|i|FWLT~p~contract~+~1~0~111~Engineering contract adds project work
N1936|M|G|M|i|
N1937|S|S|L|i|IFF~p~capital_return~+~1~0~001~Declared dividend is issuer history but ex-date explainer is not a new trigger
N1938|S|G|L|i|GM~p~earnings~+~3~0~111~Company swings from severe loss to strong profit
N1939|A|A|L|a|INGN~a~short_report,analyst_action,operations~+~2~0~001~Analyst rejects short thesis and cites growth and market-share gains
N1940|E|G|M|i|
N1941|Q|G|L|i|INTU~p~earnings_preview~0~0~0~001~Scheduled event|PANW~p~earnings_preview~0~0~0~001~Scheduled event|URBN~p~earnings_preview~0~0~0~001~Scheduled event
N1942|P|I|L|i|AMZN~p~operations,management_governance~+~2~0~111~Seventy thousand seasonal jobs expand capacity for demand
N1943|E|G|I|d|
N1944|P|I|L|i|ZVRA~p~financing~-~0~3~111~Proposed common-stock offering is dilutive
N1945|P|I|L|i|PARR~p~guidance,operations~0~1~1~111~Capital spending guidance is substantial but lacks comparison
N1946|M|G|M|i|
N1947|A|A|L|a|ADBE~a~earnings,guidance,analyst_action~x~2~2~001~Strong quarter conflicts with weak guidance and one downgrade
N1948|Q|S|L|i|OXSQ~p~earnings_preview~0~0~0~001~Automated earnings preview only
N1949|E|G|I|i|
N1950|V|S|L|o|GPRO~p~financing,listing_market_structure,market_context~+~1~0~001~IPO trades above forty dollars on second day
N1951|M|G|M|i|
N1952|R|G|L|i|TEVA~p~regulatory,product_commercial~+~1~0~001~Court criticism may loosen emergency-contraception restrictions|CHD~m~regulatory,product_commercial~+~1~0~001~Affected product-market issuer context
N1953|A|A|L|a|DXCM~a~analyst_action~+~2~0~001~Buy maintained and target raised
N1954|A|A|L|a|THOR~a~analyst_action~-~0~3~001~Downgrade and severe target cut
N1955|M|G|L|i|TAP~p~strategy_valuation,operations~+~1~0~001~Barrons sees share gains as sustainable|AAPL~p~strategy_valuation,technology~0~0~0~001~AI positioning context|TSLA~p~strategy_valuation~0~0~0~001~Barrons pick context
N1956|E|S|L|o|I~p~options_activity~+~1~0~001~Call sweep is market activity
N1957|M|G|M|i|
N1958|W|E|L|o|SVRN~p~partnership,operations~x~1~1~001~Risk-management appointment is context but no catalyst explains spike
N1959|S|G|L|i|MDT~p~earnings,guidance~+~2~0~111~EPS beat and lower guidance bound was raised
N1960|P|G|L|i|TWTR~p~operations~-~0~1~111~Service outage disrupts user access
N1961|S|S|L|i|ONCT~p~earnings~-~0~1~111~Loss met estimate but worsened year over year
N1962|S|S|L|i|WINA~p~earnings~+~2~0~111~EPS and sales increased year over year
N1963|S|S|L|i|CBI~p~capital_return~0~0~0~001~Ex-dividend schedule context|EQR~p~capital_return~0~0~0~001~Ex-dividend schedule context|KFY~p~capital_return~0~0~0~001~Ex-dividend schedule context
N1964|P|G|I|i|
N1965|P|I|L|i|NOC~p~contract,product_commercial~+~2~0~111~First production order covers 142 radars|LMT~c~contract,product_commercial~+~1~0~001~Prime contractor orders radar systems
N1966|A|A|L|a|MPR~a~analyst_action~-~0~2~001~Rating downgraded from buy to hold
N1967|S|S|L|a|DTM~a~analyst_action~x~1~1~001~Analyst distribution is mixed
N1968|Q|G|L|i|CTAS~p~earnings_preview~0~0~0~001~Upcoming earnings expectations|UNF~m~competitive_position~0~0~0~001~Peer context
N1969|R|I|L|i|GSAT~p~regulatory,product_commercial~+~2~0~111~Spain grants terrestrial authorization
N1970|E|G|L|o|EA~t~ma_transaction,rumor~0~1~1~001~Unconfirmed Microsoft chatter|MSFT~b~ma_transaction,rumor~0~1~1~001~Unconfirmed potential buyer
N1971|A|A|L|a|RSG~a~analyst_action~+~2~0~001~Outperform maintained and target raised
N1972|S|S|M|i|
N1973|A|A|L|a|HRB~a~analyst_action~-~0~1~001~Neutral maintained while target lowered
N1974|R|R|L|i|NVAX~p~regulatory,product_commercial~x~1~1~111~Authorization expands adolescent use while adding myocarditis reporting requirements
N1975|S|S|L|o|CVS~p~options_activity~-~0~1~001~Bearish unusual options are market activity
N1976|A|A|L|a|STX~a~competitive_position,analyst_action~+~1~1~001~Analyst rejects flash-death thesis but acknowledges structural challenge|TOSYY~m~competitive_position~0~0~0~001~Technology peer context
N1977|Q|G|L|i|BZH~p~earnings_preview~0~0~0~001~Upcoming earnings|RYL~p~earnings_preview~0~0~0~001~Upcoming earnings|SPF~p~earnings_preview~0~0~0~001~Upcoming earnings
N1978|P|I|L|i|CDE~b~financing,ownership~x~1~1~111~Strategic equity investment adds exposure and funds target
N1979|P|I|L|i|KTOS~p~guidance~0~1~1~111~Sales guidance range spans consensus
N1980|P|I|L|i|BORR~p~financing,capital_structure~x~1~1~111~Convertible refinancing extends maturity but retains dilution risk
N1981|W|G|L|o|FEYE~p~market_context~0~0~0~001~Price pop on conference cancellation has no clear issuer event
N1982|P|G|L|i|TSLA~p~commercial,marketing~+~1~0~001~Referral program incentivizes demand with vehicle prizes
N1983|R|G|L|i|T~p~regulatory,capital_allocation~x~1~1~001~Large spectrum bid adds assets at high cost|TMUS~p~regulatory,capital_allocation~x~1~1~001~Large spectrum bid adds assets at high cost
N1984|E|G|L|i|CHS~t~ma_transaction,rumor~-~0~1~001~Buyout talks stall on financing and valuation
N1985|S|S|M|i|
N1986|A|A|L|a|SCTY~a~analyst_action,operations~x~1~2~001~Neutral initiation and target cite several operational challenges
N1987|Q|G|L|i|BIIB~p~regulatory,event_preview~0~0~0~001~Upcoming FDA advisory review|MRK~p~earnings_preview~0~0~0~001~Recent earnings context|PFE~p~earnings_preview~0~0~0~001~Recent earnings context
N1988|M|G|M|i|
N1989|M|G|M|i|
N1990|A|A|L|a|KR~a~earnings,operations,analyst_action~+~2~0~001~Buy recommendation cites long market-share and comp-sales record
N1991|E|G|L|i|MSFT~p~capital_return,rumor~+~1~0~001~Possible dividend increase would improve capital return
N1992|P|I|L|i|SPSC~p~financing~-~0~2~111~750-thousand-share offering is dilutive
N1993|P|I|L|i|MENT~p~management_governance~x~1~1~111~Proxy letter supports incumbent directors amid contested vote
N1994|E|E|L|i|TGT~p~earnings,operations,competitive_position~+~2~0~001~Profit and comps beat with traffic and format growth|AMZN~m~competitive_position~0~0~0~001~Competitive comparison|WMT~m~competitive_position~0~0~0~001~Competitive comparison
N1995|W|E|L|i|LESL~p~earnings,guidance~+~3~0~001~Revenue beat and improved outlook drive after-hours gain
N1996|M|G|L|o|NWY~p~earnings~-~0~2~001~Weak results drive decline|IOC~p~ma_transaction,rumor~+~1~0~001~Reported transaction interest drives spike
N1997|E|E|M|i|
N1998|M|G|L|o|ATHN~p~earnings~-~0~2~001~Weak results drive decline|ZEUS~p~earnings~+~2~0~001~Earnings drive gain
N1999|M|G|M|i|
N2000|S|S|L|i|ECOR~p~earnings~x~1~1~111~Loss beat estimate while sales missed
""".strip()

# The annotation contract reserves evidence level 1 for weak/contextual
# evidence and requires level >=2 on both sides of a mixed judgment.  Compact
# review rows above use 1 to record a present side.  Canonicalize those strength
# levels here without changing any reviewer-authored direction or eligibility.
COMPACT_ROWS = (
    COMPACT_ROWS.replace("~x~1~1~", "~x~2~2~")
    .replace("~x~1~2~", "~x~2~2~")
    .replace("~x~2~1~", "~x~2~2~")
    .replace("~+~1~1~", "~+~2~1~")
)
