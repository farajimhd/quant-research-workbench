# SEC Disclosure Taxonomy and Embedding Workload

Generated UTC: `2026-07-16T12:54:06Z`

## Verdict

The approved taxonomy contains `240` manually reviewed rules: `149` numbered definitions scraped from the SEC Forms Index, `67` manually curated EDGAR submission types absent from that index, and `24` document rules. Fuzzy title distance is candidate evidence only and never changes the authoritative label.

Observed source workload resolved by approved taxonomy rules: `23,992,352` rows / `8.787T` characters. Source-policy accounting marks `6,988,806` rows / `1.558T` source characters for embedding.

Actual rendered input is smaller: `21,018,802` resolved rendered rows / `728.402B` characters. Of these, `4,609,777` rows / `305.919B` characters are currently eligible for Qwen embedding. At report time, the v3 token and embedding tables do not yet exist.

Unresolved observed types remain blocked for manual review: `745` groups / `9.472B` characters. They are preserved in source and rendered storage; they are not silently embedded.

The renderer remains uncapped for every class. `renderer_text_limit_chars` is NULL. Eligible documents use complete 1,024-token chunks with NULL `max_chunks` and NULL `max_total_tokens`; structured datasets and technical duplicates are routed to structured extraction or preservation rather than lossy text clipping.

## Matching Method

1. Exact submitted SEC form number is authoritative.
2. Explicitly approved document prefix or exact rules are authoritative.
3. Title candidates use normalized token coverage, ordered coverage, minimum ordered-span density, and character similarity.
4. Fuzzy matches remain `manual_review_required`; approval requires an edited taxonomy publication.

## Actual Database Size Distribution

| Layer | Rows | Filings | Characters | P50 | P90 | P99 | P99.9 | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Full source text | 24,378,949 | 5,886,650 | 8.796T | 22.489K | 170.797K | 3.010M | 92.513M | 601.341M |
| Resolved rendered text | 21,018,802 | n/a | 728.402B | 6.568K | 50.202K | 504.588K | 2.661M | 99.049M |
| Eligible rendered text | 4,609,777 | n/a | 305.919B | 10.910K | 141.668K | 829.687K | n/a | n/a |

Counts use logical current rows from `FINAL`. Eligible rendered rows are joined by `document_id` to full source metadata, resolved through the approved taxonomy, and filtered by the model policy. They are the actual documents the v3 embedding extractor should process.

## Approved Taxonomy

