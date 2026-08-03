from __future__ import annotations

from typing import Any


REVIEW_CONTRACT = "news_fresh_acceptance_v4_manual_audit_review_v2"

# Reviewer-authored evidence only.  Entries are added after the primary agent
# reads the exact provider metadata, complete original source text, current gold
# labels, V9 output, and persisted rule trace.  These records are not inference
# rules and must never be imported by the classifier.
REVIEWS: dict[str, dict[str, Any]] = {
    "N1301": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_identity_false_negative"],
        "notes": (
            "Title-only issuer offering: Golub Capital priced 6M shares at $17.47. "
            "Gold GBDC financing/negative/trigger eligibility is supported. V9 missed GBDC because "
            "the title uses the safe shortened issuer name Golub Capital while authority aliases include BDC."
        ),
        "generic_fix": "Resolve safely shortened legal names after removing business-form tokens, with ambiguity and event gates.",
    },
    "N1302": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_extraction_taxonomy"],
        "notes": (
            "IMF macro outlook concerns countries and the global economy; provider VT is not a semantic issuer. "
            "Gold non_issuer_market_content, editorial analysis/original is supported. V9 correctly emitted no issuer "
            "but collapsed the extraction decision to no_supported_event."
        ),
        "generic_fix": "Distinguish non-issuer market content from issuer text that lacks a supported event.",
    },
    "N1303": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_clinical_direction_false_neutral", "v9_trigger_false_negative"],
        "notes": (
            "Merck Phase 3 results report 95% SVR12, explicitly described as virologic cure, with 97% adherence. "
            "Positive clinical direction and primary-event trigger eligibility are supported. V9 found MRK/clinical "
            "but assigned no outcome evidence and suppressed forecast/reaction eligibility."
        ),
        "generic_fix": "Recognize explicit successful clinical endpoint/result language and numeric efficacy outcomes.",
    },
    "N1304": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_boilerplate_scope", "v9_analyst_summary_direction"],
        "notes": (
            "Automated WWE analyst summary contains six bullish/somewhat-bullish, two indifferent and one somewhat "
            "bearish rating; average target rose 17.63%. Positive contextual analyst direction is supported. V9's "
            "negative financing signal and earnings/guidance concepts come from generic analyst-methodology boilerplate."
        ),
        "generic_fix": "Exclude analyst methodology boilerplate and calculate automated rating-summary direction from the article table.",
    },
    "N1305": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": [
            "gold_missing_relational_issuer",
            "v9_ma_role_inversion",
            "v9_recap_scope",
            "v9_market_reaction_concept_leak",
        ],
        "notes": (
            "Morning mover recap has eleven issuer catalyst passages. Existing gold correctly marks them contextual, "
            "but omits DRAD even though DRAD is the named bidder in an unsolicited proposal for PDII. PDII is the target "
            "and DRAD the acquirer; V9 reverses those roles. Several issuer passages lose event/direction evidence, while "
            "price-move wording leaks into event concepts as market_reaction."
        ),
        "gold_correction": "Add DRAD as a neutral acquisition-bidder history unit; retain all recap units as non-triggering.",
        "generic_fix": (
            "Parse received-proposal-from relations into target/acquirer roles; scope every recap sentence independently; "
            "keep observed price reaction separate from causal event concepts."
        ),
    },
    "N1306": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_automated_summary_suppression"],
        "notes": (
            "Automated HAFC earnings recap reports a 50.88% earnings beat and revenue up $9.744M year over year. "
            "Gold positive earnings history, non-triggering recap status is supported. V9 resolves HAFC but emits no unit."
        ),
        "generic_fix": "Emit issuer-history units from substantive automated recaps while keeping forecast/reaction ineligible.",
    },
    "N1307": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": [
            "gold_missing_context_issuer",
            "gold_incomplete_concepts",
            "source_lane_quality_defect",
            "v9_origin_mismatch",
            "v9_analyst_context_scope",
        ],
        "notes": (
            "Syndicated Zacks analysis is negative for NPSP (earnings/revenue miss and guidance cut), positive analyst "
            "context for ALXN/GILD, and explicitly discusses AMGN royalty-product context. Existing gold omits AMGN and "
            "omits product/regulatory context for NPSP. SBS is the medical abbreviation short bowel syndrome, not an issuer. "
            "The external lane is repeated anti-bot transport text and must be rejected. V9 trace recognizes syndicated "
            "Zacks provenance but returned editorial_original and misses Strong Buy direction for ALXN/GILD."
        ),
        "gold_correction": (
            "Add AMGN as neutral issuer-history product/earnings context; add product_commercial and regulatory to NPSP; "
            "retain ALXN/GILD analyst-action context and exclude SBS."
        ),
        "generic_fix": (
            "Reject anti-bot lanes; honor syndicated-origin evidence; parse ranked analyst recommendations; "
            "disambiguate uppercase domain abbreviations from securities using exchange-qualified identity."
        ),
    },
    "N1308": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_brand_identity_missing", "v9_context_unit_suppression", "v9_origin_mismatch"],
        "notes": (
            "Title-only Piper Jaffray update conveys reassuring Tivity management commentary and identifies "
            "UnitedHealthcare's new fitness benefit. Gold TVTY positive analyst context and UNH neutral commercial "
            "counterparty context is supported. V9 resolves TVTY but suppresses all units and does not resolve the "
            "UnitedHealthcare operating brand to UNH; persisted analyst-origin evidence disagrees with returned origin."
        ),
        "generic_fix": (
            "Add point-in-time operating-brand aliases, emit contextual analyst units from substantive titles, and make "
            "returned provenance derive from the same evidence authority shown in the trace."
        ),
    },
    "N1309": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_symbol_acronym_false_positive"],
        "notes": (
            "SPI Solar/SOPW is the sole issuer and contract beneficiary. SEF means solar energy facility throughout, "
            "despite erroneous provider ticker metadata. Gold SOPW positive contract trigger and SEF exclusion are supported."
        ),
        "generic_fix": (
            "Do not resolve bare uppercase acronyms as securities when local syntax defines them as a common noun; require "
            "exchange-qualified symbol, issuer alias, or strong identity evidence."
        ),
    },
    "N1310": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_financial_term_polysemy"],
        "notes": (
            "HTGC priced a 3.1M-share public offering. Gold financing/negative trigger labels and all eligibility fields "
            "are supported. V9 adds earnings only because 'last reported sales price' is misread as operating sales."
        ),
        "generic_fix": "Disambiguate stock sale/market price phrases from revenue or earnings evidence.",
    },
    "N1311": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_recap_scope", "v9_market_reaction_concept_leak"],
        "notes": (
            "Premarket recap has substantive catalysts for ULTA (beat plus weak outlook), TRVN (common-stock offering), "
            "and GPS (same-store sales gain); VOD is price-only and correctly excluded. Gold contextual units are supported. "
            "V9 drops TRVN, misses GPS positive direction, and adds observed market_reaction to event concepts."
        ),
        "generic_fix": "Scope each mover sentence independently and separate observed price action from event concepts.",
    },
    "N1312": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_relation_scope", "v9_identity_false_negative", "v9_primary_event_role"],
        "notes": (
            "Reuters title reports Intel CEO's current foundry-services update: AMZN, CSCO, MSFT and QCOM support Intel's "
            "effort. INTC is the positive primary subject; the four named firms are neutral counterparties. Gold labels are "
            "supported. V9 misses CSCO, treats the list as shared ambiguous subjects, loses the commercial concept and "
            "downgrades the current operating event to editorial analysis."
        ),
        "generic_fix": (
            "Parse subject-predicate-object relations in titles, resolve company lists via identity authority, and classify "
            "reported current issuer operating events independently from source provenance."
        ),
    },
    "N1313": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_short_name_identity_false_negative", "v9_analyst_unit_suppression"],
        "notes": (
            "Evercore initiates Lyft with Outperform and explicit secular-upside rationale. Gold positive analyst-action "
            "history, non-triggering analyst status is supported. V9 recognizes the analyst article but resolves no issuer."
        ),
        "generic_fix": "Allow exact provider-backed short issuer names in explicit analyst-action syntax under point-in-time identity checks.",
    },
    "N1314": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_hidden_identity_false_positive"],
        "notes": (
            "Historical Bitcoin/Coinbase commentary is correctly labeled neutral, contextual COIN editorial analysis. "
            "V9 matches scored fields but its identity trace spuriously resolves BTCFF from the generic phrase 'Bitcoin "
            "Treasury Companies'; AXP provider metadata is irrelevant and correctly not emitted."
        ),
        "generic_fix": "Reject generic compound phrases as issuer aliases unless syntax or provider evidence identifies the security.",
    },
    "N1315": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_roundup_concept_scope", "v9_market_reaction_concept_leak", "v9_hidden_identity_ambiguity"],
        "notes": (
            "Market roundup contains Fiat/Chrysler stake acquisition, ZQK and PAY earnings, Sony data breach, and TAP "
            "possible bid. Gold contextual directions and concepts are supported. V9 misses M&A for FIATY/TAP and cyber/legal "
            "harm for SNE, adds market_reaction to ZQK, and its trace resolves MCO from Moody's even though the issuer is not "
            "a semantic subject of the article."
        ),
        "generic_fix": (
            "Expand sentence-scoped M&A and cyber-event grammar, separate price observations, and require issuer-subject "
            "syntax before emitting or retaining identity candidates from organization names."
        ),
    },
    "N1316": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_followup_role", "v9_origin_mismatch"],
        "notes": (
            "Title says shares are up after First Data earlier reported a Q1 beat and raised FY guidance. Gold correctly "
            "treats this as an aggregated why-moving follow-up with positive contextual earnings/guidance, not a new trigger."
        ),
        "generic_fix": "Give explicit earlier/after price-reaction syntax precedence as why-moving follow-up and aggregation provenance.",
    },
    "N1317": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_extraction_taxonomy"],
        "notes": "Brazilian real exchange-rate movement is non-issuer market content; provider EWZ is only a weak proxy link.",
        "generic_fix": "Distinguish non-issuer market observations from issuer text lacking a supported event.",
    },
    "N1318": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_cross_issuer_scope", "v9_market_reaction_concept_leak"],
        "notes": (
            "Editorial analysis attributes NVDA upside forecast to Gene Munster and separately reports TSMC earnings/guidance. "
            "Gold keeps NVDA analyst_action and TSM earnings/guidance contextual. V9 leaks TSM results, observed price action, "
            "and general product discussion into NVDA concepts."
        ),
        "generic_fix": "Enforce issuer-bounded paragraphs/quotes and keep observed market reaction outside event concepts.",
    },
    "N1319": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_ma_role_error", "v9_roundup_concept_scope", "v9_market_reaction_concept_leak"],
        "notes": (
            "Market roundup contains WMT beat/raise, SGH/RH guidance raises, NTEC loss, DPLO purchase plus downgrade, and AXGN "
            "offering. Existing DPLO direction/concepts are reasonable but its issuer role is wrong: Diplomat agreed to buy LDI, "
            "so DPLO is acquirer, not target. V9 misses DPLO M&A, misses RH positive outlook direction, treats SGH guidance EPS "
            "as realized earnings, and adds market_reaction to NTEC."
        ),
        "gold_correction": "Change DPLO issuer role from target to acquirer.",
        "generic_fix": "Parse active M&A voice, distinguish guidance metrics from realized results, and separate price observations.",
    },
    "N1320": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_roundup_direction", "v9_roundup_concept_scope"],
        "notes": (
            "Morning roundup has mixed TOL/HRL results, BIG/TSL misses, Ford credit upgrade, analyst actions, RAH acquisition "
            "with expected accretion, and MET strategic ROE target. Gold units are complete and contextual. V9 misses several "
            "comparator-driven directions and the Ford, RAH, and MET concepts."
        ),
        "generic_fix": (
            "Parse compact estimate-vs-actual rows, credit-rating actions, acquisition/accretion clauses, and issuer outlook "
            "targets within independently scoped roundup bullets."
        ),
    },
    "N1321": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_mixed_horizon_direction", "v9_origin_mismatch", "v9_hypothetical_concept"],
        "notes": (
            "CLSA analyst discusses Morgan Stanley's quarterly miss and weak trading alongside strong annual growth and a "
            "13% forward revenue expectation. Gold mixed analyst/earnings context is supported. V9 keeps only the negative "
            "quarter, calls the interview editorial-original, and turns hypothetical industry restructuring discussion into operations."
        ),
        "generic_fix": "Combine current and explicit forward analyst evidence, preserve analyst provenance, and reject hypothetical concepts.",
    },
    "N1323": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_options_concept", "v9_boilerplate_scope"],
        "notes": (
            "Automated UNH article is primarily unusual options activity with evenly split bullish/bearish sentiment; analyst "
            "ratings are secondary context and the next-earnings date is boilerplate, not an earnings event. Existing gold "
            "omits options_activity. V9 adds earnings from the calendar sentence."
        ),
        "gold_correction": "Add options_activity to UNH concepts; retain mixed direction and non-triggering history eligibility.",
        "generic_fix": "Recognize options-flow as its own concept and exclude calendar/educational boilerplate from event concepts.",
    },
    "N1324": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Cattle futures settlement and USDA slaughter statistics are correctly treated as non-issuer commodity content.",
        "generic_fix": "none",
    },
    "N1325": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_extraction_taxonomy"],
        "notes": (
            "Reuters hurricane-landfall alert is non-issuer weather/market context. Provider SPY/UNG/USO links are proxies, "
            "not semantic issuer subjects. Gold is supported; V9 emits no issuer but uses no_supported_event."
        ),
        "generic_fix": "Distinguish non-issuer macro/weather content from issuer text lacking a supported event.",
    },
    "N1322": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_long_document_scope"],
        "notes": (
            "Full FISI transcript reports improving earnings, $65M debt refinancing, repurchases, dividend increase, "
            "favorable NIM/efficiency guidance, and completed BaaS wind-down/branch consolidation. Gold positive automated "
            "summary status is supported but concepts omit financing and operations. V9 scans the whole transcript without "
            "section priority and invents clinical, contract, product and regulatory concepts from generic language."
        ),
        "gold_correction": "Add financing and operations to FISI concepts; retain non-triggering automated-summary eligibility.",
        "generic_fix": (
            "Use summary/prepared-results sections as high-authority evidence for long transcripts and require event-local "
            "syntax before accepting concepts from Q&A, disclaimers, or generic banking vocabulary."
        ),
    },
    "N1326": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Direct Sidoti upgrade of DCO from Neutral to Buy is correctly labeled positive analyst context and non-triggering.",
        "generic_fix": "none",
    },
    "N1327": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "v9_mixed_metric_direction", "v9_trigger_false_negative"],
        "notes": (
            "Title-only DFS monthly credit metrics contain offsetting evidence: delinquencies rose while write-offs fell. "
            "Mixed current-event direction and trigger eligibility are supported. No attribution or external source exists, "
            "so gold editorial_aggregation is unsupported; Benzinga-authored editorial_original is the defensible origin."
        ),
        "gold_correction": "Change source_origin to editorial_original; retain mixed direction and trigger eligibility.",
        "generic_fix": "Handle paired adverse/favorable credit metrics as mixed and allow compact current operating metrics to trigger.",
    },
    "N1328": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_event_role_error", "gold_eligibility_error", "v9_source_provenance", "v9_concept_scope"],
        "notes": (
            "FreightWaves syndication reports a current WERN $3.75 special dividend, new 5M-share repurchase, and $500M "
            "credit facilities funding the dividend. This is a material current issuer event even though syndicated; gold "
            "editorial_analysis/non-trigger labels are wrong. Direction is mixed because shareholder return is debt-funded. "
            "External text duplicates the article then appends unrelated current site content. V9 wrongly calls origin original, "
            "misses financing, and adds earnings/regulatory from filing references."
        ),
        "gold_correction": (
            "Set content_role primary_event, source_origin editorial_aggregation, and forecast/reaction eligible true; "
            "retain mixed capital_return/financing semantics."
        ),
        "generic_fix": "Separate event role from source provenance, deduplicate external text, and reject appended page chrome/unrelated stories.",
    },
    "N1329": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_parent_subsidiary_direction"],
        "notes": (
            "AIG subsidiary ILFC filed for an IPO in which an AIG subsidiary would sell shares. Gold mixed financing trigger "
            "is supported: value/liquidity realization is offset by reduced ownership/exposure. V9 captures only dilution/sale negativity."
        ),
        "generic_fix": "Apply parent-subsidiary IPO role semantics with both value realization and ownership-reduction evidence.",
    },
    "N1330": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_primary_source_precedence", "v9_related_link_contamination", "v9_indirect_regulatory_direction"],
        "notes": (
            "Article includes the official FDA release warning JUUL; MO owns 35% and is an indirect harmed issuer. Gold "
            "regulatory-primary, negative regulatory trigger is supported. V9 treats related-link earnings text and static "
            "ownership/product mentions as current positive events, overwhelming the warning."
        ),
        "generic_fix": "Prioritize attached official regulator text, exclude related-link titles, and propagate adverse regulation through explicit ownership links.",
    },
    "N1331": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_source_provenance", "v9_analyst_scope", "v9_market_reaction_leak"],
        "notes": (
            "TheStreet dividend-stock list is syndicated editorial aggregation. Its DUK, POM and T passages explicitly "
            "combine reported earnings/results with analyst valuation or recommendation evidence; gold records only "
            "analyst_action for those issuers, while JNJ is correctly limited to dividend/capital-return comparison. "
            "V9 loses the analyst distinctions, calls the source original and treats observed price commentary as a catalyst."
        ),
        "gold_correction": "Add earnings to DUK, POM and T concepts; retain their issuer-specific analyst directions and the aggregation origin.",
        "generic_fix": (
            "Preserve syndicated-source provenance and label each list passage independently, separating reported results, "
            "analyst valuation and observed market reaction rather than pooling article-wide concepts."
        ),
    },
    "N1332": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_source_provenance", "v9_product_semantics", "v9_related_text_contamination"],
        "notes": (
            "This is a CNBC-derived why-moving follow-up about a demonstrated OpenAI integration in Figma. Gold correctly "
            "treats it as positive product/commercial issuer history but not a fresh forecast trigger. V9 identifies the "
            "follow-up role yet calls it original, misses the product event and adds earnings/market-reaction concepts from "
            "surrounding link and price text."
        ),
        "gold_correction": "none",
        "generic_fix": (
            "Retain attributed aggregation provenance, recognize demonstrated product integrations, and exclude related-link "
            "and observed-price language from event-concept extraction."
        ),
    },
    "N1333": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_preview_direction", "v9_clinical_false_positive"],
        "notes": (
            "ANF is an earnings preview, not a realized result: consensus expected EPS and sales declines and estimates "
            "had been revised lower. Gold negative contextual sentiment and non-trigger eligibility are supported. V9 "
            "returns neutral and invents a clinical concept from unrelated vocabulary."
        ),
        "gold_correction": "none",
        "generic_fix": "Score explicit lowered estimates and year-over-year preview deltas, and require biomedical trial context for clinical concepts.",
    },
    "N1334": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_extraction_taxonomy"],
        "notes": (
            "The article is technical analysis of crypto instruments only. Gold unsupported_instrument is correct; V9 "
            "correctly emits no issuer but collapses the reason to no_supported_event."
        ),
        "gold_correction": "none",
        "generic_fix": "Preserve unsupported-instrument as a distinct extraction outcome when all identified symbols are non-equity instruments.",
    },
    "N1335": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_truncated_issuer_identity", "v9_contextual_product_event"],
        "notes": (
            "The long cannabis/sports feature explicitly states Marvin Washington's involvement with CBD-products maker "
            "ISODIOL INTERNATIO (ISOLF). Other provider tickers are broad sector metadata. Gold positive product/commercial "
            "issuer history for ISOLF alone is supported. V9 emits no issuer, likely because the displayed company name is truncated."
        ),
        "gold_correction": "none",
        "generic_fix": "Resolve exchange-qualified ticker mentions even when the adjacent provider company name is truncated, then scope contextual product evidence to that issuer.",
    },
    "N1336": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_event_role", "v9_comparator_issuer_fp", "v9_related_text_contamination"],
        "notes": (
            "AMD announced and launched two gaming APUs with stated price/performance advantages, so gold positive primary "
            "product event and trigger eligibility are supported. INTC and NVDA are comparison products, not affected issuer "
            "units. V9 demotes the launch to editorial analysis, emits comparator issuers and imports earnings/market reaction "
            "from related links and the opening price observation."
        ),
        "gold_correction": "none",
        "generic_fix": (
            "Recognize explicit issuer launch predicates as primary events, distinguish product comparators from affected "
            "issuers, and exclude related-link plus observed-price text from event concepts."
        ),
    },
    "N1337": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_only_regulatory_event", "v9_symbol_issuer_resolution"],
        "notes": (
            "The title itself reports government officials considering a major crackdown on Google and explicitly names "
            "$GOOG; SPY is only broad-market metadata. Gold negative regulatory trigger for GOOG is supported despite the "
            "body containing only the source URL. V9 emits no event or issuer."
        ),
        "gold_correction": "none",
        "generic_fix": "Allow sufficiently explicit attributed headline evidence to form a regulatory event and resolve cashtag issuers without requiring a body.",
    },
    "N1338": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_transaction_roles", "v9_title_only_ma", "v9_issuer_identity"],
        "notes": (
            "The title supports a material merger rumor involving CBS and Viacom, but it does not establish an acquirer/target "
            "orientation; both are transaction participants. Gold neutral M&A trigger semantics are otherwise supported. "
            "V9 recognizes the article role but emits neither issuer nor transaction concept."
        ),
        "gold_correction": "Replace unsupported CBS-acquirer/VIAB-target roles with symmetric transaction-participant roles; retain both neutral trigger units.",
        "generic_fix": "Resolve company names in compact title-only M&A reports and preserve symmetric participant roles unless the acquisition predicate establishes orientation.",
    },
    "N1339": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_source_provenance", "v9_direction", "v9_concept_scope"],
        "notes": (
            "Oppenheimer warns that a foreign-led Tesla buyout could face CFIUS review. Negative analyst sentiment and "
            "regulatory context are supported, but the buyout/M&A context is also explicit and omitted from gold. V9 calls "
            "the source editorial, returns neutral, and records analyst/M&A while missing regulation."
        ),
        "gold_correction": "Add ma_transaction alongside regulatory; retain analyst_research origin, negative direction and non-trigger eligibility.",
        "generic_fix": "Recognize conditional regulatory obstacles in analyst M&A commentary and preserve analyst-source attribution from explicit firm speech.",
    },
    "N1340": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_only_ma", "v9_unlisted_provider_counterparty", "v9_rejection_direction"],
        "notes": (
            "The title fully specifies SYNL's 3% stake, merger proposal and USAP board rejection. Gold mixed M&A/ownership "
            "trigger units for bidder and target are supported. Only SYNL appears in provider metadata, so USAP requires the "
            "point-in-time identity authority. V9 emits no issuers."
        ),
        "gold_correction": "none",
        "generic_fix": "Parse relational title-only stake/proposal/rejection events and resolve named counterparties from point-in-time issuer identity rather than provider tickers alone.",
    },
    "N1341": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "The reported newspaper evacuation has no supported public issuer. Gold and V9 correctly emit non-issuer market content.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1342": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_award_product_overreach"],
        "notes": (
            "ONTO's issuer release announces a TSMC collaboration award and continuing process-control collaboration. Gold "
            "positive contract/collaboration trigger is supported; TSM is the awarding customer/counterparty, not a separate "
            "affected issuer unit. V9 adds product_commercial even though no new product launch or commercial availability is announced."
        ),
        "gold_correction": "none",
        "generic_fix": "Treat recognition of an existing collaboration as contract/collaboration evidence without inferring a new product-commercial event from platform descriptions.",
    },
    "N1343": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_missing_metadata_identity", "v9_external_bot_text"],
        "notes": (
            "The Zacks recap explicitly maps company names to AIG, TRH, GS, WFC and BAC in text despite empty provider "
            "tickers. Its substantive event is AIG's sale of its remaining TRH stake through a secondary offering. Gold "
            "properly limits affected units to AIG and TRH but under-specifies concepts: ownership and financing apply to "
            "both sides. V9 emits non-issuer content. The external lane is repeated anti-bot page text and must be rejected."
        ),
        "gold_correction": "Assign both ownership and financing to AIG and TRH; retain contextual aggregation eligibility.",
        "generic_fix": "Resolve explicit company-name/ticker pairs when provider tickers are empty and reject repeated anti-bot challenge text as an external source lane.",
    },
    "N1344": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_swing_to_profit_direction"],
        "notes": (
            "The premarket roundup scopes one realized result to RIG and earnings previews to the other issuers. Gold "
            "positive RIG sentiment for 'swung to a profit' and neutral preview sentiment elsewhere are supported. V9 "
            "matches all structure and concepts but leaves RIG neutral."
        ),
        "gold_correction": "none",
        "generic_fix": "Treat an explicit swing from loss to profit as positive realized earnings evidence within the issuer passage.",
    },
    "N1345": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_extraction_taxonomy", "v9_source_provenance"],
        "notes": (
            "The IMF G-20 outlook is attributed macro content with no corporate issuer; BROAD/SPY/VGK are broad-market "
            "metadata. Gold non-issuer market content and aggregation provenance are supported. V9 emits no issuer but "
            "uses no_supported_event and editorial_original."
        ),
        "gold_correction": "none",
        "generic_fix": "Classify attributed institutional macro reports as non-issuer market content with aggregation provenance.",
    },
    "N1346": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_recap_passage_scope", "v9_joint_venture_semantics"],
        "notes": (
            "The premarket-loser recap contains distinct issuer catalysts. CNK's selling shareholders announced a share "
            "sale, NRGY launched a common-unit offering, and MT/BHP discussed a mining joint venture. Gold omits ownership/"
            "financing for CNK. V9 misses the joint-venture concepts and directions, while adding contract, operations and "
            "product concepts to NRGY from generic words outside the offering predicate."
        ),
        "gold_correction": "Add ownership and financing to CNK; retain the existing contextual recap units and eligibility.",
        "generic_fix": "Segment mover recaps by issuer paragraph, recognize joint-venture predicates for both counterparties, and constrain offering concepts to financing/ownership evidence.",
    },
    "N1347": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_point_in_time_alias", "v9_counterparty_fp", "v9_recap_passage_scope"],
        "notes": (
            "The market roundup contains issuer-scoped catalysts. Gold omits guidance for DOV even though it explicitly "
            "cut quarterly and full-year forecasts. V9 also emits obsolete IPCIQ beside point-in-time IPCI, promotes CRM/DIS/"
            "GOOG bidder references to issuer units, misses TSRO's upgrade and TWLO's offering, and treats observed TWTR price "
            "movement as an event concept."
        ),
        "gold_correction": "Add guidance to DOV; retain earnings because the article also describes the quarterly EPS outlook.",
        "generic_fix": (
            "Use one point-in-time ticker identity, scope each roundup catalyst to its affected issuer, suppress merely "
            "consulted/withdrawn bidder references, and separate observed price movement from causal concepts."
        ),
    },
    "N1348": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_source_provenance"],
        "notes": (
            "GE directly announced a turbine supply, construction and maintenance contract. Gold issuer-direct positive "
            "contract trigger is fully supported and V9 matches every issuer field, but mislabels provenance as editorial original."
        ),
        "gold_correction": "none",
        "generic_fix": "Detect first-person issuer announcement/press-release language as issuer_direct even when the provider URL is Benzinga-hosted.",
    },
    "N1349": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_issuer_update_role", "v9_source_provenance", "v9_operating_metric_direction"],
        "notes": (
            "BNL's first-person operating update reports completed property acquisitions, a controlled pipeline, 99.7% "
            "occupancy and full recent rent collection. Gold positive primary operations trigger and issuer-direct origin are "
            "supported. V9 emits the issuer but demotes the event to neutral editorial context with no concept or trigger."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize first-person dated operating updates and favorable occupancy/collection/pipeline metrics as issuer-direct primary operations events.",
    },
    "N1350": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_macro_nonissuer_gate", "v9_role_channel_leak"],
        "notes": (
            "El-Erian's macro commentary compares equity indexes, the dollar, gold and technology breadth; SPY/RSP are "
            "instruments, not corporate issuers. Gold non-issuer editorial analysis is supported. V9 emits no issuer but calls "
            "the article regulatory solely from broad policy/regulation language and uses no_supported_event."
        ),
        "gold_correction": "none",
        "generic_fix": "Apply the non-issuer macro/instrument gate before role inference and do not infer regulatory_event from channels or policy commentary without an affected issuer.",
    },
    "N1351": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Wells Fargo maintained Overweight but cut LGIH's target from $102 to $42. Gold and V9 correctly preserve mixed analyst sentiment and non-trigger eligibility.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1352": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "v9_why_moving_title", "v9_title_only_issuer"],
        "notes": (
            "The title is Benzinga's own inference that BETR shares rose because investors viewed a company presentation "
            "positively. It is a why-moving follow-up and positive market-reaction history, not a fresh causal event. No "
            "outside source is attributed, so gold editorial_aggregation is unsupported; editorial_original is appropriate. "
            "V9 emits no issuer or event."
        ),
        "gold_correction": "Change source_origin to editorial_original; retain why_moving_followup, positive market reaction and non-trigger eligibility.",
        "generic_fix": "Recognize explicit title-only share-move explanations as issuer history while keeping them forecast/reaction ineligible.",
    },
    "N1353": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_source_lane_contamination", "v9_multi_event_role", "v9_direction"],
        "notes": (
            "The provider article is a two-event editorial roundup: PHRRF filed pre-submission ANDA correspondence, "
            "described anticipated approval/launch and a new commercialization partnership; Stella acquired FTHWF's U.S. "
            "assets and assumed clinic operations. Gold omits contract_order for PHRRF and omits ma_transaction/operations "
            "for FTHWF. The attached 535k-character KETALAR prescribing-information PDF is a generic reference document, "
            "not current issuer evidence. V9 lets it contaminate concepts and misses both event directions and eligibility."
        ),
        "gold_correction": "Add contract_order to PHRRF and ma_transaction plus operations to FTHWF; retain issuer-specific directions and trigger decisions.",
        "generic_fix": (
            "Segment multi-event editorial articles by issuer, recognize regulatory filing/anticipated launch and asset-sale "
            "predicates, and exclude generic reference PDFs from current-event semantics unless explicitly issuer-authored and event-linked."
        ),
    },
    "N1354": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_eligibility_error", "gold_unsupported_origin", "v9_missing_metadata_identity", "v9_title_only_financing"],
        "notes": (
            "The title reports VRNT's imminent $200M convertible investment closing and conversion price. It is a material "
            "current financing event, not merely contextual history, so gold's non-trigger decision and mentioned-subject role "
            "are inconsistent with its primary_event label. With no body or attributed issuer release, issuer_direct provenance "
            "is not established. V9 emits non-issuer content because provider tickers are empty."
        ),
        "gold_correction": "Set VRNT primary_subject and forecast/reaction eligible true; use editorial_original provenance; retain mixed financing/valuation semantics.",
        "generic_fix": "Resolve company names through identity authority when provider tickers are empty and treat explicit material investment/conversion headlines as financing triggers.",
    },
    "N1355": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "The compact Bank of America downgrade and lower target for KNX is correctly classified by gold and V9 as negative analyst context.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1356": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_extraction_taxonomy", "v9_source_provenance"],
        "notes": (
            "Spiegel's report on German tax increases and euro-crisis preparation is attributed macro content; BROAD/VGK "
            "are instruments rather than issuers. Gold non-issuer aggregation is supported. V9 emits no issuer but records "
            "no_supported_event and editorial_original."
        ),
        "gold_correction": "none",
        "generic_fix": "Preserve non-issuer macro outcomes and infer aggregation when a headline explicitly attributes the report to another publication.",
    },
    "N1357": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unjustified_direction", "v9_title_only_financing", "v9_filing_provenance"],
        "notes": (
            "The title reports ABBV issuing EUR3.6B of debt according to a 424B5. It establishes a material financing event "
            "but contains no use-of-proceeds, pricing stress or other evidence that makes the text itself negative. Gold's "
            "negative direction is therefore unsupported; neutral is appropriate. V9 demotes the filing-derived headline to "
            "editorial context and misses the financing trigger."
        ),
        "gold_correction": "Change ABBV text and forecast direction to neutral; retain financing, trigger eligibility and filing-attributed aggregation origin.",
        "generic_fix": "Recognize filing-attributed debt issuance as a primary financing event while keeping direction neutral absent favorable or adverse terms.",
    },
    "N1358": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "gold_comparator_units", "v9_primary_product_event", "v9_issuer_scope"],
        "notes": (
            "GM released final Volt pricing and improved range, supporting a positive primary product trigger. The article is "
            "Benzinga-authored with company quotation, not attributed aggregation. Ford, Honda and Toyota are only price-class "
            "comparators and should not be gold issuer units; TSLA has an explicit adverse competitive implication and may "
            "remain contextual. V9 misses GM while emitting all comparators."
        ),
        "gold_correction": "Change origin to editorial_original and remove F, HMC and TM issuer units; retain GM trigger and contextual negative TSLA unit.",
        "generic_fix": "Prioritize the announced product issuer and suppress neutral product-list comparators unless the passage states an issuer-specific consequence.",
    },
    "N1359": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_role", "v9_source_provenance", "v9_financial_metric_leak"],
        "notes": (
            "The article aggregates JMP's Outperform initiation and CNBC commentators' bullish reasoning about TWTR. Gold "
            "positive analyst context and aggregation provenance are supported. V9 calls it editorial original, returns "
            "neutral and mistakes cited growth/margin reasoning for a new earnings event."
        ),
        "gold_correction": "none",
        "generic_fix": "Treat attributed analyst initiation plus commentary as analyst aggregation and keep supporting historical metrics inside analyst reasoning rather than new earnings concepts.",
    },
    "N1360": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_etf_as_issuer", "v9_macro_nonissuer_gate"],
        "notes": (
            "The article is macro commentary about Fed reserves, banking-system fragility and broad indexes. IWM is an ETF "
            "used only to quantify the Russell 2000 move, not an issuer. Gold non-issuer editorial analysis is supported. "
            "V9 incorrectly emits IWM and promotes the macro commentary to a primary event."
        ),
        "gold_correction": "none",
        "generic_fix": "Exclude ETFs/index instruments from issuer units and apply macro non-issuer classification before current-event role inference.",
    },
    "N1361": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "v9_title_only_halt", "v9_listing_event"],
        "notes": (
            "The title establishes that SIXD trading was halted, a material current market-structure event with unknown "
            "cause and therefore neutral text direction. No external or exchange attribution is present, so gold's aggregation "
            "origin is unsupported; editorial_original is safer. V9 emits no issuer or event."
        ),
        "gold_correction": "Change source_origin to editorial_original; retain neutral listing/market-structure trigger semantics.",
        "generic_fix": "Recognize explicit ticker/company trading-halt headlines as listing/market-structure triggers even without a body or stated reason.",
    },
    "N1362": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "v9_followup_direction", "v9_market_reaction_concept"],
        "notes": (
            "Benzinga explains observed rallies in PHUN, DWAC and RUM through election-related association. It is correctly "
            "a non-triggering why-moving follow-up with positive issuer history, but no outside article is aggregated; the "
            "origin should be editorial_original. V9 keeps the role/eligibility but returns neutral and treats observed PHUN "
            "movement as a causal concept."
        ),
        "gold_correction": "Change source_origin to editorial_original; retain all three positive contextual issuer units and no event concepts.",
        "generic_fix": "Derive follow-up sentiment from explicit observed direction while keeping price movement out of causal event concepts.",
    },
    "N1363": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "v9_extraction_taxonomy"],
        "notes": (
            "The title reports a U.S. government deficit statistic; DIA/SPY are broad-market instruments and no issuer is "
            "implicated. Gold non-issuer market content is correct, but no outside source is named, so editorial_aggregation "
            "is unsupported and editorial_original is the available provenance. V9 uses no_supported_event."
        ),
        "gold_correction": "Change source_origin to editorial_original; retain non_issuer_market_content.",
        "generic_fix": "Classify government economic-statistic headlines with only ETF metadata as non-issuer market content.",
    },
    "N1364": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "v9_temporal_event_polarity", "v9_related_link_contamination"],
        "notes": (
            "The current event is DGLY's withdrawal of a planned dilutive offering, which the article explicitly frames as "
            "positive. The prior offering terms are background, not the active negative event. Gold why-moving follow-up, "
            "positive financing history and non-trigger eligibility are supported, but the Benzinga-authored article is "
            "editorial_original rather than aggregation. V9 scores the superseded offering as negative and imports legal "
            "concepts from related-link titles plus observed market reaction."
        ),
        "gold_correction": "Change source_origin to editorial_original; retain positive financing follow-up semantics.",
        "generic_fix": "Model cancellation/withdrawal as reversing the polarity of a planned dilutive event, and exclude related-link plus observed-price text from concepts.",
    },
    "N1365": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_primary_product_event", "v9_source_provenance", "v9_sales_guidance_as_earnings"],
        "notes": (
            "DSCI directly announced first shipment of a new OTC product to a major pharmacy chain, expected placement in "
            "4,000 stores and approximately $1.2M of second-half sales. Gold positive product trigger is supported but omits "
            "explicit guidance. V9 demotes the issuer release, returns neutral and labels projected sales as realized earnings."
        ),
        "gold_correction": "Add guidance to DSCI concepts; retain positive product-commercial trigger and issuer-direct provenance.",
        "generic_fix": "Recognize first shipment/distribution launches as primary product events and distinguish prospective sales guidance from realized earnings.",
    },
    "N1366": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_role_origin_conflation", "v9_automated_origin", "v9_market_reaction_concept"],
        "notes": (
            "This is structurally a mover recap and explicitly says it was generated by Benzinga's automated content engine. "
            "Automation describes source origin; mover_recap describes content role. Gold conflates the two by using "
            "automated_summary for both. The issuer units correctly retain only passages with an earnings catalyst and omit "
            "price-only rows. V9 gets mover role right but calls the source aggregation and adds observed price moves as concepts."
        ),
        "gold_correction": "Set content_role to mover_recap and retain source_origin automated_summary; keep the existing earnings issuer units and eligibility.",
        "generic_fix": "Model automation as provenance independently from mover-recap role and never promote observed move percentages to causal event concepts.",
    },
    "N1367": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Jefferies downgraded DLTR and cut its target. Gold and V9 agree on negative analyst context and all operational flags.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1368": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_direction_balance", "v9_product_overreach", "v9_operations_miss"],
        "notes": (
            "CSCO reported revenue/EPS beats, mostly above-consensus quarterly/full-year guidance, management changes and "
            "stabilizing demand despite a 13% year-over-year revenue decline. Gold positive direction is supported by the "
            "preponderance and forward evidence, but concepts omit explicit guidance and management/governance. V9 becomes "
            "mixed from the historical decline, calls Splunk contribution a new product event and misses operating stabilization."
        ),
        "gold_correction": "Add guidance and management_governance to CSCO concepts; retain earnings, operations and positive trigger direction.",
        "generic_fix": "Weight estimate beats and forward guidance above historical YoY decline, and distinguish acquired-product contribution from a newly announced product event.",
    },
    "N1369": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_extraction_taxonomy", "v9_source_provenance"],
        "notes": (
            "The Reuters/Sun-attributed Brexit negotiation report is non-issuer political/macro content; EWU/VGK are "
            "instruments. Gold non-issuer aggregation is supported. V9 emits no issuer but records no_supported_event and "
            "editorial_original despite explicit attribution."
        ),
        "gold_correction": "none",
        "generic_fix": "Preserve attributed political/macro reports as non-issuer aggregation rather than unsupported events.",
    },
    "N1370": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_followup_role", "v9_source_provenance"],
        "notes": (
            "This standardized 'earlier reported' item repeats KOS results: a narrower-than-expected loss but a material "
            "revenue miss and large YoY declines. Gold mixed non-triggering follow-up/aggregation semantics are supported. "
            "V9 gets issuer direction and earnings concept right but treats it as a fresh issuer-direct primary event."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize explicit 'earlier reported' result summaries as non-triggering editorial follow-ups, not fresh issuer-direct events.",
    },
    "N1371": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_roundup_role", "v9_direction_balance", "v9_market_reaction_concept"],
        "notes": (
            "The stocks-to-watch article is a market roundup with distinct issuer passages. ADBE combines lower profit and "
            "weak guidance with strong subscription growth; gold omits the explicit guidance concept. RH combines an earnings "
            "beat with a co-CEO resignation. TXI explores a sale and ZQK posts a loss. V9 calls the whole article a preview, "
            "misses mixed balances/management context and repeatedly adds observed after-hours moves as concepts."
        ),
        "gold_correction": "Add guidance to ADBE concepts; retain operations, mixed direction and all existing contextual units.",
        "generic_fix": "Classify stocks-to-watch lists as roundups, score each issuer passage independently, and exclude observed after-hours movement from causal concepts.",
    },
    "N1372": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "gold_unsupported_origin", "v9_source_provenance"],
        "notes": (
            "The Benzinga long-idea article summarizes JCP's earnings beat, $900M repurchase, comparable-sales/EPS guidance "
            "and operating turnaround initiatives. Gold positive contextual sentiment is supported but omits guidance and "
            "operations, both of which V9 correctly extracts. No outside article is attributed, so editorial_original is the "
            "supported provenance rather than aggregation."
        ),
        "gold_correction": "Add guidance and operations to JCP concepts and change source_origin to editorial_original; retain positive non-triggering editorial analysis.",
        "generic_fix": "Preserve explicit guidance and operating initiatives inside editorial issuer history, while inferring original provenance when no source is attributed.",
    },
    "N1373": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_automated_valuation_context"],
        "notes": (
            "The automated P/E article presents both undervaluation and weaker-peer interpretations for MSGN. Gold neutral "
            "strategy/valuation history, automated provenance and non-trigger eligibility are supported. V9 recognizes the "
            "article type but suppresses the issuer unit and valuation concept."
        ),
        "gold_correction": "none",
        "generic_fix": "Emit issuer-scoped valuation context from automated P/E articles as history even though it is not a causal forecast trigger.",
    },
    "N1374": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_preview_direction"],
        "notes": (
            "STBA's preview says consensus implies 16.92% EPS growth and 2.36% sales growth, while estimates were revised "
            "higher. Gold positive contextual preview sentiment is supported despite a neutral analyst rating. V9 returns neutral."
        ),
        "gold_correction": "none",
        "generic_fix": "Score explicit positive consensus growth and upward estimate revisions in previews without treating them as realized results.",
    },
    "N1375": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_unsupported_origin", "v9_extraction_taxonomy"],
        "notes": (
            "The U.K. unemployment statistic is non-issuer macro content and EWU is an ETF. Gold extraction decision is "
            "correct, but no outside source is attributed in the available title-only record, so editorial_aggregation is "
            "unsupported. V9 uses no_supported_event."
        ),
        "gold_correction": "Change source_origin to editorial_original; retain non_issuer_market_content.",
        "generic_fix": "Classify macro-statistic headlines carrying only ETF metadata as non-issuer market content.",
    },
    "N1376": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_regulatory_role", "v9_source_provenance"],
        "notes": (
            "The FDA removed TNYA's clinical hold after concerns were addressed, allowing its Phase 1b/2a trial to resume. "
            "Gold positive regulatory trigger is supported but omits the inseparable clinical-trial concept. V9 captures both "
            "concepts and direction yet calls the regulatory event a generic primary event and the relayed issuer announcement original."
        ),
        "gold_correction": "Add clinical alongside regulatory; retain positive trigger and regulatory_event role.",
        "generic_fix": "Give FDA hold/removal predicates regulatory-event precedence while retaining clinical context and source attribution.",
    },
    "N1377": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_filing_role_origin", "v9_charge_as_earnings", "v9_strategic_alternatives"],
        "notes": (
            "The filing-derived HLVX disclosure reports a 70% workforce reduction, associated charges and evaluation of "
            "strategic alternatives. Gold negative regulatory-primary operations/strategy trigger is supported. V9 captures "
            "negative operations but treats future restructuring charges as earnings, misses strategic alternatives and loses "
            "the regulatory filing role/provenance."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize filing-derived restructuring disclosures, distinguish expected charges from earnings results, and extract explicit strategic-alternatives review.",
    },
    "N1378": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_concept_error", "gold_unsupported_origin", "v9_operations_direction"],
        "notes": (
            "The title reports record CMCL production, 5.1% first-half growth and raised production guidance. This is an "
            "operations plus guidance event, not an earnings result. With no body or source attribution, issuer_direct origin "
            "is not established. V9 extracts guidance but remains neutral, omits operations and disables the trigger."
        ),
        "gold_correction": "Replace earnings with guidance, retain operations, and change source_origin to editorial_original; keep positive trigger eligibility.",
        "generic_fix": "Recognize production records/growth and raised production outlook as positive operations/guidance triggers without requiring earnings vocabulary.",
    },
    "N1379": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_automated_boilerplate_direction"],
        "notes": (
            "The automated MODN recap reports a 75% EPS beat, revenue growth and explicit FY2022 EPS guidance. Gold positive "
            "context is supported but omits guidance. V9 extracts guidance yet turns mixed, likely because generic boilerplate "
            "mentions that a beat or miss may affect price."
        ),
        "gold_correction": "Add guidance to MODN concepts; retain positive automated-summary history and non-trigger eligibility.",
        "generic_fix": "Exclude generic automated earnings boilerplate from direction scoring and score only the issuer's actual results and guidance values.",
    },
    "N1380": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_roundup_direction", "v9_compact_results", "v9_market_reaction_concept"],
        "notes": (
            "The roundup contains correctly separable catalysts. LOW reported results plus above-consensus annual guidance, "
            "and CAR reported weak revenue plus weak FY16 guidance; gold omits guidance for both. ABCO has an earnings beat "
            "and sales miss and should remain mixed. V9 misses compact 'upbeat/stronger results' for SQBG/XXIA and adds "
            "observed share moves as concepts."
        ),
        "gold_correction": "Add guidance to LOW and CAR concepts; retain all existing issuer directions and contextual eligibility.",
        "generic_fix": "Scope each roundup sentence, recognize compact qualitative result predicates, preserve beat/miss mixtures, and exclude observed price movement from event concepts.",
    },
    "N1381": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_acronym_ticker_collision", "v9_external_domain_parking"],
        "notes": (
            "LEI means Conference Board Leading Economic Index in this macro report, not the equity ticker LEI. Gold "
            "non-issuer content is correct. The current external lane is a later domain-for-sale page and must be rejected. "
            "V9 emits a false issuer from the acronym."
        ),
        "gold_correction": "none",
        "generic_fix": "Disambiguate acronym symbols using surrounding expansion/context and reject domain-parking/captcha pages as external source text.",
    },
    "N1382": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_alias_substring_collision", "v9_analyst_reasoning_concept_leak"],
        "notes": (
            "RBC downgraded BRCD, cut its target and lowered its 2016 EPS estimate. Gold negative analyst-action context is "
            "supported. V9 falsely resolves JCS from the generic substring 'Communications Systems' inside Brocade's full "
            "name, and promotes cited dividend yield/EPS estimate reasoning to capital-return and earnings events."
        ),
        "gold_correction": "none",
        "generic_fix": "Require unambiguous full-name/alias boundaries for issuer resolution and keep analyst valuation inputs inside analyst_action rather than current issuer concepts.",
    },
    "N1383": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_editorial_issuer_suppression", "v9_external_source_drift", "v9_contextual_regulatory_scope"],
        "notes": (
            "The editorial argues positively for ACH while describing BABA, TCEHY and WYNN as negatively exposed to Chinese "
            "regulatory crackdowns. Gold contextual issuer sentiments and non-trigger eligibility are supported. V9 suppresses "
            "all four issuer units. The external lane is later Chalco/Portfolio Armor site material rather than publication-time text."
        ),
        "gold_correction": "none",
        "generic_fix": "Retain explicitly scoped issuers in editorial analysis, attach regulatory context only to exposed issuers, and reject temporally drifted external-page captures.",
    },
    "N1384": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_origin_error", "gold_missing_market_structure", "v9_halt_suppression"],
        "notes": (
            "The title directly reports that NVAX trading was halted pending news. This is an actionable listing/market-structure "
            "event, but the Benzinga title is not a regulator-primary source. Gold trigger eligibility is supported; its origin and "
            "empty concept list are not. V9 recognizes the article role yet emits no issuer unit or trigger."
        ),
        "gold_correction": "Change source_origin to editorial_original and add listing_market_structure for NVAX; retain neutral forecast/reaction trigger eligibility.",
        "generic_fix": "Treat issuer-scoped halt/resume/news-pending headlines as listing-market-structure events and do not require a body to emit the explicit issuer trigger.",
    },
    "N1385": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_transaction_role_scope", "v9_hypothetical_financial_leak", "v9_analyst_mixed_sentiment"],
        "notes": (
            "Goldman analyzes CBOE's acquisition of BATS: BATS is the positively situated target, while CBOE has modeled "
            "accretion offset by leverage, de-rating and a reiterated Sell rating, hence mixed. Gold is supported. V9 misses the "
            "M&A concept for both issuers and misclassifies hypothetical pro-forma EPS as current earnings/guidance."
        ),
        "gold_correction": "none",
        "generic_fix": "Propagate transaction concepts by acquirer/target role, preserve issuer-specific mixed analyst evidence, and keep hypothetical pro-forma financials out of current earnings/guidance concepts.",
    },
    "N1386": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_options_activity", "v9_automated_context_leak", "v9_options_sentiment"],
        "notes": (
            "The article's primary evidence is unusual AMZN options activity described as noticeably bullish, with 61% bullish "
            "versus 38% bearish trades. Gold positive context is reasonable but omits the options-activity concept. V9 lets the "
            "boilerplate analyst list and next-earnings notice introduce analyst/earnings concepts and turns the result mixed."
        ),
        "gold_correction": "Add options_activity as the AMZN concept; retain positive automated-summary history and non-trigger eligibility.",
        "generic_fix": "Give the article's options-flow summary precedence and exclude templated analyst recaps, next-earnings notices and observed price text from current concepts and sentiment.",
    },
    "N1387": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_reasoning_concept_leak", "v9_observed_move_concept"],
        "notes": (
            "JPMorgan initiated ARLP Overweight with an explicit positive sales-growth and low-cost-production thesis. Gold "
            "positive analyst_action context is supported. V9 correctly gets role and direction but adds earnings from the "
            "analyst's forward thesis and market_reaction from the prior day's observed share gain."
        ),
        "gold_correction": "none",
        "generic_fix": "Keep analyst thesis forecasts within analyst_action and exclude backward-looking share-price observations from issuer event concepts.",
    },
    "N1388": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "RZLT directly announced an issuer-funded underwritten common-stock and pre-funded-warrant offering. Gold and V9 agree on negative financing, direct origin and trigger eligibility.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1389": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_executive_update_scope", "v9_operations_sentiment"],
        "notes": (
            "The SHAK founder described severe restaurant disruption and slow reopening, while GRUB reported both fivefold "
            "inbound orders and risk that restaurant customers would permanently close. Gold negative SHAK and mixed GRUB "
            "operations context is supported. V9 resolves both issuers but loses their scoped operational evidence."
        ),
        "gold_correction": "none",
        "generic_fix": "Extract issuer-scoped operational updates from attributed executive interviews and preserve opposing company-specific positives and negatives.",
    },
    "N1390": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance", "v9_analyst_balance", "v9_operations_classification", "v9_observed_move_concept"],
        "notes": (
            "Tesla reported record deliveries/production, while the analyst roundup balanced strong demand evidence against "
            "profitability concerns and failure to reiterate full-year delivery guidance. Gold mixed sentiment is supported but "
            "omits guidance. V9 turns positive, misses operations, and promotes operational figures/observed price text to "
            "earnings and product concepts."
        ),
        "gold_correction": "Add guidance to TSLA concepts; retain mixed analyst-event operations context and non-trigger eligibility.",
        "generic_fix": "Classify production/deliveries as operations, capture explicit non-reiteration of guidance, balance attributed analyst evidence, and exclude observed market moves.",
    },
    "N1391": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance", "v9_primary_transaction_demotion", "v9_trigger_suppression", "v9_transaction_sentiment"],
        "notes": (
            "Valeant publicly said it was prepared to raise its Allergan offer above $200, while Allergan also reported upbeat "
            "results and raised annual guidance. Gold correctly treats this as a current primary trigger but omits guidance for "
            "AGN. V9 demotes it to analysis, disables both triggers and loses the acquirer/target-specific mixture."
        ),
        "gold_correction": "Add guidance to AGN concepts alongside earnings and ma_transaction; retain existing issuer directions and trigger eligibility.",
        "generic_fix": "Recognize current bid revisions and target responses as primary transaction triggers, assign acquirer/target evidence separately, and retain concurrent issuer results and guidance.",
    },
    "N1392": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_issuer_direct_origin"],
        "notes": (
            "AMR's announcement states that it and named subsidiaries filed voluntary Chapter 11 petitions. Gold negative "
            "credit-solvency trigger and issuer-direct provenance are supported. V9 gets every semantic field except origin, "
            "which it incorrectly marks editorial."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize verbatim issuer announcement language and first-person company declarations as issuer-direct provenance even when hosted on an editorial URL.",
    },
    "N1393": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_incomplete_concepts", "v9_generated_story_detection", "v9_observed_move_concept", "v9_mixed_results"],
        "notes": (
            "This explicitly Neuro-generated after-hours recap contains separable issuer result passages. Gold directions are "
            "well supported, but UPST/PINS omit guidance and RIVN omits product progress. V9 misses the generated-story role, "
            "adds observed market reactions, and turns RIVN's revenue beat/loss miss positive instead of mixed."
        ),
        "gold_correction": "Add guidance to UPST and PINS; add product_commercial to RIVN; retain automated-summary contextual eligibility and all existing directions.",
        "generic_fix": "Detect explicit generation disclosures, scope each recap passage, exclude observed price moves, and preserve opposing beat/miss evidence within each issuer.",
    },
    "N1394": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_filing_headline_role", "v9_ppp_financing", "v9_trigger_suppression"],
        "notes": (
            "The issuer's 8-K disclosed a roughly $4.96M PPP loan. Gold treats the filing-derived financing as a positive "
            "regulatory trigger. V9 resolves FEIM but demotes the title to editorial analysis, misses financing and suppresses "
            "forecast/reaction eligibility."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize explicit filing/form headlines and obtained-loan predicates as regulatory financing triggers, including title-only records.",
    },
    "N1395": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_market_structure", "v9_security_identity", "v9_halt_suppression"],
        "notes": (
            "The title reports an after-hours halt of preferred security WHLRP pending news. Gold trigger semantics are "
            "supported but its concept list is incomplete. V9 emits no issuer unit, likely because the preferred-share symbol "
            "is absent or weak in the identity authority."
        ),
        "gold_correction": "Add listing_market_structure to WHLRP; retain neutral halt trigger eligibility and editorial-aggregation origin.",
        "generic_fix": "Support point-in-time preferred/security-class identities and emit issuer-scoped halt events from structured halt titles without requiring body text.",
    },
    "N1396": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_counterparty_direction", "v9_primary_update_demotion", "v9_concept_scope", "v9_source_aggregation"],
        "notes": (
            "Panasonic reported profitable battery production, strong Tesla demand beyond capacity, resumed operations and "
            "expansion talks. Gold positive PCRFF earnings/operations and positive TSLA counterparty operations are supported. "
            "V9 makes TSLA negative from historical relationship concerns, adds unrelated concepts and disables both current triggers."
        ),
        "gold_correction": "none",
        "generic_fix": "Weight current attributed updates over historical caveats, propagate supplier-demand/capacity evidence by issuer role, and distinguish operations from contracts, products and earnings.",
    },
    "N1397": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_concept_error", "v9_issuer_direct_origin"],
        "notes": (
            "BOOT provided a forward Q3 EPS and sales range, with sales below consensus. This is negative guidance, not "
            "realized earnings. V9 correctly identifies guidance, direction and trigger eligibility but misses issuer-direct provenance."
        ),
        "gold_correction": "Replace earnings with guidance for BOOT; retain negative direction and trigger eligibility.",
        "generic_fix": "Classify explicit 'sees' forward ranges as guidance and recognize compact issuer guidance releases as issuer-direct.",
    },
    "N1398": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "The FreightWaves commentary concerns China's Belt and Road influence and European logistics infrastructure without a supported traded issuer unit. Gold and V9 correctly retain it as non-issuer editorial market content.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1399": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_transaction", "v9_class_share_identity", "v9_reported_sale_trigger"],
        "notes": (
            "The report says Brown-Forman hired Rothschild to auction its wine business after weak sales. Gold mixed trigger "
            "semantics are reasonable but the asset-sale process is an M&A transaction concept, not none. V9 resolves BF.B "
            "but demotes the report to non-trigger analysis and loses its opposing divestiture and weak-sales evidence."
        ),
        "gold_correction": "Add ma_transaction to BF.B; retain mixed direction, editorial-aggregation provenance and trigger eligibility.",
        "generic_fix": "Recognize reported adviser-led asset-sale/auction processes as current transaction triggers and support punctuation-bearing class-share symbols.",
    },
    "N1400": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_reasoning_concept_leak"],
        "notes": (
            "Goldman downgraded DLB to Sell, cut its target and explained structural demand risks. Gold negative analyst_action "
            "context is supported. V9 gets role and sentiment but promotes the analyst's expected quarter beat and expected "
            "future guidance into current earnings/guidance issuer events."
        ),
        "gold_correction": "none",
        "generic_fix": "Keep analyst expectations, forecasts and model discussion inside analyst_action unless the issuer itself reports results or guidance.",
    },
    "N1401": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_estimate_as_earnings"],
        "notes": (
            "The article reports Jefferies' BBY downgrade and reduced analyst estimates, supported by softer TV demand and "
            "margin concerns. Gold negative analyst_action context is correct. V9 incorrectly treats analyst estimate revisions "
            "as a realized earnings event."
        ),
        "gold_correction": "none",
        "generic_fix": "Distinguish analyst estimate revisions from issuer-reported earnings and retain them solely as analyst_action evidence.",
    },
    "N1402": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_nonissuer_market_decision", "v9_provider_ticker_noise", "v9_aggregation_origin"],
        "notes": (
            "The title is a broad S&P/futures market update, not an issuer event. BROAD is noisy provider metadata and SPY is "
            "only the index proxy. Gold non-issuer market content is correct. V9 emits no issuer but uses no_supported_event "
            "instead of the explicit non-issuer decision and loses the aggregated market-update origin."
        ),
        "gold_correction": "none",
        "generic_fix": "Classify index/futures-only updates as non_issuer_market_content, treat generic BROAD metadata as noise, and recognize compact market-feed aggregation provenance.",
    },
    "N1403": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_mover_recap_false_issuer", "v9_catalyst_reference_as_event", "v9_observed_move_concept"],
        "notes": (
            "This automated after-market mover list gives price/volume statistics and only terse references that some earnings "
            "reports occurred, without issuer event details. Gold no_supported_event is correct. V9 emits OMIC, SAVA and VEEV "
            "history units and concepts from recap boilerplate and observed moves."
        ),
        "gold_correction": "none",
        "generic_fix": "For mover recaps, require a ticker-scoped causal passage with substantive event evidence; do not create issuer history from price statistics or 'earnings came out' boilerplate alone.",
    },
    "N1404": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_issuer_direct_origin", "v9_sales_context_as_earnings", "v9_conditional_regulatory_leak"],
        "notes": (
            "Mylan directly announced settlement of patent litigation. Product sales figures are market context, and DOJ/FTC "
            "review is a condition on the agreement rather than a completed regulatory action. Gold positive legal trigger is "
            "supported. V9 adds earnings/regulatory concepts and misses direct provenance."
        ),
        "gold_correction": "none",
        "generic_fix": "Distinguish contextual product-market sales from issuer earnings, conditional regulatory review from an actual decision, and detect issuer press-release provenance.",
    },
    "N1405": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_editorial_recommendation_suppression", "v9_missing_provider_ticker_identity", "v9_aggregation_origin"],
        "notes": (
            "The short editorial excerpt explicitly predicts better 2011 performance for NuVasive, EnerNOC and Christopher & "
            "Banks. Gold positive contextual issuer units are supported, including ENOC despite absent provider metadata. V9 "
            "suppresses all three and marks the aggregated teaser as original."
        ),
        "gold_correction": "none",
        "generic_fix": "Resolve explicit company names against point-in-time identity even when provider tickers are absent and retain scoped editorial recommendations as non-trigger issuer history.",
    },
    "N1406": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_nonissuer_market_decision", "v9_aggregation_origin"],
        "notes": (
            "The Bloomberg-attributed State Department policy headline concerns Chinese companies generally; FXI and SPY are "
            "market proxies, not issuer subjects. Gold non-issuer market content is correct. V9 emits no issuer but calls it "
            "no_supported_event and loses the explicit aggregation provenance."
        ),
        "gold_correction": "none",
        "generic_fix": "Classify broad policy stories attached only to funds/index proxies as non_issuer_market_content and preserve explicit wire attribution as editorial_aggregation.",
    },
    "N1407": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_automated_analyst_boilerplate", "v9_analyst_aggregate_direction"],
        "notes": (
            "The actual SITM table contains only bullish or somewhat bullish ratings and a 48.01% rise in the average target. "
            "Gold positive automated analyst context is supported. V9 becomes negative from instructional boilerplate mentioning "
            "bearish/negative categories and adds earnings/guidance from generic definitions."
        ),
        "gold_correction": "none",
        "generic_fix": "Parse populated analyst-rating table values, ignore empty category labels and explanatory boilerplate, and keep generic analyst metric examples out of issuer concepts.",
    },
    "N1408": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Gilead directly announced five-year Phase 3 Biktarvy data showing sustained efficacy, safety and no resistance-related failures. Gold and V9 fully agree on positive clinical trigger semantics.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1409": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_unsupported_instrument_decision", "v9_external_market_page_drift"],
        "notes": (
            "The article concerns Bitcoin, represented by X:BTCUSD, rather than a supported equity issuer. Gold "
            "unsupported_instrument is correct. V9 collapses this to no_supported_event. The external Polymarket lane is a "
            "later market-resolution page and should not influence publication semantics."
        ),
        "gold_correction": "none",
        "generic_fix": "Preserve explicit unsupported-instrument decisions for crypto/FX symbols and reject temporally drifted external market pages.",
    },
    "N1410": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_reasoning_concept_leak"],
        "notes": (
            "JPMorgan raised NCR's target and maintained Overweight based on bookings momentum and reduced risks. Gold positive "
            "analyst_action context is supported. V9 incorrectly adds earnings merely because the analyst note followed a prior "
            "earnings report."
        ),
        "gold_correction": "none",
        "generic_fix": "Do not promote a referenced prior earnings report into a current event when the present article's action is an analyst target/rating update.",
    },
    "N1411": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_regulatory_trigger_demotion", "v9_comparator_scope", "v9_promotional_boilerplate"],
        "notes": (
            "FDA Fast Track designation is a current positive regulatory trigger for co-developers PFE and LLY. REGN and TEVA "
            "are contextual competitors whose joint program had a prior clinical hold. Gold scopes these correctly. V9 demotes "
            "the trigger, misses comparator regulatory negatives and adds clinical/financing from background and Zacks promotion."
        ),
        "gold_correction": "none",
        "generic_fix": "Treat granted regulatory designations as current triggers for all named owners, keep comparator history scoped, and exclude recommendation/IPO marketing boilerplate from issuer concepts.",
    },
    "N1412": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_aggregation_origin"],
        "notes": (
            "Mubadala is an Abu Dhabi investment fund without a supported traded issuer identity in this authority. Gold and V9 "
            "correctly retain the mixed financial report as non-issuer content; V9 alone loses the compact wire-style aggregation provenance."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize terse third-party financial-wire summaries as editorial_aggregation even when no issuer unit is supported.",
    },
    "N1413": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_why_moving_pattern", "v9_older_catalyst_context", "v9_product_context"],
        "notes": (
            "The headline explicitly explains ONVO's move as possibly caused by recirculation of a prior-day printed-liver "
            "article. Gold positive product context, why-moving role and non-trigger eligibility are supported. V9 suppresses "
            "the issuer and loses the causal-followup semantics."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize 'shares move; may be attributed to earlier article' as why_moving_followup, retain the scoped prior catalyst for issuer history, and never promote it to a fresh trigger.",
    },
    "N1414": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_direction_balance", "v9_analyst_financial_leak"],
        "notes": (
            "Imperial downgraded ORN, cut estimates and its target because of execution and Texas-demand risks, despite some "
            "expected business growth. Gold negative analyst_action context is supported. V9 overweights isolated positives to "
            "mixed and promotes referenced issuer results/guidance to a current earnings concept."
        ),
        "gold_correction": "none",
        "generic_fix": "Give explicit downgrade/target-cut action precedence while retaining supporting rationale, and keep referenced issuer financials inside analyst_action context.",
    },
    "N1415": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_concept_error", "v9_roundup_scope", "v9_observed_move_concept", "v9_compact_catalyst"],
        "notes": (
            "The mover list includes substantive per-issuer catalysts. Most gold units are supported, but USAT issued sales "
            "guidance and filed an offering, not earnings. V9 misses CBAY clinical data and several compact analyst/M&A/product "
            "catalysts, while repeatedly adding observed market_reaction."
        ),
        "gold_correction": "Replace earnings with guidance for USAT and retain financing; preserve its mixed direction and all contextual eligibility.",
        "generic_fix": "Scope each mover bullet, extract only stated catalysts, recognize compact analyst/clinical/agreement/M&A predicates, and never use the observed move itself as an event concept.",
    },
    "N1416": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_concept_error", "v9_neutral_trigger_suppression", "v9_issuer_direct_origin"],
        "notes": (
            "HLIT's forward Q1 revenue range straddles the consensus estimate and is approximately neutral guidance, not "
            "realized earnings. V9 correctly identifies guidance and neutral sentiment but suppresses the trigger/forecast "
            "direction and misses direct provenance."
        ),
        "gold_correction": "Replace earnings with guidance for HLIT; retain neutral direction and forecast/reaction trigger eligibility.",
        "generic_fix": "Emit neutral but eligible guidance triggers when ranges bracket consensus and recognize compact issuer guidance headlines as issuer-direct.",
    },
    "N1417": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Wunderlich downgraded RUSHA from Buy to Hold while modestly raising its target. Gold and V9 correctly represent the net negative analyst action as contextual and non-triggering.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1418": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_aggregated_earnings_origin", "v9_multi_security_identity"],
        "notes": (
            "The compact Benzinga earnings record reports positive year-over-year FFO and sales for the same Granite stapled "
            "security represented by GRP and GRP.U. Gold correctly retains both security identities and editorial-aggregation "
            "origin. V9 semantic labels match but incorrectly calls the provider summary issuer-direct."
        ),
        "gold_correction": "none",
        "generic_fix": "Preserve multiple valid security-class identities for one issuer and classify generated/rewritten earnings summaries as editorial_aggregation unless direct-source evidence exists.",
    },
    "N1419": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_aggregation_origin"],
        "notes": "The Bank of Greece current-account release is macroeconomic non-issuer content. Gold and V9 agree on that decision; V9 alone mislabels the terse reported-data summary as editorial_original rather than aggregation.",
        "gold_correction": "none",
        "generic_fix": "Recognize compact attributed official-statistics summaries as editorial_aggregation.",
    },
    "N1420": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "UBS maintained Buy on MEOH and raised its target from $62 to $64. Gold and V9 fully agree on positive contextual analyst_action semantics.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1421": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance", "v9_why_moving_balance", "v9_external_api_boilerplate", "v9_aggregation_origin"],
        "notes": (
            "CHGG beat quarterly EPS/sales but issued first-quarter and FY23 revenue guidance materially below consensus. The "
            "why-moving item is therefore negative overall, and gold omits guidance from its concepts. V9 becomes mixed and "
            "loses aggregation provenance; the external API advertisement is unrelated and must be rejected."
        ),
        "gold_correction": "Add guidance to CHGG alongside earnings; retain negative contextual direction and why-moving non-trigger eligibility.",
        "generic_fix": "In why-moving result stories, score material forward-guidance misses over small historical beats, preserve both concepts, and reject unrelated external API boilerplate.",
    },
    "N1422": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_exchange_regulatory_role", "v9_company_name_ma_false_positive", "v9_security_terms_financing_false_positive"],
        "notes": (
            "NYSE Regulation suspended KWAC securities and began delisting because capitalization standards were not met. "
            "Gold negative listing-market-structure trigger is correct. V9 gets direction/eligibility but treats 'Acquisition' "
            "in the company name as M&A and warrant/security descriptions as financing, while losing exchange-primary provenance."
        ),
        "gold_correction": "none",
        "generic_fix": "Recognize exchange regulatory notices, suppress event predicates inside legal issuer names, and do not treat security-description terms as a new financing event.",
    },
    "N1423": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_multi_issuer_analyst_scope", "v9_analyst_reasoning_concept_leak", "v9_cross_issuer_polarity"],
        "notes": (
            "Goldman downgraded KBR/MLM/SUM, upgraded CAT/CMI, and expressed positive contextual views on DE/TEX. Gold scopes "
            "these issuer-specific analyst directions correctly. V9 bleeds construction negatives into machinery names and "
            "recasts analyst margin/earnings rationale as current issuer earnings/operations, sometimes omitting analyst_action."
        ),
        "gold_correction": "none",
        "generic_fix": "Segment multi-issuer analyst articles by named subsection/action, prevent cross-issuer polarity bleed, and retain valuation/operating forecasts within analyst_action.",
    },
    "N1424": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_concept_error", "v9_guidance_trigger_suppression", "v9_issuer_direct_origin"],
        "notes": (
            "CC provided a forward FY18 adjusted EBITDA range without a comparator. That is neutral guidance, not earnings. "
            "V9 misses the concept entirely, demotes the title to analysis and suppresses trigger eligibility."
        ),
        "gold_correction": "Replace earnings with guidance for CC; retain neutral direction and forecast/reaction trigger eligibility.",
        "generic_fix": "Recognize compact forward EBITDA ranges as neutral guidance triggers and infer issuer-direct compact guidance provenance from structured guidance records.",
    },
    "N1425": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_executive_identity", "v9_management_death_event", "v9_missing_provider_tickers"],
        "notes": (
            "Charlie Munger's death is a current negative governance event for Berkshire, where he was vice chairman, and DJCO, "
            "where he was chairman. COST is only historical director context. Gold applies those roles and eligibility correctly. "
            "V9 emits nothing, compounded by provider metadata omitting both Berkshire share classes."
        ),
        "gold_correction": "none",
        "generic_fix": "Resolve named executives/directors to point-in-time issuer roles from the identity authority, add missing share-class securities, and distinguish current offices from historical affiliations.",
    },
    "N1426": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_only_transaction_rumor", "v9_transaction_role", "v9_aggregation_origin"],
        "notes": (
            "The Barron's-attributed title reports an imminent potential TEVA bid for MYL. Gold correctly marks a positive target, "
            "neutral acquirer and current rumor trigger for both. V9 emits no issuer units because there is no body."
        ),
        "gold_correction": "none",
        "generic_fix": "Parse title-only attributed acquisition rumors, assign target/acquirer roles and preserve editorial-aggregation provenance without requiring body text.",
    },
    "N1427": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_mover_no_catalyst_decision"],
        "notes": (
            "This after-hours mover list contains only price, volume and market-cap observations with no causal event passage. "
            "Gold no_supported_event is correct because named issuers exist but no supported catalyst does. V9 correctly emits "
            "no issuer units yet collapses the decision to non_issuer_market_content."
        ),
        "gold_correction": "none",
        "generic_fix": "For mover recaps with named issuers but no causal passages, emit no_supported_event rather than non_issuer_market_content.",
    },
    "N1428": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_roundup_compact_catalyst", "v9_regulatory_product_confusion"],
        "notes": (
            "The premarket roundup contains five substantive issuer passages: NOK downgrade, BIIB FDA approval, RRD note "
            "offering, FWLT contract and AMAP repurchase authorization. Gold scopes all five correctly. V9 misses AMAP capital "
            "return, calls BIIB's FDA approval product_commercial and loses their positive directions."
        ),
        "gold_correction": "none",
        "generic_fix": "Parse each roundup bullet independently, map repurchase authorization to capital_return and FDA approval to regulatory, and preserve local sentiment.",
    },
    "N1429": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance", "v9_roundup_scope", "v9_price_only_issuer", "v9_observed_move_concept"],
        "notes": (
            "The market roundup includes JASO profit plus higher shipment outlook, SGK/MATW M&A, STAA regulatory/analyst "
            "news, KNDI results, VRSN/GTN downgrades and WUBA financing. Gold omits JASO guidance. V9 emits price-only "
            "CWCO/RGP history, misses transaction/financing passages and adds market_reaction to catalyst units."
        ),
        "gold_correction": "Add guidance to JASO alongside earnings; retain its positive contextual direction and all existing issuer units.",
        "generic_fix": "Segment market-roundup sections, require causal evidence rather than price-only mentions, extract compact M&A/financing/guidance predicates, and exclude observed moves.",
    },
    "N1430": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_listing_extension_polarity", "v9_contingent_reverse_split", "v9_boilerplate_concept", "v9_aggregation_origin"],
        "notes": (
            "HKPD received an extension but remained noncompliant with the $1 bid rule and might need a reverse split; gold "
            "negative listing/capital-structure context is supported. V9 overweights the extension to positive, adds earnings and "
            "market_reaction from surrounding metrics, and loses aggregation provenance."
        ),
        "gold_correction": "none",
        "generic_fix": "Score continued listing deficiency and contingent reverse-split risk together, exclude ranking/price boilerplate, and preserve why-moving aggregation provenance.",
    },
    "N1431": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Anthera directly priced a 33M-share underwritten public offering with an overallotment option. Gold and V9 fully agree on negative financing trigger semantics.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1432": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_incidental_customer_identity", "v9_cross_subject_financial_scope", "v9_external_source_drift"],
        "notes": (
            "This interview concerns private real-estate developer The Wright Group. AutoZone and CVS are merely examples of "
            "retail projects/customers and have no issuer event. Gold non-issuer decision is supported. V9 emits both as issuer "
            "history and assigns Wright Group revenue commentary to AZO; the external lane is unrelated future web content."
        ),
        "gold_correction": "none",
        "generic_fix": "Distinguish incidental customer examples from article subjects, bind financial predicates to their grammatical owner, and reject temporally unrelated external captures.",
    },
    "N1433": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_wire_aggregation_origin"],
        "notes": (
            "The title-only NHK report describes a Japanese nuclear-monitoring problem without identifying a traded issuer. "
            "Gold correctly treats it as non-issuer editorial aggregation. V9 agrees on decision and role but calls the "
            "second-hand NHK report editorial_original."
        ),
        "gold_correction": "none",
        "generic_fix": "Treat explicitly attributed wire, broadcaster and newspaper reports as editorial_aggregation when the provider is relaying rather than originating the report.",
    },
    "N1434": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_ma", "v9_observed_move_trigger", "v9_mixed_sentiment", "v9_ownership_confusion", "v9_aggregation_origin"],
        "notes": (
            "This is a why-moving follow-up published after LM hit a new low. It summarizes mixed results plus several "
            "acquisitions/investments, so gold should include ma_transaction alongside earnings. V9 incorrectly promotes "
            "the recap to a fresh primary trigger, calls it issuer-direct, reduces mixed evidence to negative and maps acquired stakes to ownership."
        ),
        "gold_correction": "Add ma_transaction to LM; retain mixed text sentiment, follow-up role and forecast/reaction ineligibility.",
        "generic_fix": "Detect observed-move follow-ups, preserve mixed beat/miss evidence, map acquisition stakes to M&A rather than ownership, and retain editorial aggregation provenance.",
    },
    "N1435": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": (
            "The provider article is an industry salary survey with no traded issuer event. Gold and V9 correctly classify it "
            "as non-issuer editorial analysis. The unrelated 50,000-character external lane is source drift and did not leak into labels."
        ),
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1436": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_mover_no_catalyst_decision"],
        "notes": (
            "This automated after-market mover list reports only observed price, volume and market-cap statistics. Named "
            "securities exist but no causal catalyst does, so gold no_supported_event is correct. V9 incorrectly collapses it to non_issuer_market_content."
        ),
        "gold_correction": "none",
        "generic_fix": "For mover recaps with named securities but no supported catalyst, emit no_supported_event rather than non_issuer_market_content.",
    },
    "N1437": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance", "v9_compact_positive_sentiment", "v9_observed_move_concept"],
        "notes": (
            "The roundup correctly scopes eight issuer catalysts. Gold omits guidance from both NMBL and MENT even though "
            "the text explicitly says downbeat/weak outlook. V9 misses positive polarity for ANW's upbeat earnings and LBMH's "
            "agreed acquisition, and adds market_reaction to NMBL from its observed decline."
        ),
        "gold_correction": "Add guidance to NMBL and MENT alongside earnings; preserve all other issuer units and contextual eligibility.",
        "generic_fix": "Preserve local polarity in compact roundup clauses, recognize target-positive agreed acquisitions, and never promote observed price movement to a catalyst concept.",
    },
    "N1438": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_negative_evidence_balance", "v9_observed_move_concept", "v9_aggregation_origin"],
        "notes": (
            "This AI-assisted why-moving recap reports declining total and consultancy revenue, a large net loss and disposal/restructuring losses, "
            "outweighing one growing division. Gold negative context is supported. V9 calls it mixed, adds market_reaction from the observed surge and loses aggregation provenance."
        ),
        "gold_correction": "none",
        "generic_fix": "Weight consolidated deterioration above one positive segment, exclude observed moves from concepts, and classify AI-assisted why-moving recaps as editorial aggregation.",
    },
    "N1439": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_earnings_origin"],
        "notes": (
            "The compact earnings wire contains a strong EPS beat but revenue miss and year-over-year declines, supporting gold mixed direction and trigger eligibility. "
            "V9 matches all issuer semantics but incorrectly treats Benzinga's report as issuer_direct."
        ),
        "gold_correction": "none",
        "generic_fix": "Use issuer_direct only for issuer-authored releases or explicitly quoted filings; provider-authored earnings wires are editorial aggregation.",
    },
    "N1440": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_clinical_concept_overlap"],
        "notes": "The issuer announced first-patient enrollment in a Phase 2 study. Gold clinical-only positive trigger semantics are correct; V9 wrongly adds product_commercial and regulatory without a product launch or regulatory action.",
        "gold_correction": "none",
        "generic_fix": "Map trial initiation/enrollment to clinical alone unless the same passage contains a distinct commercial or regulator decision predicate.",
    },
    "N1441": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_compact_earnings_miss", "v9_earnings_origin"],
        "notes": "MSA missed both EPS and revenue estimates in a compact Benzinga earnings wire. Gold negative trigger semantics are unambiguous; V9 neutralizes the misses, suppresses eligibility and calls the provider-authored wire issuer_direct.",
        "gold_correction": "none",
        "generic_fix": "Parse compact EPS/revenue miss comparisons as negative earnings triggers and reserve issuer_direct provenance for issuer-authored source material.",
    },
    "N1442": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_issuer_release_origin"],
        "notes": "CyberOptics announced deployment of its system on its partner's new platform and supplied a CEO quote. Gold issuer-direct positive contract/product trigger semantics are supported; V9 only misclassifies provenance as editorial_original.",
        "gold_correction": "none",
        "generic_fix": "Recognize issuer announcement syntax and first-party executive quotations as issuer_direct provenance even when the stored URL is a provider mirror.",
    },
    "N1443": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_offering_boilerplate_concepts", "v9_binary_external_payload"],
        "notes": (
            "The current event is a registered direct stock-and-warrant financing. Clinical programs and prior FDA clearances appear only in the About section; "
            "gold financing-only negative trigger semantics are correct. V9 leaks clinical and regulatory concepts from boilerplate. The external lane is JPEG binary text and must remain excluded."
        ),
        "gold_correction": "none",
        "generic_fix": "Stop current-event concept extraction at offering boilerplate/About sections and reject binary/image payloads before semantic processing.",
    },
    "N1444": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_rivn_concept_scope", "v9_cross_issuer_sentiment", "v9_expert_opinion_scope", "v9_origin_byline"],
        "notes": (
            "RIVN's evidence is a delivery beat and raised delivery outlook, not earnings; gold should use operations plus guidance. TSLA's evidence is Gary Black's negative thesis on FSD demand and marketing. "
            "V9 leaks RIVN's positive delivery facts into TSLA, misses the expert-opinion concept and treats a Benzinga-authored synthesis as aggregation merely because it quotes social posts."
        ),
        "gold_correction": "Replace earnings with guidance for RIVN, retaining operations, positive contextual direction and existing eligibility.",
        "generic_fix": "Bind each quoted claim to its issuer, recognize attributable investment-research opinion without requiring a formal rating, and derive origin from authorship rather than the presence of quotations.",
    },
    "N1445": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_cross_listing_alias", "v9_primary_announcement_role", "v9_counterparty_financing", "v9_boilerplate_concept"],
        "notes": (
            "Jushi announced a large capacity expansion and IIPR would partially finance it through an enlarged lease. Gold correctly labels JUSHF operations and IIPR financing. "
            "V9 fails to consolidate CSE:JUSH with OTC JUSHF, suppresses the actual JUSHF unit, misses IIPR's agreement, demotes the announcement and leaks clinical from boilerplate."
        ),
        "gold_correction": "none",
        "generic_fix": "Resolve cross-listing aliases to one issuer with the point-in-time security used by gold, preserve explicit counterparty financing, recognize current expansion announcements, and exclude company-description boilerplate.",
    },
    "N1446": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_thesis_concept_leak"],
        "notes": "Singular Research reiterated Buy and described expected sales and margin growth. Gold correctly keeps the current concept as analyst_action; V9 incorrectly converts the analyst's forecast rationale into an earnings event.",
        "gold_correction": "none",
        "generic_fix": "Within analyst-event articles, retain forecast rationale as evidence under analyst_action rather than emitting earnings/guidance concepts absent a contemporaneous issuer report.",
    },
    "N1447": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_thesis_concept_leak"],
        "notes": "Barclays initiated CELG at Equal Weight with balanced long-term thesis. Gold neutral analyst_action context is correct; V9 leaks discussed franchises, approvals, pipeline and outlook into four separate issuer-event concepts.",
        "gold_correction": "none",
        "generic_fix": "For analyst-event documents, bind discussed products, approvals, results and outlook to analyst rationale unless the text states a new contemporaneous issuer event.",
    },
    "N1448": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_options_activity", "v9_options_boilerplate_concepts"],
        "notes": (
            "The article contains current, issuer-specific unusual options flow with a clearly bearish aggregate interpretation. It should produce a contextual VST issuer unit under options_activity, "
            "not no_supported_event. V9 emits an issuer but invents earnings and credit_solvency from About/current-position boilerplate."
        ),
        "gold_correction": "Emit VST as a contextual issuer unit with negative text sentiment, options_activity, forecast/reaction ineligible and issuer-history eligible; retain automated_summary role/origin.",
        "generic_fix": "Add explicit options_activity extraction for unusual-flow summaries and exclude About, scheduled-earnings and historical-bankruptcy boilerplate from current concepts.",
    },
    "N1449": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "The Aptiv preview reports declining consensus estimates ahead of results. Gold and V9 fully agree on negative contextual earnings-preview semantics and ineligibility as a new reaction trigger.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1450": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_market_proxy_decision"],
        "notes": "The Reuters-attributed statement concerns bilateral trade policy, while SPY is only a market proxy. Gold non_issuer_market_content is correct; V9 lets the provider ticker turn it into no_supported_event.",
        "gold_correction": "none",
        "generic_fix": "Do not treat broad ETFs or market proxies as named issuer subjects when the article is purely macro/policy content.",
    },
    "N1451": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_macro_proxy_decision", "v9_government_regulatory_confusion", "v9_liveblog_origin"],
        "notes": (
            "This is a live macro recap of Greece-Eurogroup negotiations. The listed bank/ETF symbols are market gauges, not issuer event subjects. "
            "Gold non-issuer editorial aggregation is correct; V9 calls it no_supported_event/regulatory_event/editorial_original."
        ),
        "gold_correction": "none",
        "generic_fix": "Classify government negotiations and bailout live blogs as macro editorial aggregation, distinguish them from issuer regulation, and ignore quoted market gauges as issuer candidates.",
    },
    "N1452": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance", "v9_post_result_trigger", "v9_conditional_regulatory"],
        "notes": (
            "This CNBC follow-up discusses already-released results and explicitly says RCL raised annual guidance. Gold should include guidance alongside earnings while remaining contextual. "
            "V9 incorrectly treats the interview as a fresh primary trigger and adds regulatory from a conditional future Cuba clearance."
        ),
        "gold_correction": "Add guidance to RCL alongside earnings; retain positive sentiment, editorial-analysis role and forecast/reaction ineligibility.",
        "generic_fix": "Distinguish post-result interviews from primary releases and require an actual regulator action before emitting regulatory rather than a conditional clearance discussion.",
    },
    "N1453": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_preview_precedence", "v9_aggregation_origin", "v9_observed_move_concept"],
        "notes": "The article is an earnings preview enriched with prior dividend and analyst actions. Gold preview/aggregation semantics are correct. V9 lets the analyst list override preview role, calls it original and adds market_reaction from a 0.02% prior close move.",
        "gold_correction": "none",
        "generic_fix": "Give explicit ahead-of-earnings preview structure precedence over embedded analyst history, retain aggregation provenance, and exclude prior price observations from concepts.",
    },
    "N1454": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "Carmike commenced a common-share offering with an overallotment option. Gold and V9 fully agree on issuer-direct negative financing trigger semantics.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1455": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_only_contract"],
        "notes": "The title itself unambiguously reports CMTL's $5.5M follow-on contract win. Gold positive contract trigger semantics are supported; V9 identifies the issuer but emits no concept, direction or eligibility because no body is stored.",
        "gold_correction": "none",
        "generic_fix": "Parse explicit title-only contract wins as positive contract_order triggers without requiring body text.",
    },
    "N1456": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_observed_move_trigger"],
        "notes": "This premarket follow-up was published after PETS missed earnings and was already down 16%. Gold correctly preserves negative earnings context but excludes it as a fresh trigger. V9 promotes it to primary and forecast/reaction eligible.",
        "gold_correction": "none",
        "generic_fix": "When a report leads with an observed move caused by already-released results, classify it as why_moving_followup and suppress fresh trigger eligibility.",
    },
    "N1457": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_summary_balance", "v9_analyst_boilerplate_concepts"],
        "notes": "The automated analyst summary contains bearish rating distribution but an increased average target implying upside, so gold mixed is supported. V9 neutralizes the conflict and leaks earnings/guidance from generic analyst-method boilerplate.",
        "gold_correction": "none",
        "generic_fix": "Aggregate opposing rating and target evidence as mixed, and exclude generic explanations of analyst forecasts from issuer concepts.",
    },
    "N1458": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_incidental_customer_identity", "v9_analyst_thesis_concept_leak"],
        "notes": "The article is analyst research about ADBE after results. Facebook and T-Mobile are only customer success examples, not issuer-event subjects. Gold correctly labels ADBE only; V9 emits FB and turns analyst customer examples into product_commercial.",
        "gold_correction": "none",
        "generic_fix": "Exclude named customer examples from issuer units and keep commercial examples inside analyst rationale unless a new event is explicitly announced for that customer.",
    },
    "N1459": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance", "v9_observed_move_concept", "v9_earnings_origin"],
        "notes": "RAD reported mixed quarterly results and explicitly lowered annual earnings guidance. Gold should include guidance alongside earnings. V9 also adds market_reaction from the premarket decline and calls the provider-authored update issuer_direct.",
        "gold_correction": "Add guidance to RAD alongside earnings; retain mixed direction and trigger eligibility.",
        "generic_fix": "Preserve explicit lowered outlook as guidance, exclude observed price moves, and classify provider-authored result summaries as editorial aggregation.",
    },
    "N1460": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_mixed_balance", "v9_analyst_thesis_concept_leak"],
        "notes": "UBS downgraded WM to Neutral while raising its target and forecasting stronger growth, supporting gold mixed analyst context. V9 reduces it to negative and turns the analyst forecast into earnings.",
        "gold_correction": "none",
        "generic_fix": "Balance rating direction, target change and stated thesis when scoring analyst sentiment, and keep projected results under analyst_action rather than earnings.",
    },
    "N1461": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_earnings_wire_origin", "v9_periodic_sales_trigger"],
        "notes": "UMC's May sales fell year over year, a current negative periodic-results trigger. The Benzinga text reports the company figure but is not an issuer-authored release, so gold issuer_direct provenance is unsupported. V9 wrongly demotes the event and suppresses trigger eligibility.",
        "gold_correction": "Change source_origin from issuer_direct to editorial_aggregation; retain negative earnings trigger semantics.",
        "generic_fix": "Recognize current monthly sales reports as primary earnings/results triggers while deriving provenance from actual authorship, not the verb reported.",
    },
    "N1462": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_named_counterparty", "v9_title_only_legal", "v9_provider_ticker_dependence"],
        "notes": "The title reports a bilateral patent-peace agreement between Finjan and publicly traded Mimecast. Gold correctly labels FNJN but omits MIME solely because provider metadata omitted it. V9 also misses the legal event and positive direction entirely.",
        "gold_correction": "Retain FNJN and add MIME as a positive legal-event counterparty with forecast/reaction and issuer-history eligibility, resolved point-in-time from the named company.",
        "generic_fix": "Resolve explicitly named public counterparties beyond provider tickers and parse title-only patent settlement/peace agreements as positive legal triggers for each affected issuer.",
    },
    "N1463": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_ma_analyst_scope", "v9_cross_issuer_sentiment", "v9_aggregation_origin"],
        "notes": "The article aggregates several analyst views on uncertain INTC-ALTR negotiations. Gold correctly combines M&A and analyst context, positive for the target and mixed for the acquirer. V9 substitutes contract/product concepts, loses acquirer uncertainty and aggregation provenance.",
        "gold_correction": "none",
        "generic_fix": "Recognize analyst discussion of an active M&A rumor as ma_transaction plus analyst_action, assign target/acquirer sentiment separately, and preserve multi-source aggregation provenance.",
    },
    "N1464": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_analyst_action", "v9_cashtag_identity", "v9_short_seller_role"],
        "notes": "The attributed Spruce Point short thesis explicitly targets $TWOU and challenges its business assumptions. Gold should include analyst_action rather than none. V9 misses the issuer because provider tickers are empty and misclassifies the quoted tweet as a market roundup.",
        "gold_correction": "Add analyst_action to TWOU; retain negative contextual sentiment, analyst_event/research provenance and trigger ineligibility.",
        "generic_fix": "Resolve explicit cashtags as issuer candidates and classify attributable short-seller theses as analyst/research context, not market roundups.",
    },
    "N1465": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_acquirer_role_scope"],
        "notes": "3M completed its cash tender offer and described the second-step merger. Gold correctly labels positive target and neutral acquirer M&A triggers. V9 handles COGT but drops the same transaction concept and trigger status from MMM.",
        "gold_correction": "none",
        "generic_fix": "For bilateral completed acquisitions, emit ma_transaction and trigger eligibility for both target and acquirer while maintaining role-specific direction.",
    },
    "N1466": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_issuer_identity", "v9_reaffirmed_growth_guidance"],
        "notes": "ICON explicitly reaffirmed revenue and EPS guidance showing substantial year-over-year growth. Gold positive guidance trigger is supported. V9 recognizes article role but emits no ICLR unit, concept or eligibility despite provider ticker and title identity.",
        "gold_correction": "none",
        "generic_fix": "Resolve issuer brand names through the in-memory identity authority and parse reaffirmed guidance with stated growth as a positive trigger.",
    },
    "N1467": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_ai_disclaimer_role", "v9_context_balance", "v9_historical_ipo_concept"],
        "notes": "This is Benzinga editorial analysis of Burry's broad valuation warning, using SpaceX's strong debut as counterevidence. Gold mixed SPCX valuation/market context is supported. V9 lets an AI-assistance disclaimer override the editorial role, neutralizes the conflict and emits financing from the earlier IPO description.",
        "gold_correction": "none",
        "generic_fix": "Do not classify editorial articles as automated summaries solely from an AI-assistance disclaimer; preserve opposing valuation and performance evidence and keep prior IPO facts contextual.",
    },
    "N1468": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_asset_sale", "v9_mover_all_rows", "v9_results_verb_confusion", "v9_structural_automation"],
        "notes": "The list records four 52-week highs; only CALI has a causal catalyst, an announced $62.3M asset sale. Gold should add ma_transaction to CALI. V9 emits only CALI, mistakes reported that it sold for earnings, and misses the other three contextual move units.",
        "gold_correction": "Add ma_transaction to CALI alongside market_reaction; retain positive contextual direction and all four issuer-history units.",
        "generic_fix": "Parse every row of structured mover lists, distinguish asset-sale predicates from earnings, and recognize structurally automated high/low lists even without an explicit footer.",
    },
    "N1469": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_title_wire_origin", "v9_parenthetical_loss_eps"],
        "notes": "The title reports DRTS nine-month loss per share and is sufficient for a negative earnings unit, but Benzinga Newsdesk authorship does not support gold issuer_direct provenance. V9 fails to parse accounting-parentheses loss notation and drops the unit.",
        "gold_correction": "Change source_origin from issuer_direct to editorial_aggregation; retain negative earnings trigger semantics.",
        "generic_fix": "Parse parenthetical EPS such as $(0.31) as a negative value and classify provider-authored title wires as editorial aggregation.",
    },
    "N1470": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_blog_origin", "v9_cross_issuer_market_share", "v9_historical_ma_context", "v9_external_bot_page"],
        "notes": "The Zacks analyst blog compares UAE handset positions and cites Microsoft's pending Nokia acquisition. Gold scopes Nokia, Apple, BlackBerry and Microsoft correctly. V9 loses the comparative sentiment/concepts and misreads generic sales/regulator words. The external lane is an access-denial bot page and must stay rejected.",
        "gold_correction": "none",
        "generic_fix": "Preserve attributed analyst-blog aggregation, bind market-share comparisons and historical M&A to the correct issuer, and reject bot/access-denial external pages.",
    },
    "N1471": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_earnings_wire_origin"],
        "notes": "GPS beat the sales estimate and grew year over year, supporting positive earnings trigger semantics. The provider-authored compact wire is not issuer-authored, so gold and V9 issuer_direct provenance is unsupported even though their remaining labels agree.",
        "gold_correction": "Change source_origin from issuer_direct to editorial_aggregation; retain all issuer-level labels.",
        "generic_fix": "Derive source origin from actual authorship; a provider report of company results is editorial aggregation, not issuer_direct.",
    },
    "N1472": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_automation_false_positive", "v9_dual_miss_balance"],
        "notes": "Oracle missed both EPS and revenue estimates; year-over-year sales growth does not outweigh two consensus misses. Gold negative primary earnings trigger is supported. V9 calls the compact Newsdesk wire automated, scores it mixed and suppresses trigger eligibility.",
        "gold_correction": "none",
        "generic_fix": "Require structural or explicit automation evidence before automated_summary classification and weight simultaneous EPS/revenue misses above background year-over-year growth.",
    },
    "N1473": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_source_entity_typo_resolution", "v9_multi_analyst_scope", "v9_historical_ma_context"],
        "notes": (
            "The source contains an internal entity typo: it says Charter was downgraded to a $39 target after correctly stating Charter was upgraded to $361; the later Dish thesis makes clear the downgrade belongs to DISH. "
            "Gold resolves the intended subjects correctly. V9 takes the malformed sentence literally, makes CHTR negative, drops DISH and misses VZ/T analyst and historical M&A context."
        ),
        "gold_correction": "none",
        "generic_fix": "Use issuer identity, target-price plausibility, surrounding thesis headings and noncontradiction checks to quarantine source entity typos; then scope every rating and historical M&A clause to its resolved issuer.",
    },
    "N1474": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_missing_guidance_contract", "v9_analyst_context_policy"],
        "notes": "Jefferies raised TLVT's target after an EPS beat and explicitly cited reaffirmed FY guidance and a recent $127M contract. Because gold already retains the contemporaneous earnings fact, omitting equally explicit guidance and contract evidence is inconsistent.",
        "gold_correction": "Add guidance and contract_order to TLVT alongside analyst_action and earnings; retain positive contextual analyst semantics.",
        "generic_fix": "Apply one consistent policy to explicit contemporaneous factual catalysts cited in analyst research: retain them as contextual concepts without making the article a fresh trigger.",
    },
    "N1475": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_observed_move_inconsistency", "v9_company_name_guidance", "v9_roundup_compact_sentiment", "v9_structural_automation"],
        "notes": (
            "Gold scopes only roundup rows with supported catalysts, but inconsistently adds market_reaction to EXPE/TRIP while omitting the same observed-move concept from the other catalyst rows. "
            "V9 invents guidance units for companies named Guidance Software and NCI, misses FLO earnings and neutralizes EXPE's positive result."
        ),
        "gold_correction": "Remove market_reaction from EXPE and TRIP so causal concept families are applied consistently; retain earnings and all existing catalyst issuer units.",
        "generic_fix": "Parse roundup clauses locally, prevent company-name tokens such as Guidance from becoming concepts, retain compact result sentiment, and recognize formulaic market updates as automated summaries.",
    },
    "N1476": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_joint_partnership_trigger", "v9_issuer_release_origin"],
        "notes": "ServiceNow and NVIDIA jointly announced an expanded partnership and co-development program. Gold correctly emits positive contract/product triggers for both. V9 finds the concepts but demotes the release to editorial analysis, calls it original editorial and suppresses trigger eligibility.",
        "gold_correction": "none",
        "generic_fix": "Recognize joint company-announcement language as issuer-direct primary events and preserve trigger eligibility for every explicit public counterparty.",
    },
    "N1477": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_only_analyst_clinical", "v9_analyst_origin"],
        "notes": "RBC's title-only note explicitly describes negative clinical-outcome risk for BIIB. Gold negative analyst_action plus clinical context is supported. V9 neutralizes the quote, misses clinical and misclassifies analyst-research provenance.",
        "gold_correction": "none",
        "generic_fix": "Parse attributable title-only analyst risk statements with their domain concept and classify explicit research notes as analyst_research.",
    },
    "N1478": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_cross_issuer_scope", "v9_analyst_thesis_concept_leak"],
        "notes": "Cowen reiterated ACB as a positive top pick and used CGC's large loss as a negative comparator. Gold correctly separates the issuers. V9 leaks projected products/results into ACB concepts and neutralizes CGC while dropping its analyst_action context.",
        "gold_correction": "none",
        "generic_fix": "Scope analyst comparisons by issuer, keep forecast product discussion under analyst rationale, and preserve explicit negative comparator evidence and analyst_action for the compared issuer.",
    },
    "N1479": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_secondary_results_trigger", "v9_mixed_results_guidance", "v9_observed_move_concept", "v9_external_source_drift"],
        "notes": (
            "The FreightWaves analysis follows Delta's current results: large misses/losses offset by cash improvement and positive forward guidance, supporting gold mixed earnings/guidance context. "
            "V9 promotes the secondary analysis to a fresh trigger, scores only positive and adds market_reaction. The unrelated external airline corpus must remain excluded."
        ),
        "gold_correction": "none",
        "generic_fix": "Distinguish secondary result analysis from the primary release, balance current misses against outlook, exclude observed moves, and reject unrelated external corpora.",
    },
    "N1480": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": ["candidate_alias_common_noun"],
        "notes": (
            "This is non-issuer geopolitical analysis about Iranian asset seizure and military escalation, with no provider ticker or public-company event. "
            "Gold and V9 correctly emit no issuer and non_issuer_market_content. The frozen candidate metadata nevertheless proposes JYNT from the common phrase 'joint company announcement'; that alias collision must never become issuer evidence."
        ),
        "gold_correction": "none",
        "generic_fix": "Require unambiguous company-name context for common-word issuer aliases such as 'the joint'; never admit them from ordinary prose alone.",
    },
    "N1481": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_title_only_bilateral_license"],
        "notes": "The title is a complete bilateral commercial-license announcement. Gold correctly labels both Atomera and STMicroelectronics positive contract counterparties; V9 emits neither.",
        "gold_correction": "none",
        "generic_fix": "Parse title-only signed license and commercial-agreement predicates and emit role-scoped units for both explicit public counterparties.",
    },
    "N1482": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_mover_recap_issuer_omission", "v9_market_reaction_context"],
        "notes": "This is an automated sector mover recap explicitly saying no issuer news exists. Gold correctly retains every named mover as neutral market-reaction history while making all trigger flags false. V9 drops all nine issuers.",
        "gold_correction": "none",
        "generic_fix": "For mover-list structures, resolve every explicit company/ticker pair, emit market_reaction context, preserve issuer history, and never promote price movement into a causal trigger.",
    },
    "N1483": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_cross_listing_alias", "v9_clinical_product_leak"],
        "notes": "PHRRF and CSE:PHRM are two symbols for the same PharmaTher issuer. Gold correctly applies the clinical/regulatory FDA-IND event to both. V9 labels only PHRRF and adds product_commercial even though no commercialization event occurred.",
        "gold_correction": "none",
        "generic_fix": "Expand a resolved issuer event to its explicit point-in-time cross-listing symbols and distinguish clinical-study authorization from product commercialization.",
    },
    "N1484": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_roundup_incomplete_units", "v9_roundup_role", "v9_roundup_scope", "v9_observed_move_concept"],
        "notes": "The market primer contains legitimate issuer passages for ARO, DIS, GRPN, BKS, KR, TIF, and ZUMZ. Gold omitted the four earnings-preview issuers BKS, KR, TIF, and ZUMZ. Barclays is the source of a copper view rather than the subject and remains excluded. V9 also misclassifies the article as a preview, adds market_reaction to causal event concepts, and misses GRPN.",
        "gold_correction": "Add contextual issuer-history units for BKS, KR, TIF, and ZUMZ with earnings; TIF also has market_reaction. Keep all forecast/reaction eligibility false and keep BCS excluded.",
        "generic_fix": "Segment market primers by section, emit every substantive issuer passage, distinguish forthcoming earnings previews from realized events, and exclude source firms and observed moves from causal concepts.",
    },
    "N1485": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_activist_filing_provenance", "v9_ma_role_direction", "v9_target_omission", "v9_concept_scope"],
        "notes": "JANA's filed letter argues that EQT's proposed Rice acquisition overpays, dilutes EQT holders, and transfers value to Rice holders. Gold correctly makes EQT negative and Rice positive with role-specific M&A concepts. V9 reverses EQT, omits Rice, and invents capital_return/product concepts.",
        "gold_correction": "none",
        "generic_fix": "Recognize filed activist communications, resolve named M&A targets, and assign dilution/value-transfer evidence by acquirer/target role without leaking hypothetical alternatives into concepts.",
    },
    "N1486": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_numeric_earnings_comparison"],
        "notes": "Apple Mac sales increased from $7.16B to $8.675B. Gold's positive earnings trigger is supported; V9 recognizes earnings but fails to interpret the explicit year-over-year numeric comparison.",
        "gold_correction": "none",
        "generic_fix": "Evaluate same-metric current-versus-prior numeric comparisons with units and direction, including title-only segment results.",
    },
    "N1487": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_roundup_origin", "v9_observed_move_concept", "v9_mixed_metric_balance", "v9_guidance_omission"],
        "notes": "The structured earnings roundup has complete, well-scoped gold units. V9 is broadly correct but calls the aggregation editorial, adds market_reaction without observed price moves, misses Ipsen guidance, and underweights mixed metric combinations for FLWS and HNHPF.",
        "gold_correction": "none",
        "generic_fix": "Recognize templated earnings roundups as automated summaries, add only explicit forward guidance, and balance beat/miss and year-over-year metrics per issuer without a synthetic market-reaction concept.",
    },
    "N1488": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_portfolio_concept_misuse", "v9_portfolio_scope", "v9_context_sentiment", "v9_external_bot_text"],
        "notes": "This is an author's historical portfolio diary, not fresh issuer news. Gold incorrectly calls the SLW thesis and UUP technical reduction guidance, and omits explicit market-reaction context for BUCY, KMP, and UUP plus the stated upcoming earnings context for ROVI. V9 also misses several issuer passages and lets nearby earnings language leak across positions. The parked-domain external page is invalid.",
        "gold_correction": "Replace SLW guidance with no concept; replace UUP guidance with market_reaction; add market_reaction to BUCY and KMP; add earnings to ROVI. Preserve all as issuer-history-only context.",
        "generic_fix": "Segment portfolio diaries by bullet, scope concepts and sentiment within each position, retain contextual actions/history, and reject parked-domain or bot external text.",
    },
    "N1489": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_generated_story_role", "v9_issuer_metric_balance", "v9_financing_ownership_scope"],
        "notes": "The explicit Benzinga Neuro disclosure supports automated_summary. Gold correctly treats the ARK trades and cited issuer fundamentals as historical context. V9 mistakes it for why-moving, reverses Block despite raised guidance, and misses Deere guidance and Robinhood insider-sales ownership evidence.",
        "gold_correction": "none",
        "generic_fix": "Honor explicit generated-story provenance, segment fund-trade stories by issuer, and balance financing, guidance, ownership, earnings, and growth evidence within each issuer passage.",
    },
    "N1490": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_event_preview_role", "v9_preview_provenance", "v9_clinical_preview_concept"],
        "notes": "Crinetics only scheduled a future call about product progress and Phase 2 data. Gold correctly labels a neutral issuer-direct clinical preview with history eligibility only. V9 calls it a why-moving follow-up and drops the clinical context.",
        "gold_correction": "none",
        "generic_fix": "Recognize scheduled calls/webcasts and future data presentations as previews, preserve their domain concept, and do not infer why-moving from 'reported' or 'highlighting'.",
    },
    "N1491": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_analyst_rationale_concept_leak"],
        "notes": "The article is a clean Watsco downgrade and target cut. Gold correctly limits the event concept to analyst_action. V9 adds capital_return only because the analyst rationale discusses a past special dividend.",
        "gold_correction": "none",
        "generic_fix": "In analyst events, keep historical rationale as evidence but do not create additional event concepts unless the article reports a new corresponding issuer action.",
    },
    "N1492": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_nonissuer_regulatory_role", "v9_attributed_aggregation_origin"],
        "notes": "This is a non-issuer regulatory-policy article about a proposed crypto tax exemption. Gold correctly uses regulatory_event and editorial_aggregation because the account explicitly attributes reporting to Finbold and The Block. V9 reduces it to generic editorial analysis/original.",
        "gold_correction": "none",
        "generic_fix": "Classify enacted, proposed, or introduced government rules as regulatory events even without an issuer and infer aggregation when substantive claims are explicitly attributed to outside reporting.",
    },
    "N1493": {
        "gold_status": "pass",
        "v9_status": "pass",
        "issue_codes": [],
        "notes": "The short feed-derived opinion concerns the Fukushima disaster without a tradable issuer. Gold and V9 correctly classify it as non-issuer editorial analysis.",
        "gold_correction": "none",
        "generic_fix": "none",
    },
    "N1494": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_comparator_strategy_concept", "v9_contextual_sentiment"],
        "notes": "Cramer's secondary analysis is negative on Target, positive on Amazon's competitive position, and positive on Best Buy's service strategy. Gold omits strategy_valuation for BBY. V9 finds all issuers and Target's earnings/guidance but neutralizes all three issuer-specific theses.",
        "gold_correction": "Add strategy_valuation to BBY; retain all three units as non-trigger editorial context.",
        "generic_fix": "Score attributable comparative theses per issuer and add strategy_valuation only to the issuer whose competitive strategy is substantively evaluated.",
    },
    "N1495": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_nonissuer_macro_decision"],
        "notes": "The Reuters headline is geopolitical non-issuer content; KSA and SPY are market proxies rather than issuer subjects. Gold is correct. V9 emits no issuer but uses no_supported_event instead of the explicit non-issuer decision.",
        "gold_correction": "none",
        "generic_fix": "When the text is substantive geopolitical or macro news but has no issuer subject, emit non_issuer_market_content rather than no_supported_event.",
    },
    "N1496": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_nonissuer_policy_role", "v9_nonissuer_macro_decision"],
        "notes": "The Reuters headline reports a concrete Treasury authorization affecting oil markets. It remains non-issuer content, but regulatory_event is more precise than gold's editorial_analysis. SPY and USO are market proxies, not issuer units.",
        "gold_correction": "Change content_role from editorial_analysis to regulatory_event; retain non_issuer_market_content and editorial_aggregation.",
        "generic_fix": "Recognize concrete government authorizations and policy actions as non-issuer regulatory events and do not promote market-proxy tickers into issuer units.",
    },
    "N1497": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_ipo_preview_scope", "v9_listing_financing_distinction", "v9_incidental_customer_units"],
        "notes": "Gold correctly limits issuer units to the three offering subjects. Starbucks, Target, and Alibaba are merely Oatly customer/partner examples and are not issuer units. V9 emits those incidental firms, confuses Squarespace's direct listing with financing, and misses growth/financial context for the offering subjects.",
        "gold_correction": "none",
        "generic_fix": "Segment IPO previews by candidate issuer, distinguish direct listings from capital-raising offerings, and suppress customer/partner examples that have no issuer-specific event or thesis.",
    },
    "N1498": {
        "gold_status": "correction_required",
        "v9_status": "fix_required",
        "issue_codes": ["gold_analyst_role_origin", "gold_incidental_competitors", "gold_missing_guidance_legal", "v9_external_bot_text"],
        "notes": "The body is a Zacks analyst blog, while the external lane is an access-denial bot page. Gold incorrectly calls it editorial analysis and includes Office Depot and Staples solely because they are named competitors. The OfficeMax passage is mixed and contains earnings/outlook, a tax settlement, and a five-year contract.",
        "gold_correction": "Change role/origin to analyst_event/analyst_research; remove ODP and SPLS issuer units; add guidance and legal to OMX alongside earnings and contract_order; keep OMX forecast/reaction ineligible.",
        "generic_fix": "Recognize Zacks analyst-blog signatures, reject access-denial external text, suppress incidental competitor mentions, and scope settlement, guidance, and contract evidence to the analyzed issuer.",
    },
    "N1499": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_asset_purchase_ma_pattern"],
        "notes": "The title states that an American Water Works subsidiary purchased another water company's operating assets. Gold's positive acquirer M&A trigger is supported; V9 emits the issuer but misses the event and direction.",
        "gold_correction": "none",
        "generic_fix": "Recognize subsidiary purchases/acquisitions of operating assets as acquirer-side M&A events even when terms are undisclosed and no body exists.",
    },
    "N1500": {
        "gold_status": "pass",
        "v9_status": "fix_required",
        "issue_codes": ["v9_earnings_magnitude_balance"],
        "notes": "A 72.73% EPS miss, 75% EPS decline, and 8.61% sales decline outweigh a 1.30% sales beat. Gold correctly calls the result negative; V9's unweighted evidence presence produces mixed.",
        "gold_correction": "none",
        "generic_fix": "Weight same-issuer earnings evidence by metric importance and stated magnitude so a small revenue beat cannot cancel a severe EPS miss and broad year-over-year deterioration.",
    },
}

def build_review_specs() -> list[dict[str, Any]]:
    return [dict(sample_id=sample_id, **value) for sample_id, value in sorted(REVIEWS.items())]


__all__ = ["build_review_specs"]