| Scope | Match | Type | Official or canonical title | Category | Impact | Score | Source rows | Source chars | Rendered rows | Rendered chars | Embed | Strategy |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| document | prefix | EX- | Other submitted exhibit | exhibit | Exhibit-dependent | 2 | 116,008 | 4.555B | 115,627 | 1.224B | yes | complete_document_chunks |
| document | prefix | EX-1 | Underwriting or distribution agreement exhibit | exhibit | Exhibit-dependent | 2 | 111,565 | 14.072B | 110,512 | 4.807B | yes | complete_document_chunks |
| document | prefix | EX-10 | Material contract exhibit | material_contract | Potentially material; parent-context dependent | 4 | 369,422 | 63.345B | 363,913 | 25.282B | yes | complete_document_chunks |
| document | prefix | EX-101 | XBRL data exhibit | technical_representation | Technical representation; no independent catalyst | 0 | 0 | 0 | 0 | 0 | no | structured_extraction_only |
| document | prefix | EX-102 | Asset-level data file | structured_finance | Structured-product disclosure | 2 | 66,557 | 3.985T | 7 | 498.966K | no | structured_extraction_only |
| document | prefix | EX-103 | Asset-level data supporting file | structured_finance | Structured-product disclosure | 2 | 66,248 | 773.676M | 3 | 109.732K | no | structured_extraction_only |
| document | prefix | EX-104 | Cover page interactive data exhibit | technical_representation | Technical representation; no independent catalyst | 0 | 0 | 0 | 0 | 0 | no | structured_extraction_only |
| document | prefix | EX-106 | Asset-level data file | structured_finance | Structured-product disclosure | 2 | 1 | 45.454K | 1 | 17.360K | no | structured_extraction_only |
| document | prefix | EX-2 | Transaction agreement exhibit | transaction_exhibit | High potential; parent-transaction dependent | 5 | 396,351 | 13.865B | 387,530 | 5.191B | yes | complete_document_chunks |
| document | prefix | EX-3 | Charter or bylaws exhibit | exhibit | Exhibit-dependent | 2 | 61,407 | 6.876B | 57,609 | 2.881B | yes | complete_document_chunks |
| document | prefix | EX-31 | Officer certification | administrative | Administrative or compliance support | 1 | 386,959 | 4.790B | 386,915 | 1.351B | no | preserve_only |
| document | prefix | EX-32 | Section 906 certification | administrative | Administrative or compliance support | 1 | 309,531 | 2.046B | 309,490 | 371.886M | no | preserve_only |
| document | prefix | EX-33 | Asset-backed servicing compliance report | structured_finance | Structured-product disclosure | 2 | 43,702 | 21.425B | 43,658 | 2.288B | no | structured_extraction_only |
| document | prefix | EX-34 | Asset-backed servicing compliance assertion | structured_finance | Structured-product disclosure | 2 | 43,676 | 2.013B | 43,649 | 307.542M | no | structured_extraction_only |
| document | prefix | EX-35 | Asset-backed servicer compliance statement | structured_finance | Structured-product disclosure | 2 | 21,899 | 17.244B | 21,893 | 1.026B | no | structured_extraction_only |
| document | prefix | EX-36 | Asset-backed investor communication | structured_finance | Structured-product disclosure | 2 | 1,090 | 6.799M | 1,090 | 2.696M | no | structured_extraction_only |
| document | prefix | EX-4 | Security instrument or rights exhibit | exhibit | Exhibit-dependent | 2 | 117,227 | 32.947B | 115,577 | 13.484B | yes | complete_document_chunks |
| document | prefix | EX-99 | Additional exhibit or press release | additional_exhibit | Context-dependent; can be high | 4 | 1,268,947 | 328.462B | 1,248,473 | 45.462B | yes | complete_document_chunks |
| document | exact | EX-FILING FEES | Filing fee exhibit | administrative | Administrative or compliance support | 1 | 234,611 | 2.580B | 234,602 | 248.809M | no | preserve_only |
| document | exact | NPORT-EX | Form N-PORT Part F attachment | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 179,707 | 1.506T | 179,597 | 91.763B | no | structured_extraction_only |
| document | exact | PART II | Form narrative attachment: Part II | other_disclosure | Content-dependent disclosure | 2 | 2,828 | 1.768B | 2,826 | 352.754M | yes | complete_document_chunks |
| document | exact | PART II AND III | Form narrative attachment: Parts II and III | other_disclosure | Content-dependent disclosure | 2 | 7,412 | 8.627B | 7,405 | 2.476B | yes | complete_document_chunks |
| document | exact | PROXY VOTING RECORD | Proxy voting record attachment | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 11,175 | 40.120B | 2 | 8.550K | no | structured_extraction_only |
| document | exact | XML | Generic XML document | technical_representation | Technical representation; no independent catalyst | 0 | 14,605,847 | 748.899B | 14,605,181 | 172.615B | no | structured_extraction_only |
| form | exact | 1 | Application for registration or exemption from registration as a national securities exchange | administrative | Administrative or regulatory | 1 | 2,598 | 387.102K | 2,598 | 387.102K | no | preserve_only |
| form | exact | 1-A | Regulation A Offering Statement | offering | Offering and capital-structure relevance | 4 | 5,229 | 68.241M | 1 | 149 | yes | complete_document_chunks |
| form | exact | 1-E | Notification under Regulation E | administrative | Administrative or regulatory | 1 | 1 | 11.861K | 1 | 1.683K | no | preserve_only |
| form | exact | 1-K | Annual Reports and Special Financial Reports | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 2,820 | 4.808M | 0 | 0 | yes | complete_document_chunks |
| form | exact | 1-N | Form and amendments for notice of registration as a national securities exchange for the sole purpose of trading security futures products | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | 1-SA | Semiannual Report or Special Financial Report Pursuant to Regulation A | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 2,558 | 1.119B | 2,556 | 183.541M | yes | complete_document_chunks |
| form | exact | 1-U | Current Report Pursuant to Regulation A | current_event | High potential; event-dependent | 5 | 10,875 | 343.660M | 10,875 | 72.554M | yes | complete_document_chunks |
| form | exact | 1-Z | Exit Report Under Regulation A | other_disclosure | Content-dependent disclosure | 2 | 573 | 1.176M | 0 | 0 | yes | complete_document_chunks |
| form | exact | 10 | General form for registration of securities pursuant to Section 12(b) or (g) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 10-12B | Exchange Act securities registration statement | offering | Offering and capital-structure relevance | 4 | 416 | 123.700M | 416 | 27.517M | yes | complete_document_chunks |
| form | exact | 10-12G | Registration of a class of securities under the Exchange Act | other_disclosure | Content-dependent disclosure | 2 | 1,889 | 1.935B | 1,887 | 629.978M | yes | complete_document_chunks |
| form | exact | 10-D | Asset-Backed Issuer Distribution Report Pursuant to Section 13 or 15(d) of the Securities Exchange Act of 1934 | structured_finance | Structured-product disclosure | 2 | 68,531 | 2.469B | 68,531 | 483.138M | no | structured_extraction_only |
| form | exact | 10-K | Annual report pursuant to Section 13 or 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 60,383 | 161.986B | 60,379 | 23.021B | yes | complete_document_chunks |
| form | exact | 10-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Non-Resident General Partner of Broker or Dealer | administrative | Administrative or regulatory | 1 | 1 | 149 | 1 | 149 | no | preserve_only |
| form | exact | 10-Q | General form for quarterly reports under Section 13 or 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 138,577 | 278.067B | 138,573 | 27.497B | yes | complete_document_chunks |
| form | exact | 11-K | Annual reports of employee stock purchase, savings and similar plans pursuant to Section 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 7,506 | 2.997B | 7,504 | 353.897M | yes | complete_document_chunks |
| form | exact | 12B-25 | Notification of late filing | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | 13F | Information required of institutional investment managers pursuant to Section 13(f) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 13F-HR | Institutional investment manager holdings report | ownership | Ownership relevance; usually delayed | 2 | 215,425 | 480.771M | 0 | 0 | yes | complete_document_chunks |
| form | exact | 13H | Information Required of Large Traders Pursuant To Section 13(h) of the Securities Exchange Act of 1934 | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 144 | Notice of proposed sale of securities pursuant to Rule 144 | insider_ownership | Insider ownership or sale signal | 3 | 119,540 | 852.871M | 820 | 6.085M | yes | complete_document_chunks |
| form | exact | 15 | Certification and notice of termination of registration under Section 12(g) or suspension of duty to file reports under Sections 13 and 15(d) | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | 15F | Certification of a foreign private issuer’s termination of registration of a class of securities under Section 12(g) or its termination of the duty to file reports under Section 13(a) or Section 15(d) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 17-H | Risk Assessment for Brokers & Dealers | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 18 | Application for registration pursuant to Section 12(b) & (c) of the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | 18-K | Annual report for foreign governments and political subdivisions thereof | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 1,344 | 54.195M | 1,344 | 12.743M | yes | complete_document_chunks |
| form | exact | 19B-4 | Proposed rule change by self-regulatory organization | other_disclosure | Content-dependent disclosure | 2 | 2 | 298 | 2 | 298 | yes | complete_document_chunks |
| form | exact | 19B-7 | Proposed rule change by self-regulatory organization | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 2-E | Report of sales pursuant to Rule 609 of Regulation E | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 20-F | Registration statement / Annual report / Transition report | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 8,077 | 39.928B | 8,077 | 6.373B | yes | complete_document_chunks |
| form | exact | 20FR12B | Foreign issuer Exchange Act securities registration statement | offering | Offering and capital-structure relevance | 4 | 168 | 499.319M | 168 | 112.581M | yes | complete_document_chunks |
| form | exact | 20FR12G | Foreign issuer Exchange Act securities registration statement | offering | Offering and capital-structure relevance | 4 | 62 | 151.467M | 62 | 31.458M | yes | complete_document_chunks |
| form | exact | 24F-2 | Annual notice of securities sold pursuant to Rule 24-f2 (with amendments adopted in 2024 RILAs release) | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | 24F-2NT | Investment-company annual notice of securities sold | administrative | Administrative or regulatory | 1 | 42,891 | 644.603M | 19,501 | 91.418M | no | preserve_only |
| form | exact | 25 | Notification of the removal from listing and registration of matured, redeemed or retired securities | administrative | Administrative or regulatory | 1 | 872 | 11.174M | 871 | 1.971M | no | preserve_only |
| form | exact | 253G1 | Regulation A offering circular | offering | Offering and capital-structure relevance | 4 | 207 | 245.114M | 207 | 70.002M | yes | complete_document_chunks |
| form | exact | 253G2 | Regulation A offering circular supplement | offering | Offering and capital-structure relevance | 4 | 4,258 | 1.860B | 4,258 | 561.069M | yes | complete_document_chunks |
| form | exact | 253G3 | Regulation A offering circular amendment | offering | Offering and capital-structure relevance | 4 | 62 | 42.678M | 62 | 11.803M | yes | complete_document_chunks |
| form | exact | 253G4 | Regulation A offering circular supplement | offering | Offering and capital-structure relevance | 4 | 5 | 1.567M | 5 | 446.401K | yes | complete_document_chunks |
| form | exact | 3 | Initial statement of beneficial ownership of securities | insider_ownership | Insider ownership or sale signal | 3 | 124,825 | 432.351M | 0 | 0 | yes | complete_document_chunks |
| form | exact | 4 | Statement of changes in beneficial ownership of securities | insider_ownership | Insider ownership or sale signal | 3 | 1,399,467 | 9.584B | 0 | 0 | yes | complete_document_chunks |
| form | exact | 40-17G | Investment-company fidelity bond filing | other_disclosure | Content-dependent disclosure | 2 | 4,366 | 1.584B | 4,344 | 372.339M | yes | complete_document_chunks |
| form | exact | 40-APP | Investment Company Act application for exemptive relief | administrative | Administrative or regulatory | 1 | 2,556 | 669.955M | 2,556 | 245.067M | no | preserve_only |
| form | exact | 40-F | Registration statement pursuant to Section 12 or annual report pursuant to Section 13(a) or 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 1,293 | 454.484M | 1,293 | 78.088M | yes | complete_document_chunks |
| form | exact | 424B1 | Prospectus filed under Rule 424(b)(1) | offering | Offering and capital-structure relevance | 4 | 346 | 901.553M | 346 | 208.811M | yes | complete_document_chunks |
| form | exact | 424B2 | Prospectus filed under Rule 424(b)(2) | offering | Offering and capital-structure relevance | 4 | 516,951 | 142.455B | 516,950 | 43.264B | yes | complete_document_chunks |
| form | exact | 424B3 | Prospectus filed under Rule 424(b)(3) | offering | Offering and capital-structure relevance | 4 | 54,363 | 58.400B | 54,117 | 12.387B | yes | complete_document_chunks |
| form | exact | 424B4 | Prospectus filed under Rule 424(b)(4) | offering | Offering and capital-structure relevance | 4 | 4,701 | 11.964B | 4,701 | 3.353B | yes | complete_document_chunks |
| form | exact | 424B5 | Prospectus filed under Rule 424(b)(5) | offering | Offering and capital-structure relevance | 4 | 22,238 | 15.370B | 22,238 | 5.699B | yes | complete_document_chunks |
| form | exact | 424B7 | Prospectus filed under Rule 424(b)(7) | offering | Offering and capital-structure relevance | 4 | 1,509 | 731.289M | 1,509 | 263.939M | yes | complete_document_chunks |
| form | exact | 424B8 | Prospectus filed under Rule 424(b)(8) | offering | Offering and capital-structure relevance | 4 | 1,404 | 310.441M | 1,404 | 106.918M | yes | complete_document_chunks |
| form | exact | 424H | Preliminary prospectus filed under Rule 424(h) | offering | Offering and capital-structure relevance | 4 | 1,007 | 7.178B | 1,007 | 1.525B | yes | complete_document_chunks |
| form | exact | 425 | Business-combination communication | corporate_transaction | High potential transaction relevance | 5 | 33,982 | 2.730B | 33,970 | 1.224B | yes | complete_document_chunks |
| form | exact | 485APOS | Post-effective investment-company amendment under Rule 485(a) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 8,463 | 18.508B | 8,463 | 5.825B | no | separate_fund_pipeline |
| form | exact | 485BPOS | Post-effective investment-company amendment under Rule 485(b) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 41,509 | 188.999B | 41,506 | 36.510B | no | separate_fund_pipeline |
| form | exact | 485BXT | Post-effective investment-company amendment extension | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 15,414 | 644.999M | 15,414 | 111.517M | no | separate_fund_pipeline |
| form | exact | 486APOS | Post-effective business-development-company amendment under Rule 486(a) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 213 | 388.099M | 213 | 153.700M | no | separate_fund_pipeline |
| form | exact | 486BPOS | Post-effective business-development-company amendment under Rule 486(b) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 1,181 | 2.688B | 1,181 | 868.732M | no | separate_fund_pipeline |
| form | exact | 487 | Investment-company pricing amendment | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 6,437 | 2.736B | 6,437 | 1.287B | no | separate_fund_pipeline |
| form | exact | 497 | Investment-company definitive materials | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 93,588 | 54.812B | 93,549 | 11.757B | no | separate_fund_pipeline |
| form | exact | 497K | Investment-company summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 159,355 | 14.750B | 159,348 | 3.934B | no | separate_fund_pipeline |
| form | exact | 497VPI | Variable insurance product initial summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 6,601 | 1.777B | 6,601 | 225.492M | no | separate_fund_pipeline |
| form | exact | 497VPU | Variable insurance product updated summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 12,319 | 2.929B | 12,319 | 299.398M | no | separate_fund_pipeline |
| form | exact | 5 | Annual statement of changes in beneficial ownership of securities | insider_ownership | Insider ownership or sale signal | 3 | 16,103 | 112.433M | 0 | 0 | yes | complete_document_chunks |
| form | exact | 6-K | Report of foreign private issuer pursuant to Rule 13a-16 or 15d-16 under the Securities Exchange Act of 1934 | current_event | High potential; event-dependent | 5 | 194,217 | 25.581B | 194,113 | 2.921B | yes | complete_document_chunks |
| form | exact | 7-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Individual Non-Resident Broker or Dealer | administrative | Administrative or regulatory | 1 | 2 | 298 | 2 | 298 | no | preserve_only |
| form | exact | 8-A | Registration of certain classes of securities pursuant to Section 12(b) or (g) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | 8-K | Current report pursuant to Section 13 or 15(d) | current_event | High potential; event-dependent | 5 | 527,687 | 20.530B | 527,677 | 3.444B | yes | complete_document_chunks |
| form | exact | 8-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Corporate Non-Resident Broker or Dealer | administrative | Administrative or regulatory | 1 | 12 | 1.788K | 12 | 1.788K | no | preserve_only |
| form | exact | 9-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Partnership Non-Resident Broker or Dealer | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | ABS DD-15E | Certification of Provider of Third-Party Due Diligence Services for Asset-Backed Securities | structured_finance | Structured-product disclosure | 2 | 0 | 0 | 0 | 0 | no | structured_extraction_only |
| form | exact | ABS-15G | Asset-Backed Securitizer Report | structured_finance | Structured-product disclosure | 2 | 20,989 | 100.677B | 20,989 | 51.747B | no | structured_extraction_only |
| form | exact | ABS-EE | Form for Submission of Electronic Exhibits for Asset-Backed Securities | structured_finance | Structured-product disclosure | 2 | 41,794 | 526.712M | 41,794 | 77.711M | no | structured_extraction_only |
| form | exact | ADV | Uniform Application for Investment Adviser Registration and Report by Exempt Reporting Advisers | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | ADV-E | Certificate of accounting of client securities and funds in the possession or custody of an investment adviser | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | ADV-H | Application for a temporary or continuing hardship exemption | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | ADV-NR | Appointment of agent for service of process by non-resident general partner and non-resident managing agent of an investment adviser | administrative | Administrative or regulatory | 1 | 98 | 14.602K | 98 | 14.602K | no | preserve_only |
| form | exact | ADV-W | Notice of withdrawal from registration as investment adviser | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | ARS | Annual report to security holders | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 603 | 516.780M | 549 | 220.587M | yes | complete_document_chunks |
| form | exact | ATS | Initial operation report, amendment to initial operation report and cessation of operations report for alternative trading systems | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | ATS-N | NMS Stock Alternative Trading Systems | other_disclosure | Content-dependent disclosure | 2 | 6 | 564.183K | 0 | 0 | yes | complete_document_chunks |
| form | exact | ATS-R | Quarterly report of alternative trading systems activities | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | BD | Uniform application for broker-dealer registration | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | BD-N | Notice of registration as a broker-dealer for the purpose of trading security futures products pursuant to Section 15(b)(11) of the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | BDW | Uniform request for broker-dealer withdrawal | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | C | Form C | offering | Offering and capital-structure relevance | 4 | 19,783 | 192.850M | 0 | 0 | yes | complete_document_chunks |
| form | exact | CA-1 | Registration or exemption from registration as a clearing agency and for amendment to registration | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | CB | Tender offer/rights offering notification form | corporate_transaction | High potential transaction relevance | 5 | 809 | 46.932M | 809 | 9.377M | yes | complete_document_chunks |
| form | exact | CFPORTAL | Application or Amendment to Application for Registration or Withdrawal from Registration as Funding Portal Under the Securities Exchange Act of 1934 | offering | Offering and capital-structure relevance | 4 | 574 | 5.150M | 0 | 0 | yes | complete_document_chunks |
| form | exact | CRS | Customer Relationship Summary | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | CUSTODY | Form Custody for Broker-Dealers | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | D | Notice of Exempt Offering of Securities | offering | Offering and capital-structure relevance | 4 | 414,181 | 3.325B | 0 | 0 | yes | complete_document_chunks |
| form | exact | DEF 14A | Definitive proxy statement | offering | Offering and capital-structure relevance | 4 | 40,761 | 43.777B | 40,744 | 9.182B | yes | complete_document_chunks |
| form | exact | DEF 14C | Definitive information statement | offering | Offering and capital-structure relevance | 4 | 2,889 | 790.934M | 2,889 | 243.360M | yes | complete_document_chunks |
| form | exact | DEFA14A | Additional definitive proxy soliciting material | offering | Offering and capital-structure relevance | 4 | 51,044 | 2.365B | 50,968 | 780.589M | yes | complete_document_chunks |
| form | exact | DEFC14A | Definitive proxy statement for contested solicitation | offering | Offering and capital-structure relevance | 4 | 657 | 495.464M | 657 | 115.324M | yes | complete_document_chunks |
| form | exact | DEFM14A | Definitive proxy statement for merger or acquisition | corporate_transaction | High potential transaction relevance | 5 | 1,810 | 9.485B | 1,810 | 2.647B | yes | complete_document_chunks |
| form | exact | DEFR14A | Revised definitive proxy statement | offering | Offering and capital-structure relevance | 4 | 1,477 | 812.332M | 1,477 | 183.526M | yes | complete_document_chunks |
| form | exact | F-1 | Registration statement for securities of certain foreign private issuers | offering | Offering and capital-structure relevance | 4 | 6,778 | 24.507B | 6,776 | 5.094B | yes | complete_document_chunks |
| form | exact | F-10 | Registration statement for securities of certain Canadian issuers | offering | Offering and capital-structure relevance | 4 | 808 | 377.514M | 807 | 144.398M | yes | complete_document_chunks |
| form | exact | F-3 | Registration statement for securities of certain foreign private issuers | offering | Offering and capital-structure relevance | 4 | 2,138 | 1.140B | 2,138 | 403.139M | yes | complete_document_chunks |
| form | exact | F-4 | Registration statement for securities of certain foreign private issuers issued in certain business combination transactions | corporate_transaction | High potential transaction relevance | 5 | 1,403 | 13.858B | 1,403 | 2.908B | yes | complete_document_chunks |
| form | exact | F-6 | Registration statement under the Securities Act of 1933 for depositary shares evidenced by American depositary receipts | offering | Offering and capital-structure relevance | 4 | 561 | 33.093M | 561 | 6.779M | yes | complete_document_chunks |
| form | exact | F-7 | Registration statement under the Securities Act of 1933 for securities of certain Canadian issuers offered for cash upon the exercise of rights granted to existing security holders | offering | Offering and capital-structure relevance | 4 | 17 | 5.529M | 17 | 2.527M | yes | complete_document_chunks |
| form | exact | F-8 | Registration statement under the Securities Act of 1933 for securities of certain Canadian issuers to be issued in exchange offers or a business combination | corporate_transaction | High potential transaction relevance | 5 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | F-80 | Registration statement for securities of certain Canadian issuers to be issued in exchange offers or a business combination | corporate_transaction | High potential transaction relevance | 5 | 1 | 1.385M | 1 | 517.373K | yes | complete_document_chunks |
| form | exact | F-N | Appointment of agent for service of process by foreign banks and foreign insurance companies | administrative | Administrative or regulatory | 1 | 170 | 3.629M | 170 | 919.130K | no | preserve_only |
| form | exact | F-X | Appointment of agent for service of process and undertaking | administrative | Administrative or regulatory | 1 | 1,246 | 28.927M | 1,246 | 6.324M | no | preserve_only |
| form | exact | FWP | Free writing prospectus | offering | Offering and capital-structure relevance | 4 | 164,796 | 22.970B | 163,805 | 5.720B | yes | complete_document_chunks |
| form | exact | ID | Uniform application for access codes to file on EDGAR | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | MA | Instructions for the Form MA Series | other_disclosure | Content-dependent disclosure | 2 | 1,856 | 229.345M | 0 | 0 | yes | complete_document_chunks |
| form | exact | MA-I | Information Regarding Natural Persons who Engage in Municipal Advisory Activities | other_disclosure | Content-dependent disclosure | 2 | 12,205 | 125.391M | 0 | 0 | yes | complete_document_chunks |
| form | exact | MA-NR | Designation of U.S. Agent for Service of Process for Non-Residents | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | MA-W | Notice of Withdrawal from Registration as a Municipal Advisor | administrative | Administrative or regulatory | 1 | 228 | 836.692K | 0 | 0 | no | preserve_only |
| form | exact | MSD | Application for registration as a municipal securities dealer or amendment to such application | administrative | Administrative or regulatory | 1 | 97 | 14.453K | 97 | 14.453K | no | preserve_only |
| form | exact | MSDW | Notice of withdrawal from registration as a municipal securities dealer | administrative | Administrative or regulatory | 1 | 7 | 1.043K | 7 | 1.043K | no | preserve_only |
| form | exact | N-14 | Form for the registration of securities issued in business combination transactions by investment companies and business development companies | corporate_transaction | High potential transaction relevance | 5 | 1,647 | 3.165B | 1,647 | 880.842M | yes | complete_document_chunks |
| form | exact | N-14 8C | Investment-company business-combination registration statement | corporate_transaction | High potential transaction relevance | 5 | 373 | 1.166B | 373 | 322.858M | yes | complete_document_chunks |
| form | exact | N-17D-1 | Report filed by small business investment company (SBIC) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | N-17F-1 | Certificate of accounting of securities and similar investments of a management investment company in the custody of members of national securities exchanges | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | N-17F-2 | Certificate of accounting of securities and similar investments in the custody of management investment companies | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | N-18F-1 | Notification of election pursuant to Rule 18f-1 under the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | N-1A | Registration form for open-end management investment companies | other_disclosure | Content-dependent disclosure | 2 | 656 | 988.889M | 656 | 394.774M | yes | complete_document_chunks |
| form | exact | N-2 | Registration statement for closed-end management investment companies | offering | Offering and capital-structure relevance | 4 | 2,975 | 5.595B | 2,975 | 1.902B | yes | complete_document_chunks |
| form | exact | N-23C-3 | Notification of repurchase offer | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | N-27D-1 | Accounting of Segregated Trust Account | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | N-3 | Registration statement of separate accounts organized as management investment companies | offering | Offering and capital-structure relevance | 4 | 4 | 27.024M | 4 | 3.092M | yes | complete_document_chunks |
| form | exact | N-30B-2 | Periodic and interim reports sent to investment-company shareholders | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 3,347 | 457.732M | 3,318 | 54.046M | no | separate_fund_pipeline |
| form | exact | N-4 | Registration statement of separate accounts organized as unit investment trusts (with amendments adopted in 2024 RILAs release) | offering | Offering and capital-structure relevance | 4 | 625 | 2.786B | 625 | 424.677M | yes | complete_document_chunks |
| form | exact | N-5 | Registration statement of small business investment company | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | N-54A | Notification of election to be subject to Sections 55-65 of the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 156 | 1.822M | 156 | 458.200K | no | preserve_only |
| form | exact | N-54C | Notification of withdrawal of election to be subject to Sections 55-65 of the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 59 | 958.498K | 59 | 237.136K | no | preserve_only |
| form | exact | N-6 | Registration statement for separate accounts organized as unit investment trusts that offer variable life insurance policies | offering | Offering and capital-structure relevance | 4 | 364 | 1.544B | 364 | 235.358M | yes | complete_document_chunks |
| form | exact | N-6EI-1 | Notification of claim of exemption pursuant to Rule 6e-2 or 6e-3(T) under the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | N-6F | Notice of intent to elect to be subject to Sections 55-65 of the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 62 | 618.574K | 62 | 130.107K | no | preserve_only |
| form | exact | N-8A | Notification of registration filed pursuant to Section 8(a) of Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 870 | 10.055M | 868 | 2.778M | no | preserve_only |
| form | exact | N-8B-2 | Registration statement of unit investment trusts which are currently issuing securities | offering | Offering and capital-structure relevance | 4 | 16 | 1.133M | 16 | 407.487K | yes | complete_document_chunks |
| form | exact | N-8B-4 | Registration statement of face-amount certificate companies | offering | Offering and capital-structure relevance | 4 | 7 | 446.915K | 7 | 190.661K | yes | complete_document_chunks |
| form | exact | N-8F | Application for deregistration of certain registered investment companies | administrative | Administrative or regulatory | 1 | 1,231 | 85.465M | 1,228 | 14.360M | no | preserve_only |
| form | exact | N-CEN | Annual Report for Registered Investment Companies | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 27,919 | 6.869B | 0 | 0 | no | separate_fund_pipeline |
| form | exact | N-CR | Current Report, Money Market Fund Material Events | current_event | High potential; event-dependent | 5 | 32 | 860.165K | 30 | 104.036K | yes | complete_document_chunks |
| form | exact | N-CSR | Certified shareholder report of registered management investment companies | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 25,263 | 136.103B | 25,259 | 13.718B | no | separate_fund_pipeline |
| form | exact | N-CSRS | Certified semiannual shareholder report | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 24,223 | 116.292B | 24,218 | 10.764B | no | separate_fund_pipeline |
| form | exact | N-MFP | Monthly Schedule of Portfolio Holdings of Money Market Funds | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 0 | 0 | 0 | 0 | no | structured_extraction_only |
| form | exact | N-MFP2 | Monthly money market fund portfolio report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 25,276 | 10.163B | 0 | 0 | no | structured_extraction_only |
| form | exact | N-MFP3 | Monthly money market fund portfolio report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 8,468 | 7.245B | 0 | 0 | no | structured_extraction_only |
| form | exact | N-PORT | Monthly Portfolio Investments Report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 0 | 0 | 0 | 0 | no | structured_extraction_only |
| form | exact | N-PX | Annual Report of Proxy Voting Record of Registered Management Investment Company | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 35,807 | 31.610B | 13,421 | 11.683B | no | structured_extraction_only |
| form | exact | N-Q | Quarterly schedule of portfolio holdings | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 3,305 | 3.967B | 3,303 | 426.793M | no | structured_extraction_only |
| form | exact | N-RN | Current Report For Registered Management Investment Companies and Business Development Companies | current_event | High potential; event-dependent | 5 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | N-VP | Variable insurance product filing | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 3,153 | 531.934M | 3,153 | 62.753M | no | separate_fund_pipeline |
| form | exact | N-VPFS | Variable insurance product summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 3,998 | 18.436B | 3,993 | 1.780B | no | separate_fund_pipeline |
| form | exact | N/A | Supplemental Information for Persons Requested to Supply Information Voluntarily to the Office of Credit Ratings’Monitoring Staff | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | NPORT-P | Monthly portfolio holdings report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 341,053 | 171.459B | 0 | 0 | no | structured_extraction_only |
| form | exact | NRSRO | Application for Registration as a Nationally Recognized Statistical Rating Organization (NRSRO) | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | PF | Reporting Form for Investment Advisers to Private Funds and Certain Commodity Pool Operators and Commodity Trading Advisors | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | PILOT | Initial operation report, amendment to initial operation report and quarterly report for pilot trading systems operated by self-regulatory organizations | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | POS 8C | Post-effective amendment under Investment Company Act Rule 8c | offering | Offering and capital-structure relevance | 4 | 257 | 1.075B | 257 | 196.552M | yes | complete_document_chunks |
| form | exact | POS AM | Post-effective amendment to a registration statement | offering | Offering and capital-structure relevance | 4 | 7,649 | 9.489B | 7,649 | 2.140B | yes | complete_document_chunks |
| form | exact | POS AMI | Post-effective investment-company amendment | offering | Offering and capital-structure relevance | 4 | 1,388 | 1.581B | 1,388 | 498.588M | yes | complete_document_chunks |
| form | exact | POS EX | Post-effective investment-company amendment | offering | Offering and capital-structure relevance | 4 | 3,101 | 772.352M | 3,101 | 154.867M | yes | complete_document_chunks |
| form | exact | POSASR | Automatic shelf registration post-effective amendment | offering | Offering and capital-structure relevance | 4 | 1,029 | 193.462M | 1,029 | 68.113M | yes | complete_document_chunks |
| form | exact | PRE 14A | Preliminary proxy statement | governance | Governance relevance; usually indirect | 3 | 10,198 | 9.199B | 10,194 | 2.354B | yes | complete_document_chunks |
| form | exact | PRE 14C | Preliminary information statement | governance | Governance relevance; usually indirect | 3 | 1,740 | 424.331M | 1,740 | 131.561M | yes | complete_document_chunks |
| form | exact | PREC14A | Preliminary proxy statement for contested solicitation | governance | Governance relevance; usually indirect | 3 | 786 | 536.653M | 786 | 130.032M | yes | complete_document_chunks |
| form | exact | PREM14A | Preliminary proxy statement for merger or acquisition | corporate_transaction | High potential transaction relevance | 5 | 1,079 | 4.025B | 1,079 | 1.204B | yes | complete_document_chunks |
| form | exact | PRER14A | Revised preliminary proxy statement | governance | Governance relevance; usually indirect | 3 | 1,648 | 6.813B | 1,648 | 1.553B | yes | complete_document_chunks |
| form | exact | R31 | Form for Reporting Covered Sales and Covered Round Turn Transactions Under Section 31 of the Securities Exchange Act of 1934 | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | S-1 | Registration statement under Securities Act of 1933 | offering | Offering and capital-structure relevance | 4 | 22,604 | 55.864B | 22,604 | 14.325B | yes | complete_document_chunks |
| form | exact | S-11 | Registration of securities of certain real estate companies | offering | Offering and capital-structure relevance | 4 | 402 | 1.223B | 402 | 371.438M | yes | complete_document_chunks |
| form | exact | S-20 | Registration statement under the Securities Act of 1933 | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | S-3 | Registration statement under Securities Act of 1933 | offering | Offering and capital-structure relevance | 4 | 8,749 | 3.882B | 8,749 | 1.325B | yes | complete_document_chunks |
| form | exact | S-3ASR | Automatic shelf registration statement | offering | Offering and capital-structure relevance | 4 | 4,196 | 1.535B | 4,196 | 588.948M | yes | complete_document_chunks |
| form | exact | S-4 | Registration statement under Securities Act of 1933 | corporate_transaction | High potential transaction relevance | 5 | 5,561 | 40.699B | 5,561 | 10.082B | yes | complete_document_chunks |
| form | exact | S-6 | Registration under 1933 act of securities of unit investment trusts registered on form N-8B-2 | other_disclosure | Content-dependent disclosure | 2 | 9,182 | 1.661B | 9,181 | 785.246M | yes | complete_document_chunks |
| form | exact | S-8 | Registration statement under Securities Act of 1933 to be offered to employees pursuant to certain plans | offering | Offering and capital-structure relevance | 4 | 19,012 | 1.502B | 19,011 | 346.702M | yes | complete_document_chunks |
| form | exact | S-8 POS | Post-effective amendment to employee benefit plan registration | offering | Offering and capital-structure relevance | 4 | 13,787 | 561.378M | 13,786 | 145.026M | yes | complete_document_chunks |
| form | exact | SBSE | Application for Registration of Security-based Swap Dealers and Major Security-based Swap Participants | administrative | Administrative or regulatory | 1 | 56 | 4.562M | 0 | 0 | no | preserve_only |
| form | exact | SBSE-A | Application for Registration of Security-based Swap Dealers and Major Security-based Swap Participants that are Registered or Registering with the Commodity Futures Trading Commission as a Swap Dealer | administrative | Administrative or regulatory | 1 | 579 | 18.103M | 0 | 0 | no | preserve_only |
| form | exact | SBSE-BD | Application for Registration of Security-based Swap Dealers and Major Security-based Swap Participants that are Registered Broker-dealers | administrative | Administrative or regulatory | 1 | 19 | 49.111K | 0 | 0 | no | preserve_only |
| form | exact | SBSE-C | Certifications for Registration of Security-based Swap Dealers and Major Security-based Swap Participants | other_disclosure | Content-dependent disclosure | 2 | 56 | 68.939K | 0 | 0 | yes | complete_document_chunks |
| form | exact | SBSE-W | Request for Withdrawal from Registration as a Security-based Swap Dealer or Major Security-based Swap Participant | administrative | Administrative or regulatory | 1 | 4 | 9.804K | 0 | 0 | no | preserve_only |
| form | exact | SBSEF | Security-Based Swap Execution Facility Application for Registration (with Amendment to Application) (and SBSEF Submission Cover Sheet) | administrative | Administrative or regulatory | 1 | 24 | 23.541K | 0 | 0 | no | preserve_only |
| form | exact | SC 13D | Beneficial ownership report | ownership_activism | High-to-medium ownership and activism relevance | 4 | 32,385 | 4.212B | 32,384 | 666.791M | yes | complete_document_chunks |
| form | exact | SC 13E3 | Going-private transaction statement | corporate_transaction | High potential transaction relevance | 5 | 1,220 | 203.075M | 1,220 | 51.438M | yes | complete_document_chunks |
| form | exact | SC 13G | Short-form beneficial ownership report | ownership | Ownership relevance; usually delayed | 2 | 150,000 | 7.238B | 149,944 | 1.478B | yes | complete_document_chunks |
| form | exact | SC 14D9 | Target-company tender-offer recommendation | corporate_transaction | High potential transaction relevance | 5 | 1,696 | 221.187M | 1,696 | 85.939M | yes | complete_document_chunks |
| form | exact | SC TO-I | Issuer tender-offer statement | corporate_transaction | High potential transaction relevance | 5 | 8,281 | 413.542M | 8,281 | 120.180M | yes | complete_document_chunks |
| form | exact | SC TO-T | Third-party tender-offer statement | corporate_transaction | High potential transaction relevance | 5 | 1,897 | 91.487M | 1,897 | 21.987M | yes | complete_document_chunks |
| form | exact | SCI | Systems Compliance and Integrity | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | SD | Specialized Disclosure Report | other_disclosure | Content-dependent disclosure | 2 | 8,554 | 205.111M | 8,549 | 34.468M | yes | complete_document_chunks |
| form | exact | SDR | Application or Amendment to Application for Registration or Withdrawal from Registration As Security-Based Swap Data Repository Under the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | SE | Form for Submission of Paper Format Exhibits by EDGAR Electronic Filers | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | SF-1 | Registration Statement Under the Securities Act of 1933 | structured_finance | Structured-product disclosure | 2 | 108 | 150.552M | 108 | 60.183M | no | structured_extraction_only |
| form | exact | SF-3 | Registration Statement Under the Securities Act of 1933 | structured_finance | Structured-product disclosure | 2 | 286 | 969.337M | 286 | 316.194M | no | structured_extraction_only |
| form | exact | SIP | Application or amendment to application for registration as securities information processor | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | SUPPL | Voluntary prospectus or offering supplement | offering | Offering and capital-structure relevance | 4 | 870 | 665.964M | 870 | 257.672M | yes | complete_document_chunks |
| form | exact | T-1 | Statement of eligibility and qualification under the Trust Indenture Act of 1939 of corporations designated to act as trustees | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | T-2 | Statement of eligibility under the Trust Indenture Act of 1939 of an individual designated to act as trustee | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | T-3 | For applications for qualification of indentures under the Trust Indenture Act of 1939 | other_disclosure | Content-dependent disclosure | 2 | 124 | 27.430M | 124 | 5.877M | yes | complete_document_chunks |
| form | exact | T-4 | Application for exemption filed pursuant to Section 304(c) of the Trust Indenture Act of 1939 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | T-6 | Application under Section 310(a)(1) of the Trust Indenture Act of 1939 for determination of eligibility of a foreign personal to act as institutional trustee | other_disclosure | Content-dependent disclosure | 2 | 6 | 198.047K | 6 | 84.614K | yes | complete_document_chunks |
| form | exact | TA-1 | Uniform form for registration as a transfer agent and for amendment to registration | other_disclosure | Content-dependent disclosure | 2 | 1,549 | 169.351M | 0 | 0 | yes | complete_document_chunks |
| form | exact | TA-2 | Form for reporting activities of transfer agents | other_disclosure | Content-dependent disclosure | 2 | 2,342 | 11.592M | 0 | 0 | yes | complete_document_chunks |
| form | exact | TA-W | Notice of withdrawal from registration as transfer agent | administrative | Administrative or regulatory | 1 | 104 | 241.975K | 0 | 0 | no | preserve_only |
| form | exact | TCR | Tip, Complaint, or Referral | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | TH | Notification of Reliance on Temporary Hardship Exemption | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | WB-APP | Application for Award for Original Information Submitted Pursuant to Section 21F of the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | 0 | no | preserve_only |
| form | exact | X-17A-19 | Report of Change in Membership Status | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART I | FOCUS Report, Part I | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART II | FOCUS Report, Part II Instructions | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART IIA | FOCUS Report Part IIa | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART IIC | FOCUS Report, Part IIC Instructions | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART III | FOCUS Report Part III | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 SCHEDULE I | (Financial and Operational Combined Uniform Single) FOCUS Report: Information Required of All Brokers and Dealers Pursuant to Rule 17a-5 | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |
| form | exact | X-17F-1A | Missing/Lost/Stolen/Counterfeit Securities Report | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | 0 | yes | complete_document_chunks |

## Largest Observed Groups

| Type | Role | Rows | Filings | Characters | P50 / P90 / P99 | Maximum | Resolution | Embed |
| --- | --- | ---: | ---: | ---: | --- | ---: | --- | --- |
| EX-102 | material_exhibit | 66,528 | 66,526 | 3.980T | 628.541K / 197.331M / 307.137M | 490.042M | approved_document_prefix | no |
| NPORT-EX | other_text_document | 172,412 | 170,683 | 1.459T | 820.306K / 22.668M / 108.219M | 309.667M | exact_document_type | no |
| XML | other_text_document | 13,895,736 | 683,656 | 721.845B | 25.881K / 103.524K / 389.119K | 64.397M | exact_document_type | no |
| 10-Q | primary_document | 134,858 | 134,855 | 274.618B | 1.501M / 4.060M / 10.322M | 83.640M | exact_form_type_as_document_type | yes |
| 485BPOS | primary_document | 41,509 | 41,509 | 188.999B | 1.722M / 11.659M / 38.604M | 201.670M | exact_form_type_as_document_type | no |
| EX-99.1 | press_release_exhibit | 531,714 | 526,486 | 183.740B | 39.174K / 1.158M / 2.838M | 36.354M | approved_document_prefix | yes |
| NPORT-P | primary_document | 335,334 | 335,334 | 163.109B | 101.173K / 889.954K / 3.931M | 601.341M | exact_form_type_as_document_type | no |
| 10-K | primary_document | 53,597 | 53,594 | 155.669B | 2.296M / 6.009M / 13.811M | 71.103M | exact_form_type_as_document_type | yes |
| 424B2 | primary_document | 516,951 | 516,945 | 142.455B | 203.810K / 495.046K / 1.116M | 60.825M | exact_form_type_as_document_type | yes |
| N-CSR | primary_document | 24,081 | 24,081 | 129.321B | 1.726M / 11.796M / 64.568M | 181.937M | exact_form_type_as_document_type | no |
| N-CSRS | primary_document | 23,882 | 23,882 | 114.271B | 1.484M / 10.574M / 56.163M | 150.961M | exact_form_type_as_document_type | no |
| ABS-15G/A | primary_document | 4,457 | 4,457 | 96.905B | 23.104M / 25.198M / 25.354M | 26.145M | approved_form_amendment | no |
| 424B3 | primary_document | 54,363 | 54,357 | 58.400B | 145.673K / 2.999M / 13.021M | 84.185M | exact_form_type_as_document_type | yes |
| 497 | primary_document | 93,588 | 93,569 | 54.812B | 15.578K / 1.030M / 13.133M | 154.240M | exact_form_type_as_document_type | no |
| NPORT-EX | primary_document | 7,295 | 7,295 | 47.043B | 948.340K / 18.912M / 97.533M | 116.591M | exact_document_type | no |
| DEF 14A | primary_document | 40,761 | 40,759 | 43.777B | 839.205K / 2.128M / 4.250M | 37.714M | exact_form_type_as_document_type | yes |
| PROXY VOTING RECORD | other_text_document | 11,175 | 11,163 | 40.120B | 211.054K / 4.494M / 92.387M | 391.384M | exact_document_type | no |
| 20-F | primary_document | 7,158 | 7,158 | 38.264B | 3.775M / 10.308M / 29.278M | 68.526M | exact_form_type_as_document_type | yes |
| EX-99.2 | press_release_exhibit | 99,596 | 96,741 | 35.396B | 35.845K / 780.074K / 5.534M | 37.545M | approved_document_prefix | yes |
| S-1/A | primary_document | 14,436 | 14,436 | 35.142B | 2.045M / 4.658M / 10.802M | 27.828M | approved_form_amendment | yes |
| N-PX | primary_document | 35,201 | 35,201 | 30.870B | 2.644K / 875.801K / 19.689M | 162.378M | exact_form_type_as_document_type | no |
| S-4/A | primary_document | 3,617 | 3,617 | 29.663B | 7.189M / 16.634M / 23.802M | 45.264M | approved_form_amendment | yes |
| XML | prospectus | 710,111 | 108,743 | 27.053B | 15.674K / 88.145K / 315.917K | 6.398M | exact_document_type | no |
| EX-10.1 | material_exhibit | 104,296 | 104,240 | 26.074B | 87.160K / 701.570K / 2.044M | 31.509M | approved_document_prefix | yes |
| 6-K | primary_document | 191,987 | 191,987 | 24.896B | 14.228K / 60.824K / 3.112M | 59.117M | exact_form_type_as_document_type | yes |
| FWP | primary_document | 164,796 | 164,796 | 22.970B | 59.805K / 323.631K / 703.235K | 34.957M | exact_form_type_as_document_type | yes |
| S-1 | primary_document | 8,168 | 8,168 | 20.721B | 2.065M / 4.925M / 11.545M | 31.596M | exact_form_type_as_document_type | yes |
| 8-K | primary_document | 515,054 | 515,049 | 19.968B | 33.870K / 56.608K / 110.322K | 21.009M | exact_form_type_as_document_type | yes |
| 485APOS | primary_document | 8,463 | 8,463 | 18.508B | 1.316M / 4.154M / 16.093M | 113.538M | exact_form_type_as_document_type | no |
| N-VPFS | primary_document | 3,830 | 3,830 | 17.596B | 3.133M / 10.073M / 26.430M | 79.213M | exact_form_type_as_document_type | no |
| F-1/A | primary_document | 4,831 | 4,831 | 17.491B | 2.909M / 7.562M / 14.729M | 32.857M | approved_form_amendment | yes |
| 424B5 | primary_document | 22,238 | 22,236 | 15.370B | 517.034K / 1.070M / 4.527M | 28.624M | exact_form_type_as_document_type | yes |
| EX-99.3 | press_release_exhibit | 37,347 | 35,199 | 15.015B | 28.674K / 749.790K / 7.237M | 26.006M | approved_document_prefix | yes |
| 497K | primary_document | 159,355 | 159,355 | 14.750B | 79.198K / 183.541K / 335.209K | 1.404M | exact_form_type_as_document_type | no |
| 424B4 | primary_document | 4,701 | 4,701 | 11.964B | 2.189M / 4.569M / 10.400M | 27.588M | exact_form_type_as_document_type | yes |
| S-4 | primary_document | 1,944 | 1,943 | 11.036B | 4.535M / 12.506M / 21.299M | 38.405M | exact_form_type_as_document_type | yes |
| EX-4.1 | other_text_exhibit | 28,121 | 28,118 | 10.788B | 106.338K / 789.073K / 6.665M | 14.316M | approved_document_prefix | yes |
| F-4/A | primary_document | 1,037 | 1,037 | 10.771B | 8.196M / 20.962M / 31.383M | 67.129M | approved_form_amendment | yes |
| N-MFP2 | primary_document | 24,308 | 24,308 | 9.656B | 184.201K / 863.916K / 3.622M | 11.436M | exact_form_type_as_document_type | no |
| POS AM | primary_document | 7,649 | 7,649 | 9.489B | 216.157K / 3.858M / 10.530M | 42.890M | exact_form_type_as_document_type | yes |
| DEFM14A | primary_document | 1,810 | 1,810 | 9.485B | 3.112M / 11.862M / 25.777M | 79.601M | exact_form_type_as_document_type | yes |
| 4 | primary_document | 1,374,408 | 1,374,407 | 9.420B | 4.700K / 12.585K / 33.532K | 141.509K | exact_form_type_as_document_type | yes |
| PRE 14A | primary_document | 10,198 | 10,197 | 9.199B | 611.588K / 1.926M / 4.113M | 23.885M | exact_form_type_as_document_type | yes |
| PART II AND III | other_text_document | 7,412 | 7,360 | 8.627B | 898.446K / 2.142M / 5.199M | 32.815M | exact_document_type | yes |
| EX-99.4 | press_release_exhibit | 21,566 | 20,070 | 7.875B | 15.687K / 513.749K / 7.763M | 25.946M | approved_document_prefix | yes |
| EX-99 | press_release_exhibit | 50,326 | 42,158 | 7.798B | 7.035K / 377.235K / 1.961M | 24.392M | approved_document_prefix | yes |
| EX-10.2 | material_exhibit | 49,097 | 49,081 | 7.717B | 77.848K / 288.856K / 1.554M | 17.955M | approved_document_prefix | yes |
| EX-2.1 | other_text_exhibit | 14,220 | 14,216 | 7.523B | 537.714K / 1.003M / 1.993M | 11.454M | approved_document_prefix | yes |
| 424H | primary_document | 964 | 964 | 7.090B | 4.951M / 16.207M / 23.900M | 32.655M | exact_form_type_as_document_type | yes |
| F-1 | primary_document | 1,947 | 1,947 | 7.015B | 2.965M / 7.338M / 14.873M | 31.278M | exact_form_type_as_document_type | yes |
| PRER14A | primary_document | 1,648 | 1,648 | 6.813B | 1.442M / 11.853M / 26.994M | 79.846M | exact_form_type_as_document_type | yes |
| N-CSR/A | primary_document | 1,182 | 1,182 | 6.782B | 1.234M / 13.760M / 90.845M | 135.403M | approved_form_amendment | no |
| N-MFP3 | primary_document | 7,833 | 7,833 | 6.347B | 313.960K / 1.524M / 9.811M | 18.689M | exact_form_type_as_document_type | no |
| 10-K/A | primary_document | 6,786 | 6,786 | 6.317B | 503.253K / 2.097M / 6.938M | 39.583M | approved_form_amendment | yes |
| N-CEN | primary_document | 25,440 | 25,440 | 6.060B | 45.539K / 449.468K / 3.840M | 42.263M | exact_form_type_as_document_type | no |
| EX-99.5 | press_release_exhibit | 13,818 | 12,768 | 5.631B | 16.950K / 645.086K / 7.680M | 25.536M | approved_document_prefix | yes |
| EX-99.H OTH MAT CONT | press_release_exhibit | 10,433 | 3,271 | 5.442B | 127.275K / 1.840M / 5.628M | 5.966M | approved_document_prefix | yes |
| SC 13G/A | primary_document | 106,259 | 106,259 | 4.731B | 16.627K / 107.094K / 265.004K | 7.618M | approved_form_amendment | yes |
| EX-99.G CUST AGREEMT | press_release_exhibit | 3,202 | 1,977 | 4.717B | 420.306K / 4.411M / 6.767M | 7.321M | approved_document_prefix | yes |
| NPORT-P | other_text_document | 475 | 475 | 4.392B | 68.660K / 477.798K / 593.821M | 593.821M | exact_form_type_as_document_type | no |
| EX-102.1 | material_exhibit | 21 | 21 | 4.185B | 208.216M / 322.899M / 339.747M | 339.747M | approved_document_prefix | no |
| EX-10.3 | material_exhibit | 29,961 | 29,946 | 4.156B | 77.561K / 243.222K / 1.404M | 7.530M | approved_document_prefix | yes |
| EX-1.1 | other_text_exhibit | 17,114 | 17,113 | 4.049B | 218.553K / 345.798K / 1.077M | 8.891M | approved_document_prefix | yes |
| PREM14A | primary_document | 1,079 | 1,079 | 4.025B | 2.320M / 7.633M / 21.211M | 55.266M | exact_form_type_as_document_type | yes |
| NPORT-P/A | primary_document | 5,243 | 5,243 | 3.958B | 130.688K / 1.215M / 3.776M | 355.882M | approved_form_amendment | no |
| N-Q | primary_document | 3,284 | 3,284 | 3.917B | 271.412K / 2.040M / 15.772M | 147.498M | exact_form_type_as_document_type | no |
| ABS-15G | primary_document | 16,532 | 16,532 | 3.773B | 12.948K / 21.699K / 474.133K | 25.314M | exact_form_type_as_document_type | no |
| EX-99.6 | press_release_exhibit | 9,651 | 8,766 | 3.676B | 14.856K / 543.891K / 7.647M | 24.915M | approved_document_prefix | yes |
| EX-4.2 | other_text_exhibit | 14,281 | 14,278 | 3.492B | 94.319K / 377.307K / 4.803M | 10.730M | approved_document_prefix | yes |
| 10-Q/A | primary_document | 3,719 | 3,719 | 3.449B | 587.172K / 2.125M / 6.087M | 17.109M | approved_form_amendment | yes |
| EX-35.1 | other_text_exhibit | 5,763 | 5,763 | 3.431B | 74.389K / 1.910M / 2.423M | 3.096M | approved_document_prefix | no |
| EX-35.4 | other_text_exhibit | 2,399 | 2,399 | 3.347B | 1.505M / 3.048M / 3.290M | 3.290M | approved_document_prefix | no |
| SC 13D/A | primary_document | 25,110 | 25,110 | 3.296B | 91.035K / 248.351K / 642.663K | 25.525M | approved_form_amendment | yes |
| N-2/A | primary_document | 1,724 | 1,724 | 3.247B | 1.294M / 2.809M / 14.614M | 41.072M | approved_form_amendment | yes |
| F-4 | primary_document | 366 | 366 | 3.086B | 6.787M / 18.066M / 27.458M | 31.273M | exact_form_type_as_document_type | yes |
| EX-35.3 | other_text_exhibit | 3,358 | 3,358 | 3.079B | 573.518K / 2.225M / 2.423M | 3.096M | approved_document_prefix | no |
| EX-4.3 | other_text_exhibit | 7,845 | 7,839 | 3.064B | 79.540K / 404.310K / 7.891M | 34.236M | approved_document_prefix | yes |
| 11-K | primary_document | 7,398 | 7,398 | 2.965B | 239.102K / 530.951K / 3.259M | 86.748M | exact_form_type_as_document_type | yes |
| 497VPU | primary_document | 12,319 | 12,319 | 2.929B | 75.686K / 612.990K / 1.571M | 6.172M | exact_form_type_as_document_type | no |
| EX-10.4 | material_exhibit | 21,300 | 21,283 | 2.882B | 73.261K / 245.703K / 1.352M | 10.258M | approved_document_prefix | yes |
| S-3 | primary_document | 6,558 | 6,557 | 2.842B | 339.327K / 631.812K / 1.734M | 83.854M | exact_form_type_as_document_type | yes |
| 487 | primary_document | 6,437 | 6,437 | 2.736B | 213.598K / 793.740K / 4.082M | 8.707M | exact_form_type_as_document_type | no |
| 425 | primary_document | 33,982 | 33,982 | 2.730B | 31.174K / 101.648K / 1.105M | 12.938M | exact_form_type_as_document_type | yes |
| 486BPOS | primary_document | 1,181 | 1,181 | 2.688B | 1.455M / 4.071M / 15.713M | 47.978M | exact_form_type_as_document_type | no |
| EX-3.1 | other_text_exhibit | 25,312 | 25,299 | 2.580B | 35.494K / 271.734K / 762.567K | 14.850M | approved_document_prefix | yes |
| EX-99.7 | press_release_exhibit | 7,028 | 6,469 | 2.507B | 16.120K / 446.711K / 8.827M | 24.960M | approved_document_prefix | yes |
| SC 13G | primary_document | 43,741 | 43,741 | 2.506B | 45.144K / 123.936K / 276.838K | 1.608M | exact_form_type_as_document_type | yes |
| 10-D | primary_document | 67,549 | 67,549 | 2.433B | 36.270K / 47.899K / 71.343K | 1.483M | exact_form_type_as_document_type | no |
| DEFA14A | primary_document | 51,044 | 51,030 | 2.365B | 23.534K / 61.419K / 637.862K | 10.482M | exact_form_type_as_document_type | yes |
| N-2 | primary_document | 1,251 | 1,251 | 2.348B | 1.148M / 2.946M / 16.337M | 40.313M | exact_form_type_as_document_type | yes |
| EX-31.1 | other_text_exhibit | 181,907 | 181,706 | 2.247B | 11.264K / 17.896K / 24.116K | 2.464M | approved_document_prefix | no |
| EX-33.1 | other_text_exhibit | 5,810 | 5,808 | 2.228B | 141.230K / 727.904K / 2.840M | 8.302M | approved_document_prefix | no |
| EX-33.5 | other_text_exhibit | 3,089 | 3,089 | 2.221B | 705.410K / 1.201M / 3.188M | 3.238M | approved_document_prefix | no |
| N-14 | primary_document | 1,073 | 1,073 | 2.110B | 1.207M / 3.451M / 12.064M | 57.489M | exact_form_type_as_document_type | yes |
| EX-FILING FEES | prospectus | 213,052 | 213,052 | 2.099B | 5.481K / 24.772K / 63.336K | 402.779K | exact_document_type | no |
| EX-31.2 | other_text_exhibit | 167,009 | 166,967 | 2.070B | 11.335K / 17.901K / 23.866K | 218.379K | approved_document_prefix | no |
| EX-10.5 | material_exhibit | 16,081 | 16,073 | 2.045B | 65.897K / 240.196K / 1.277M | 23.052M | approved_document_prefix | yes |
| N-CSRS/A | primary_document | 341 | 341 | 2.020B | 1.349M / 10.284M / 79.408M | 97.977M | approved_form_amendment | no |
| EX-35.2 | other_text_exhibit | 3,265 | 3,265 | 1.984B | 287.401K / 2.639M / 3.290M | 3.290M | approved_document_prefix | no |
| EX-4.4 | other_text_exhibit | 4,868 | 4,866 | 1.921B | 76.811K / 353.240K / 7.706M | 14.316M | approved_document_prefix | yes |

## Manual Review Queue

The complete queue is published in `q_live.sec_disclosure_taxonomy_candidate_v3`. The largest unresolved candidates are:

| Type | Role | Rows | Characters | Suggested score | Method |
| --- | --- | ---: | ---: | ---: | --- |
| ADD EXHB | other_text_document | 9,502 | 710.371M | 0.496 | fuzzy_title_candidate |
| EX1A-6 MAT CTRCT | other_text_document | 8,258 | 491.830M | 0.492 | fuzzy_title_candidate |
| N-2ASR | primary_document | 128 | 315.571M | 1.000 | fuzzy_title_candidate |
| 10-KT | primary_document | 137 | 312.673M | 0.922 | fuzzy_title_candidate |
| NT 10-Q | primary_document | 11,275 | 306.075M | 1.000 | fuzzy_title_candidate |
| DEFM14C | primary_document | 100 | 299.839M | 1.000 | fuzzy_title_candidate |
| 40-17F2 | primary_document | 4,020 | 272.176M | 0.625 | fuzzy_title_candidate |
| QRTLYRPT | primary_document | 111 | 256.143M | 0.481 | fuzzy_title_candidate |
| PREM14C | primary_document | 95 | 246.759M | 1.000 | fuzzy_title_candidate |
| N-23C3A | primary_document | 2,741 | 241.820M | 1.000 | fuzzy_title_candidate |
| ANNLRPT | primary_document | 56 | 232.775M | 0.935 | fuzzy_title_candidate |
| SCHEDULE 13G/A | primary_document | 27,658 | 223.219M | 0.000 | unmatched |
| EX1A-4 SUBS AGMT | other_text_document | 2,818 | 221.646M | 0.619 | fuzzy_title_candidate |
| 497J | primary_document | 36,201 | 220.795M | 0.625 | fuzzy_title_candidate |
| DFAN14A | primary_document | 4,711 | 207.301M | 0.719 | fuzzy_title_candidate |
| INTERNAL CONTROL RPT | other_text_document | 25,741 | 204.192M | 0.541 | fuzzy_title_candidate |
| PRER14C | primary_document | 195 | 199.366M | 1.000 | fuzzy_title_candidate |
| 8-A12B | primary_document | 9,524 | 192.500M | 0.963 | fuzzy_title_candidate |
| MATERIAL AMENDMENTS | other_text_document | 1,267 | 187.766M | 0.440 | fuzzy_title_candidate |
| F-3ASR | primary_document | 397 | 178.853M | 0.960 | fuzzy_title_candidate |
| NT 10-K | primary_document | 6,426 | 177.046M | 1.000 | fuzzy_title_candidate |
| EX1A-2B BYLAWS | other_text_document | 1,314 | 152.668M | 0.908 | fuzzy_title_candidate |
| EX1A-2A CHARTER | other_text_document | 2,968 | 142.770M | 0.554 | fuzzy_title_candidate |
| PRRN14A | primary_document | 332 | 135.041M | 1.000 | fuzzy_title_candidate |
| EX1A-3 HLDRS RTS | other_text_document | 2,033 | 124.476M | 0.619 | fuzzy_title_candidate |
| SCHEDULE 13G | primary_document | 14,962 | 121.777M | 0.000 | unmatched |
| S-B | primary_document | 94 | 115.602M | 0.960 | fuzzy_title_candidate |
| 13F-NT | primary_document | 51,282 | 111.752M | 0.000 | unmatched |
| SCHEDULE 13D/A | primary_document | 7,044 | 110.516M | 0.000 | unmatched |
| MA-A | primary_document | 3,513 | 100.059M | 0.448 | fuzzy_title_candidate |
| EX1A-1 UNDR AGMT | other_text_document | 918 | 84.254M | 0.606 | fuzzy_title_candidate |
| F-6EF | primary_document | 1,990 | 75.683M | 0.575 | fuzzy_title_candidate |
| ATS-N/UA | primary_document | 660 | 73.483M | 0.000 | unmatched |
| PX14A6G | primary_document | 2,167 | 73.349M | 0.785 | fuzzy_title_candidate |
| EX1A-15 ADD EXHB | other_text_document | 787 | 71.566M | 0.550 | fuzzy_title_candidate |
| N-30D | primary_document | 1,186 | 70.737M | 0.543 | fuzzy_title_candidate |
| DSTRBRPT | primary_document | 593 | 69.733M | 0.895 | fuzzy_title_candidate |
| S-B/A | primary_document | 62 | 61.983M | 0.706 | fuzzy_title_candidate |
| SC TO-C | primary_document | 1,045 | 60.883M | 0.625 | fuzzy_title_candidate |
| EX1U-6 MAT CTRCT | other_text_document | 514 | 59.362M | 0.644 | fuzzy_title_candidate |
| 305B2 | primary_document | 712 | 58.232M | 0.662 | fuzzy_title_candidate |
| EX1A-8 ESCW AGMT | other_text_document | 713 | 58.054M | 0.612 | fuzzy_title_candidate |
| EXEMPT ORDER INFO | other_text_document | 2,728 | 51.374M | 0.486 | fuzzy_title_candidate |
| NPORT-EX/A | primary_document | 16 | 50.557M | 0.961 | fuzzy_title_candidate |
| SCHEDULE 13D | primary_document | 1,788 | 49.626M | 0.000 | unmatched |
| S-1MEF | primary_document | 995 | 47.545M | 0.960 | fuzzy_title_candidate |
| ANNLRPT/A | primary_document | 8 | 47.535M | 0.559 | fuzzy_title_candidate |
| C-U | primary_document | 4,952 | 46.595M | 0.000 | unmatched |
| EX1A-12 OPN CNSL | other_text_document | 3,543 | 46.033M | 0.489 | fuzzy_title_candidate |
| EX1K-6 MAT CTRCT | other_text_document | 536 | 42.203M | 0.492 | fuzzy_title_candidate |
| F-6 POS | primary_document | 1,005 | 41.746M | 0.960 | fuzzy_title_candidate |
| POS462C | primary_document | 15 | 41.337M | 0.960 | fuzzy_title_candidate |
| 10-QT | primary_document | 32 | 41.008M | 0.922 | fuzzy_title_candidate |
| 497AD | primary_document | 1,592 | 40.133M | 0.637 | fuzzy_title_candidate |
| EX1A-13 TST WTRS | other_text_document | 996 | 39.686M | 0.603 | fuzzy_title_candidate |
| ADVISORY CONTRACTS | other_text_document | 662 | 38.518M | 0.409 | fuzzy_title_candidate |
| EX1SA-6 MAT CTRCT | other_text_document | 455 | 36.558M | 0.550 | fuzzy_title_candidate |
| X-17A-5 | primary_document | 25,791 | 34.068M | 0.454 | fuzzy_title_candidate |
| 15-12G | primary_document | 2,640 | 31.694M | 0.935 | fuzzy_title_candidate |
| NT 20-F | primary_document | 1,076 | 28.599M | 1.000 | fuzzy_title_candidate |
| 1-A POS | primary_document | 2,138 | 27.532M | 0.000 | unmatched |
| INST DEFINING RIGHTS | other_text_document | 366 | 26.281M | 0.634 | fuzzy_title_candidate |
| DFRN14A | primary_document | 300 | 25.958M | 1.000 | fuzzy_title_candidate |
| 497VPSUB | primary_document | 849 | 24.374M | 0.427 | fuzzy_title_candidate |
| 424A | primary_document | 16 | 24.108M | 0.043 | fuzzy_title_candidate |
| 40-17F1 | primary_document | 511 | 22.893M | 0.486 | fuzzy_title_candidate |
| SC 14F1 | primary_document | 182 | 22.522M | 0.582 | fuzzy_title_candidate |
| SP 15D2 | primary_document | 18 | 22.316M | 0.927 | fuzzy_title_candidate |
| EFFECT | primary_document | 29,133 | 21.951M | 0.000 | unmatched |
| 10-KT/A | primary_document | 25 | 19.726M | 0.477 | fuzzy_title_candidate |
| RW | primary_document | 2,411 | 18.821M | 0.748 | fuzzy_title_candidate |
| C-AR | primary_document | 5,043 | 18.542M | 0.000 | unmatched |
| 8-A12B/A | primary_document | 747 | 18.416M | 0.648 | fuzzy_title_candidate |
| OTHER REQUIRED INFO | other_text_document | 842 | 17.491M | 0.440 | fuzzy_title_candidate |
| 15-15D | primary_document | 1,168 | 17.369M | 0.935 | fuzzy_title_candidate |
| S-3D | primary_document | 61 | 16.817M | 0.960 | fuzzy_title_candidate |
| LEGAL PROCEEDINGS | other_text_document | 491 | 15.597M | 0.602 | fuzzy_title_candidate |
| 8-K12B | primary_document | 173 | 15.547M | 0.929 | fuzzy_title_candidate |
| EX1A-4 SUBS AGMT.1 | other_text_document | 199 | 15.526M | 0.619 | fuzzy_title_candidate |
| ATS-N/CA | primary_document | 129 | 15.322M | 0.000 | unmatched |
| EX1A-7 ACQ AGMT | other_text_document | 158 | 15.307M | 0.606 | fuzzy_title_candidate |
| ATS-N/MA | primary_document | 132 | 14.788M | 0.000 | unmatched |
| DEFR14C | primary_document | 72 | 14.585M | 1.000 | fuzzy_title_candidate |
| PREN14A | primary_document | 27 | 13.023M | 1.000 | fuzzy_title_candidate |
| 15-12B | primary_document | 948 | 12.516M | 0.726 | fuzzy_title_candidate |
| S-3DPOS | primary_document | 145 | 12.426M | 0.542 | fuzzy_title_candidate |
| SC14D9C | primary_document | 535 | 12.027M | 0.625 | fuzzy_title_candidate |
| S-4 POS | primary_document | 22 | 11.656M | 0.945 | fuzzy_title_candidate |
| EX1A-11 CONSENT | other_text_document | 3,896 | 11.555M | 0.603 | fuzzy_title_candidate |
| 40-6B/A | primary_document | 61 | 11.439M | 0.450 | fuzzy_title_candidate |
| 40FR12B | primary_document | 95 | 11.117M | 0.960 | fuzzy_title_candidate |
| EX1U-15 ADD EXHB | other_text_document | 255 | 11.075M | 0.489 | fuzzy_title_candidate |
| 40FR12B/A | primary_document | 67 | 10.751M | 0.548 | fuzzy_title_candidate |
| IRANNOTICE | primary_document | 1,793 | 10.749M | 0.611 | fuzzy_title_candidate |
| 8-A12G | primary_document | 414 | 10.409M | 0.805 | fuzzy_title_candidate |
| EX1U-1 UNDR AGMT | other_text_document | 71 | 9.442M | 0.550 | fuzzy_title_candidate |
| AW | primary_document | 1,117 | 9.284M | 0.778 | fuzzy_title_candidate |
| F-4 POS | primary_document | 1 | 9.212M | 0.000 | unmatched |
| DEFN14A | primary_document | 24 | 9.167M | 1.000 | fuzzy_title_candidate |
| F-10EF | primary_document | 26 | 9.011M | 0.854 | fuzzy_title_candidate |

## Database Products

- `q_live.sec_disclosure_taxonomy_v3`: manually approved semantic authority.
- `q_live.sec_disclosure_taxonomy_candidate_v3`: observed types, actual source statistics, fuzzy evidence, and unresolved review queue.
- `market_sip_compact.sec_embedding_policy_v3`: model-specific complete-text chunking policy.

## Sources

- SEC Forms Index: https://www.sec.gov/submit-filings/forms-index
- EDGAR Filer Manual conformance guidance: https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/understand-automated-conformance-rules-edgar-data-fields
