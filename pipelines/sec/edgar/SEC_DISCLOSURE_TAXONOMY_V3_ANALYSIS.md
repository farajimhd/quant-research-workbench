# SEC Disclosure Taxonomy and Embedding Workload

Generated UTC: `2026-07-16T13:59:35Z`

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

| Scope | Match | Type | Official or canonical title | Category | Impact | Score | Source rows | Source filings | Source chars | Source P50/P90/P99/P99.9 | Source max | Rendered rows | Rendered filings | Rendered chars | Rendered P50/P90/P99/P99.9 | Rendered max | Embed | Strategy |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | --- | ---: | --- | --- |
| document | prefix | EX- | Other submitted exhibit | exhibit | Exhibit-dependent | 2 | 116,008 | 94,246 | 4.555B | 14.007K / 47.045K / 364.587K / 3.925M | 69.711M | 115,627 | 93,998 | 1.224B | 5.630K / 16.296K / 86.566K / 586.337K | 1.761M | yes | complete_document_chunks |
| document | prefix | EX-1 | Underwriting or distribution agreement exhibit | exhibit | Exhibit-dependent | 2 | 111,565 | 68,881 | 14.072B | 17.145K / 275.896K / 1.103M / 6.926M | 58.844M | 110,512 | 68,266 | 4.807B | 4.260K / 141.373K / 268.613K / 909.016K | 3.485M | yes | complete_document_chunks |
| document | prefix | EX-10 | Material contract exhibit | material_contract | Potentially material; parent-context dependent | 4 | 369,422 | 150,011 | 63.345B | 72.046K / 331.799K / 1.705M / 3.627M | 84.751M | 363,913 | 148,408 | 25.282B | 29.729K / 138.190K / 705.103K / 1.111M | 11.016M | yes | complete_document_chunks |
| document | prefix | EX-101 | XBRL data exhibit | technical_representation | Technical representation; no independent catalyst | 0 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| document | prefix | EX-102 | Asset-level data file | structured_finance | Structured-product disclosure | 2 | 66,557 | 66,555 | 3.985T | 628.916K / 197.508M / 307.226M / 372.701M | 490.042M | 7 | 7 | 498.966K | 45.585K / 180.819K / 180.819K / 180.819K | 180.819K | no | structured_extraction_only |
| document | prefix | EX-103 | Asset-level data supporting file | structured_finance | Structured-product disclosure | 2 | 66,248 | 66,245 | 773.676M | 9.962K / 21.860K / 25.013K / 32.862K | 140.371K | 3 | 2 | 109.732K | 36.272K / 52.469K / 52.469K / 52.469K | 52.469K | no | structured_extraction_only |
| document | prefix | EX-104 | Cover page interactive data exhibit | technical_representation | Technical representation; no independent catalyst | 0 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| document | prefix | EX-106 | Asset-level data file | structured_finance | Structured-product disclosure | 2 | 1 | 1 | 45.454K | 45.454K / 45.454K / 45.454K / 45.454K | 45.454K | 1 | 1 | 17.360K | 17.360K / 17.360K / 17.360K / 17.360K | 17.360K | no | structured_extraction_only |
| document | prefix | EX-2 | Transaction agreement exhibit | transaction_exhibit | High potential; parent-transaction dependent | 5 | 396,351 | 304,107 | 13.865B | 3.575K / 26.878K / 775.244K / 1.794M | 40.543M | 387,530 | 296,755 | 5.191B | 1.771K / 5.318K / 358.168K / 732.028K | 3.984M | yes | complete_document_chunks |
| document | prefix | EX-3 | Charter or bylaws exhibit | exhibit | Exhibit-dependent | 2 | 61,407 | 40,199 | 6.876B | 57.388K / 276.704K / 767.950K / 2.190M | 14.850M | 57,609 | 38,606 | 2.881B | 34.644K / 114.002K / 218.074K / 820.323K | 2.166M | yes | complete_document_chunks |
| document | prefix | EX-31 | Officer certification | administrative | Administrative or compliance support | 1 | 386,959 | 199,085 | 4.790B | 11.264K / 17.988K / 25.003K / 42.082K | 2.464M | 386,915 | 199,058 | 1.351B | 3.479K / 3.684K / 6.574K / 9.245K | 137.114K | no | preserve_only |
| document | prefix | EX-32 | Section 906 certification | administrative | Administrative or compliance support | 1 | 309,531 | 189,036 | 2.046B | 5.919K / 10.069K / 16.713K / 26.088K | 1.188M | 309,490 | 189,020 | 371.886M | 1.096K / 1.612K / 2.203K / 3.363K | 237.467K | no | preserve_only |
| document | prefix | EX-33 | Asset-backed servicing compliance report | structured_finance | Structured-product disclosure | 2 | 43,702 | 6,298 | 21.425B | 318.820K / 1.071M / 3.188M / 3.238M | 8.302M | 43,658 | 6,296 | 2.288B | 26.579K / 154.293K / 187.267K / 197.290K | 268.578K | no | structured_extraction_only |
| document | prefix | EX-34 | Asset-backed servicing compliance assertion | structured_finance | Structured-product disclosure | 2 | 43,676 | 6,238 | 2.013B | 9.981K / 16.443K / 984.578K / 1.209M | 3.290M | 43,649 | 6,232 | 307.542M | 4.966K / 7.180K / 47.275K / 111.159K | 154.293K | no | structured_extraction_only |
| document | prefix | EX-35 | Asset-backed servicer compliance statement | structured_finance | Structured-product disclosure | 2 | 21,899 | 6,022 | 17.244B | 390.098K / 2.310M / 3.290M / 3.290M | 6.723M | 21,893 | 6,021 | 1.026B | 31.446K / 103.095K / 131.313K / 148.908K | 172.890K | no | structured_extraction_only |
| document | prefix | EX-36 | Asset-backed investor communication | structured_finance | Structured-product disclosure | 2 | 1,090 | 1,062 | 6.799M | 5.949K / 7.705K / 9.810K / 19.176K | 122.554K | 1,090 | 1,062 | 2.696M | 2.441K / 2.517K / 2.618K / 9.123K | 36.798K | no | structured_extraction_only |
| document | prefix | EX-4 | Security instrument or rights exhibit | exhibit | Exhibit-dependent | 2 | 117,227 | 65,681 | 32.947B | 93.578K / 511.688K / 5.521M / 10.108M | 34.236M | 115,577 | 64,929 | 13.484B | 44.517K / 225.618K / 2.316M / 3.533M | 4.149M | yes | complete_document_chunks |
| document | prefix | EX-99 | Additional exhibit or press release | additional_exhibit | Context-dependent; can be high | 4 | 1,268,947 | 708,072 | 328.462B | 22.091K / 593.401K / 3.369M / 13.938M | 65.198M | 1,248,473 | 695,047 | 45.462B | 8.339K / 76.320K / 415.474K / 1.988M | 20.935M | yes | complete_document_chunks |
| document | exact | EX-FILING FEES | Filing fee exhibit | administrative | Administrative or compliance support | 1 | 234,611 | 234,611 | 2.580B | 6.450K / 26.270K / 66.164K / 126.193K | 402.779K | 234,602 | 234,602 | 248.809M | 432 / 2.874K / 6.726K / 12.490K | 78.398K | no | preserve_only |
| document | exact | NPORT-EX | Form N-PORT Part F attachment | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 179,707 | 177,978 | 1.506T | 825.493K / 22.577M / 105.490M / 286.938M | 309.667M | 179,597 | 177,876 | 91.763B | 60.060K / 1.459M / 6.651M / 9.096M | 26.585M | no | structured_extraction_only |
| document | exact | PART II | Form narrative attachment: Part II | other_disclosure | Content-dependent disclosure | 2 | 2,828 | 2,826 | 1.768B | 353.795K / 806.212K / 5.951M / 25.332M | 35.029M | 2,826 | 2,825 | 352.754M | 85.990K / 197.571K / 644.506K / 1.983M | 2.646M | yes | complete_document_chunks |
| document | exact | PART II AND III | Form narrative attachment: Parts II and III | other_disclosure | Content-dependent disclosure | 2 | 7,412 | 7,360 | 8.627B | 898.446K / 2.142M / 5.199M / 19.922M | 32.815M | 7,405 | 7,356 | 2.476B | 274.952K / 625.621K / 1.022M / 2.288M | 3.529M | yes | complete_document_chunks |
| document | exact | PROXY VOTING RECORD | Proxy voting record attachment | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 11,175 | 11,163 | 40.120B | 211.054K / 4.494M / 92.387M / 147.103M | 391.384M | 2 | 2 | 8.550K | 4.439K / 4.439K / 4.439K / 4.439K | 4.439K | no | structured_extraction_only |
| document | exact | XML | Generic XML document | technical_representation | Technical representation; no independent catalyst | 0 | 14,605,847 | 792,399 | 748.899B | 25.288K / 102.913K / 384.979K / 1.677M | 64.397M | 14,605,181 | 792,399 | 172.615B | 6.327K / 26.159K / 78.297K / 197.855K | 9.904M | no | structured_extraction_only |
| form | exact | 1 | Application for registration or exemption from registration as a national securities exchange | administrative | Administrative or regulatory | 1 | 2,598 | 2,598 | 387.102K | 149 / 149 / 149 / 149 | 149 | 2,598 | 2,598 | 387.102K | 149 / 149 / 149 / 149 | 149 | no | preserve_only |
| form | exact | 1-A | Regulation A Offering Statement | offering | Offering and capital-structure relevance | 4 | 5,229 | 5,229 | 68.241M | 13.878K / 17.809K / 19.785K / 25.530K | 33.236K | 1 | 1 | 149 | 149 / 149 / 149 / 149 | 149 | yes | complete_document_chunks |
| form | exact | 1-E | Notification under Regulation E | administrative | Administrative or regulatory | 1 | 1 | 1 | 11.861K | 11.861K / 11.861K / 11.861K / 11.861K | 11.861K | 1 | 1 | 1.683K | 1.683K / 1.683K / 1.683K / 1.683K | 1.683K | no | preserve_only |
| form | exact | 1-K | Annual Reports and Special Financial Reports | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 2,820 | 2,820 | 4.808M | 1.431K / 2.675K / 3.692K / 11.599K | 12.820K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 1-N | Form and amendments for notice of registration as a national securities exchange for the sole purpose of trading security futures products | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | 1-SA | Semiannual Report or Special Financial Report Pursuant to Regulation A | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 2,558 | 2,558 | 1.119B | 236.345K / 532.994K / 4.671M / 19.887M | 31.746M | 2,556 | 2,556 | 183.541M | 50.188K / 112.057K / 385.262K / 1.565M | 1.632M | yes | complete_document_chunks |
| form | exact | 1-U | Current Report Pursuant to Regulation A | current_event | High potential; event-dependent | 5 | 10,875 | 10,875 | 343.660M | 24.505K / 31.391K / 200.194K / 1.484M | 4.410M | 10,875 | 10,875 | 72.554M | 5.287K / 8.992K / 26.214K / 94.507K | 203.899K | yes | complete_document_chunks |
| form | exact | 1-Z | Exit Report Under Regulation A | other_disclosure | Content-dependent disclosure | 2 | 573 | 573 | 1.176M | 1.618K / 2.770K / 3.415K / 4.487K | 4.487K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 10 | General form for registration of securities pursuant to Section 12(b) or (g) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 10-12B | Exchange Act securities registration statement | offering | Offering and capital-structure relevance | 4 | 416 | 416 | 123.700M | 52.226K / 457.321K / 4.228M / 9.182M | 9.182M | 416 | 416 | 27.517M | 10.990K / 136.138K / 847.144K / 1.365M | 1.365M | yes | complete_document_chunks |
| form | exact | 10-12G | Registration of a class of securities under the Exchange Act | other_disclosure | Content-dependent disclosure | 2 | 1,889 | 1,889 | 1.935B | 797.196K / 1.976M / 4.802M / 8.842M | 9.181M | 1,887 | 1,887 | 629.978M | 246.142K / 731.467K / 1.180M / 1.432M | 1.434M | yes | complete_document_chunks |
| form | exact | 10-D | Asset-Backed Issuer Distribution Report Pursuant to Section 13 or 15(d) of the Securities Exchange Act of 1934 | structured_finance | Structured-product disclosure | 2 | 68,531 | 68,531 | 2.469B | 36.311K / 47.905K / 71.530K / 157.784K | 1.483M | 68,531 | 68,531 | 483.138M | 6.741K / 9.514K / 17.690K / 25.393K | 130.064K | no | structured_extraction_only |
| form | exact | 10-K | Annual report pursuant to Section 13 or 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 60,383 | 60,380 | 161.986B | 2.062M / 5.748M / 13.346M / 25.007M | 71.103M | 60,379 | 60,376 | 23.021B | 383.522K / 681.768K / 1.128M / 1.869M | 3.639M | yes | complete_document_chunks |
| form | exact | 10-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Non-Resident General Partner of Broker or Dealer | administrative | Administrative or regulatory | 1 | 1 | 1 | 149 | 149 / 149 / 149 / 149 | 149 | 1 | 1 | 149 | 149 / 149 / 149 / 149 | 149 | no | preserve_only |
| form | exact | 10-Q | General form for quarterly reports under Section 13 or 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 138,577 | 138,574 | 278.067B | 1.475M / 4.020M / 10.247M / 21.229M | 83.640M | 138,573 | 138,570 | 27.497B | 165.756K / 363.039K / 655.640K / 1.187M | 3.218M | yes | complete_document_chunks |
| form | exact | 11-K | Annual reports of employee stock purchase, savings and similar plans pursuant to Section 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 7,506 | 7,506 | 2.997B | 238.897K / 531.194K / 3.174M / 22.270M | 86.748M | 7,504 | 7,504 | 353.897M | 36.906K / 54.810K / 280.297K / 2.131M | 5.760M | yes | complete_document_chunks |
| form | exact | 12B-25 | Notification of late filing | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | 13F | Information required of institutional investment managers pursuant to Section 13(f) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 13F-HR | Institutional investment manager holdings report | ownership | Ownership relevance; usually delayed | 2 | 215,425 | 215,425 | 480.771M | 2.052K / 2.566K / 5.253K / 14.260K | 39.139K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 13H | Information Required of Large Traders Pursuant To Section 13(h) of the Securities Exchange Act of 1934 | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 144 | Notice of proposed sale of securities pursuant to Rule 144 | insider_ownership | Insider ownership or sale signal | 3 | 119,540 | 119,540 | 852.871M | 4.185K / 9.792K / 44.726K / 240.161K | 4.910M | 820 | 820 | 6.085M | 7.198K / 8.814K / 11.441K / 15.877K | 15.877K | yes | complete_document_chunks |
| form | exact | 15 | Certification and notice of termination of registration under Section 12(g) or suspension of duty to file reports under Sections 13 and 15(d) | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | 15F | Certification of a foreign private issuer’s termination of registration of a class of securities under Section 12(g) or its termination of the duty to file reports under Section 13(a) or Section 15(d) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 17-H | Risk Assessment for Brokers & Dealers | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 18 | Application for registration pursuant to Section 12(b) & (c) of the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | 18-K | Annual report for foreign governments and political subdivisions thereof | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 1,344 | 1,344 | 54.195M | 18.881K / 48.844K / 572.615K / 2.287M | 2.603M | 1,344 | 1,344 | 12.743M | 3.311K / 13.607K / 180.015K / 600.777K | 606.304K | yes | complete_document_chunks |
| form | exact | 19B-4 | Proposed rule change by self-regulatory organization | other_disclosure | Content-dependent disclosure | 2 | 2 | 2 | 298 | 149 / 149 / 149 / 149 | 149 | 2 | 2 | 298 | 149 / 149 / 149 / 149 | 149 | yes | complete_document_chunks |
| form | exact | 19B-7 | Proposed rule change by self-regulatory organization | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 2-E | Report of sales pursuant to Rule 609 of Regulation E | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 20-F | Registration statement / Annual report / Transition report | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 8,077 | 8,077 | 39.928B | 3.459M / 9.900M / 28.201M / 50.465M | 68.526M | 8,077 | 8,077 | 6.373B | 733.606K / 1.310M / 2.421M / 3.923M | 5.993M | yes | complete_document_chunks |
| form | exact | 20FR12B | Foreign issuer Exchange Act securities registration statement | offering | Offering and capital-structure relevance | 4 | 168 | 168 | 499.319M | 2.594M / 5.334M / 11.817M / 13.066M | 13.066M | 168 | 168 | 112.581M | 640.797K / 1.107M / 1.614M / 1.866M | 1.866M | yes | complete_document_chunks |
| form | exact | 20FR12G | Foreign issuer Exchange Act securities registration statement | offering | Offering and capital-structure relevance | 4 | 62 | 62 | 151.467M | 1.937M / 4.129M / 16.326M / 16.326M | 16.326M | 62 | 62 | 31.458M | 442.644K / 986.187K / 1.326M / 1.326M | 1.326M | yes | complete_document_chunks |
| form | exact | 24F-2 | Annual notice of securities sold pursuant to Rule 24-f2 (with amendments adopted in 2024 RILAs release) | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | 24F-2NT | Investment-company annual notice of securities sold | administrative | Administrative or regulatory | 1 | 42,891 | 42,891 | 644.603M | 3.579K / 40.034K / 143.822K / 749.156K | 2.335M | 19,501 | 19,501 | 91.418M | 3.642K / 6.243K / 20.712K / 88.983K | 198.714K | no | preserve_only |
| form | exact | 25 | Notification of the removal from listing and registration of matured, redeemed or retired securities | administrative | Administrative or regulatory | 1 | 872 | 872 | 11.174M | 11.801K / 17.255K / 37.374K / 64.026K | 64.026K | 871 | 871 | 1.971M | 2.124K / 2.560K / 7.104K / 7.787K | 7.787K | no | preserve_only |
| form | exact | 253G1 | Regulation A offering circular | offering | Offering and capital-structure relevance | 4 | 207 | 207 | 245.114M | 933.349K / 2.106M / 5.545M / 6.236M | 6.236M | 207 | 207 | 70.002M | 276.625K / 595.378K / 1.329M / 1.622M | 1.622M | yes | complete_document_chunks |
| form | exact | 253G2 | Regulation A offering circular supplement | offering | Offering and capital-structure relevance | 4 | 4,258 | 4,258 | 1.860B | 26.337K / 1.288M / 3.229M / 12.857M | 32.646M | 4,258 | 4,258 | 561.069M | 7.723K / 430.200K / 913.469K / 2.413M | 3.142M | yes | complete_document_chunks |
| form | exact | 253G3 | Regulation A offering circular amendment | offering | Offering and capital-structure relevance | 4 | 62 | 62 | 42.678M | 284.763K / 1.856M / 3.446M / 3.446M | 3.446M | 62 | 62 | 11.803M | 100.337K / 529.577K / 777.068K / 777.068K | 777.068K | yes | complete_document_chunks |
| form | exact | 253G4 | Regulation A offering circular supplement | offering | Offering and capital-structure relevance | 4 | 5 | 5 | 1.567M | 34.140K / 777.200K / 777.200K / 777.200K | 777.200K | 5 | 5 | 446.401K | 7.429K / 223.616K / 223.616K / 223.616K | 223.616K | yes | complete_document_chunks |
| form | exact | 3 | Initial statement of beneficial ownership of securities | insider_ownership | Insider ownership or sale signal | 3 | 124,825 | 124,825 | 432.351M | 2.189K / 7.167K / 17.966K / 34.368K | 77.110K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 4 | Statement of changes in beneficial ownership of securities | insider_ownership | Insider ownership or sale signal | 3 | 1,399,467 | 1,399,466 | 9.584B | 4.697K / 12.567K / 33.504K / 63.490K | 141.509K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 40-17G | Investment-company fidelity bond filing | other_disclosure | Content-dependent disclosure | 2 | 4,366 | 4,347 | 1.584B | 366.616K / 589.784K / 1.009M / 2.045M | 3.338M | 4,344 | 4,325 | 372.339M | 86.727K / 133.848K / 217.156K / 479.937K | 562.101K | yes | complete_document_chunks |
| form | exact | 40-APP | Investment Company Act application for exemptive relief | administrative | Administrative or regulatory | 1 | 2,556 | 2,542 | 669.955M | 188.568K / 481.789K / 1.299M / 2.284M | 2.933M | 2,556 | 2,542 | 245.067M | 77.942K / 166.422K / 320.668K / 641.090K | 881.937K | no | preserve_only |
| form | exact | 40-F | Registration statement pursuant to Section 12 or annual report pursuant to Section 13(a) or 15(d) | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 1,293 | 1,293 | 454.484M | 105.893K / 617.100K / 3.758M / 7.539M | 7.721M | 1,293 | 1,293 | 78.088M | 31.662K / 104.144K / 654.386K / 1.049M | 1.111M | yes | complete_document_chunks |
| form | exact | 424B1 | Prospectus filed under Rule 424(b)(1) | offering | Offering and capital-structure relevance | 4 | 346 | 346 | 901.553M | 1.308M / 5.511M / 24.258M / 37.415M | 37.415M | 346 | 346 | 208.811M | 541.978K / 1.304M / 2.003M / 4.532M | 4.532M | yes | complete_document_chunks |
| form | exact | 424B2 | Prospectus filed under Rule 424(b)(2) | offering | Offering and capital-structure relevance | 4 | 516,951 | 516,945 | 142.455B | 203.810K / 495.046K / 1.116M / 4.352M | 60.825M | 516,950 | 516,944 | 43.264B | 71.733K / 126.617K / 268.282K / 755.444K | 4.229M | yes | complete_document_chunks |
| form | exact | 424B3 | Prospectus filed under Rule 424(b)(3) | offering | Offering and capital-structure relevance | 4 | 54,363 | 54,357 | 58.400B | 145.673K / 2.999M / 13.021M / 24.984M | 84.185M | 54,117 | 54,111 | 12.387B | 55.387K / 692.876K / 2.396M / 3.770M | 13.097M | yes | complete_document_chunks |
| form | exact | 424B4 | Prospectus filed under Rule 424(b)(4) | offering | Offering and capital-structure relevance | 4 | 4,701 | 4,701 | 11.964B | 2.189M / 4.569M / 10.400M / 21.596M | 27.588M | 4,701 | 4,701 | 3.353B | 754.899K / 1.089M / 1.537M / 2.742M | 3.192M | yes | complete_document_chunks |
| form | exact | 424B5 | Prospectus filed under Rule 424(b)(5) | offering | Offering and capital-structure relevance | 4 | 22,238 | 22,236 | 15.370B | 517.034K / 1.070M / 4.527M / 9.561M | 28.624M | 22,238 | 22,236 | 5.699B | 210.215K / 445.133K / 978.317K / 1.570M | 3.133M | yes | complete_document_chunks |
| form | exact | 424B7 | Prospectus filed under Rule 424(b)(7) | offering | Offering and capital-structure relevance | 4 | 1,509 | 1,509 | 731.289M | 397.528K / 754.448K / 3.321M / 6.161M | 6.397M | 1,509 | 1,509 | 263.939M | 158.485K / 281.290K / 813.594K / 1.247M | 1.273M | yes | complete_document_chunks |
| form | exact | 424B8 | Prospectus filed under Rule 424(b)(8) | offering | Offering and capital-structure relevance | 4 | 1,404 | 1,404 | 310.441M | 143.959K / 318.313K / 1.896M / 5.584M | 10.038M | 1,404 | 1,404 | 106.918M | 66.000K / 108.549K / 554.468K / 1.040M | 1.059M | yes | complete_document_chunks |
| form | exact | 424H | Preliminary prospectus filed under Rule 424(h) | offering | Offering and capital-structure relevance | 4 | 1,007 | 1,007 | 7.178B | 4.852M / 16.162M / 23.509M / 29.492M | 32.655M | 1,007 | 1,007 | 1.525B | 908.524K / 3.468M / 4.021M / 4.263M | 4.277M | yes | complete_document_chunks |
| form | exact | 425 | Business-combination communication | corporate_transaction | High potential transaction relevance | 5 | 33,982 | 33,982 | 2.730B | 31.174K / 101.648K / 1.105M / 4.368M | 12.938M | 33,970 | 33,970 | 1.224B | 14.707K / 50.784K / 550.350K / 1.521M | 3.491M | yes | complete_document_chunks |
| form | exact | 485APOS | Post-effective investment-company amendment under Rule 485(a) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 8,463 | 8,463 | 18.508B | 1.316M / 4.154M / 16.093M / 28.445M | 113.538M | 8,463 | 8,463 | 5.825B | 511.865K / 1.282M / 3.191M / 6.043M | 16.140M | no | separate_fund_pipeline |
| form | exact | 485BPOS | Post-effective investment-company amendment under Rule 485(b) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 41,509 | 41,509 | 188.999B | 1.722M / 11.659M / 38.604M / 115.169M | 201.670M | 41,506 | 41,506 | 36.510B | 524.266K / 1.921M / 5.703M / 11.610M | 16.242M | no | separate_fund_pipeline |
| form | exact | 485BXT | Post-effective investment-company amendment extension | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 15,414 | 15,414 | 644.999M | 27.809K / 62.979K / 281.971K / 773.607K | 875.412K | 15,414 | 15,414 | 111.517M | 4.413K / 8.138K / 46.290K / 118.635K | 369.149K | no | separate_fund_pipeline |
| form | exact | 486APOS | Post-effective business-development-company amendment under Rule 486(a) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 213 | 213 | 388.099M | 1.274M / 3.131M / 7.155M / 38.093M | 38.093M | 213 | 213 | 153.700M | 604.236K / 1.015M / 3.353M / 3.866M | 3.866M | no | separate_fund_pipeline |
| form | exact | 486BPOS | Post-effective business-development-company amendment under Rule 486(b) | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 1,181 | 1,181 | 2.688B | 1.455M / 4.071M / 15.713M / 40.326M | 47.978M | 1,181 | 1,181 | 868.732M | 593.741K / 1.284M / 2.618M / 3.786M | 3.885M | no | separate_fund_pipeline |
| form | exact | 487 | Investment-company pricing amendment | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 6,437 | 6,437 | 2.736B | 213.598K / 793.740K / 4.082M / 7.716M | 8.707M | 6,437 | 6,437 | 1.287B | 168.919K / 326.887K / 615.289K / 702.214K | 739.343K | no | separate_fund_pipeline |
| form | exact | 497 | Investment-company definitive materials | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 93,588 | 93,569 | 54.812B | 15.578K / 1.030M / 13.133M / 34.143M | 154.240M | 93,549 | 93,530 | 11.757B | 4.100K / 361.056K / 1.880M / 4.734M | 37.290M | no | separate_fund_pipeline |
| form | exact | 497K | Investment-company summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 159,355 | 159,355 | 14.750B | 79.198K / 183.541K / 335.209K / 588.303K | 1.404M | 159,348 | 159,348 | 3.934B | 23.186K / 45.944K / 77.689K / 127.259K | 279.361K | no | separate_fund_pipeline |
| form | exact | 497VPI | Variable insurance product initial summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 6,601 | 6,601 | 1.777B | 41.117K / 774.028K / 1.496M / 5.043M | 6.308M | 6,601 | 6,601 | 225.492M | 7.298K / 89.254K / 124.760K / 451.827K | 695.327K | no | separate_fund_pipeline |
| form | exact | 497VPU | Variable insurance product updated summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 12,319 | 12,319 | 2.929B | 75.686K / 612.990K / 1.571M / 3.261M | 6.172M | 12,319 | 12,319 | 299.398M | 11.313K / 54.920K / 124.716K / 261.235K | 695.327K | no | separate_fund_pipeline |
| form | exact | 5 | Annual statement of changes in beneficial ownership of securities | insider_ownership | Insider ownership or sale signal | 3 | 16,103 | 16,103 | 112.433M | 4.743K / 12.705K / 43.987K / 66.119K | 123.633K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 6-K | Report of foreign private issuer pursuant to Rule 13a-16 or 15d-16 under the Securities Exchange Act of 1934 | current_event | High potential; event-dependent | 5 | 194,217 | 194,217 | 25.581B | 14.269K / 62.544K / 3.170M / 9.538M | 59.117M | 194,113 | 194,113 | 2.921B | 2.251K / 12.169K / 293.560K / 958.002K | 9.774M | yes | complete_document_chunks |
| form | exact | 7-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Individual Non-Resident Broker or Dealer | administrative | Administrative or regulatory | 1 | 2 | 2 | 298 | 149 / 149 / 149 / 149 | 149 | 2 | 2 | 298 | 149 / 149 / 149 / 149 | 149 | no | preserve_only |
| form | exact | 8-A | Registration of certain classes of securities pursuant to Section 12(b) or (g) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | 8-K | Current report pursuant to Section 13 or 15(d) | current_event | High potential; event-dependent | 5 | 527,687 | 527,682 | 20.530B | 33.862K / 56.629K / 111.962K / 444.939K | 21.009M | 527,677 | 527,672 | 3.444B | 4.800K / 10.738K / 28.719K / 88.622K | 1.732M | yes | complete_document_chunks |
| form | exact | 8-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Corporate Non-Resident Broker or Dealer | administrative | Administrative or regulatory | 1 | 12 | 12 | 1.788K | 149 / 149 / 149 / 149 | 149 | 12 | 12 | 1.788K | 149 / 149 / 149 / 149 | 149 | no | preserve_only |
| form | exact | 9-M | Irrevocable Appointment of Agent for Service of Process, Pleadings and Other Papers by Partnership Non-Resident Broker or Dealer | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | ABS DD-15E | Certification of Provider of Third-Party Due Diligence Services for Asset-Backed Securities | structured_finance | Structured-product disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| form | exact | ABS-15G | Asset-Backed Securitizer Report | structured_finance | Structured-product disclosure | 2 | 20,989 | 20,989 | 100.677B | 14.201K / 23.116M / 25.245M / 25.470M | 26.145M | 20,989 | 20,989 | 51.747B | 2.846K / 12.979M / 14.213M / 14.452M | 15.008M | no | structured_extraction_only |
| form | exact | ABS-EE | Form for Submission of Electronic Exhibits for Asset-Backed Securities | structured_finance | Structured-product disclosure | 2 | 41,794 | 41,792 | 526.712M | 10.254K / 22.323K / 30.106K / 75.346K | 1.541M | 41,794 | 41,792 | 77.711M | 1.816K / 2.184K / 2.756K / 3.179K | 87.734K | no | structured_extraction_only |
| form | exact | ADV | Uniform Application for Investment Adviser Registration and Report by Exempt Reporting Advisers | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | ADV-E | Certificate of accounting of client securities and funds in the possession or custody of an investment adviser | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | ADV-H | Application for a temporary or continuing hardship exemption | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | ADV-NR | Appointment of agent for service of process by non-resident general partner and non-resident managing agent of an investment adviser | administrative | Administrative or regulatory | 1 | 98 | 98 | 14.602K | 149 / 149 / 149 / 149 | 149 | 98 | 98 | 14.602K | 149 / 149 / 149 / 149 | 149 | no | preserve_only |
| form | exact | ADV-W | Notice of withdrawal from registration as investment adviser | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | ARS | Annual report to security holders | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 603 | 603 | 516.780M | 525.525K / 1.944M / 6.813M / 13.430M | 13.430M | 549 | 549 | 220.587M | 404.801K / 689.286K / 1.010M / 1.201M | 1.201M | yes | complete_document_chunks |
| form | exact | ATS | Initial operation report, amendment to initial operation report and cessation of operations report for alternative trading systems | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | ATS-N | NMS Stock Alternative Trading Systems | other_disclosure | Content-dependent disclosure | 2 | 6 | 6 | 564.183K | 84.706K / 128.452K / 128.452K / 128.452K | 128.452K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | ATS-R | Quarterly report of alternative trading systems activities | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | BD | Uniform application for broker-dealer registration | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | BD-N | Notice of registration as a broker-dealer for the purpose of trading security futures products pursuant to Section 15(b)(11) of the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | BDW | Uniform request for broker-dealer withdrawal | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | C | Form C | offering | Offering and capital-structure relevance | 4 | 19,783 | 19,783 | 192.850M | 9.715K / 10.917K / 11.882K / 12.384K | 14.341K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | CA-1 | Registration or exemption from registration as a clearing agency and for amendment to registration | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | CB | Tender offer/rights offering notification form | corporate_transaction | High potential transaction relevance | 5 | 809 | 809 | 46.932M | 22.651K / 51.660K / 771.481K / 5.278M | 5.278M | 809 | 809 | 9.377M | 3.739K / 11.078K / 256.622K / 1.054M | 1.054M | yes | complete_document_chunks |
| form | exact | CFPORTAL | Application or Amendment to Application for Registration or Withdrawal from Registration as Funding Portal Under the Securities Exchange Act of 1934 | offering | Offering and capital-structure relevance | 4 | 574 | 574 | 5.150M | 8.590K / 11.710K / 17.995K / 19.119K | 19.119K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | CRS | Customer Relationship Summary | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | CUSTODY | Form Custody for Broker-Dealers | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | D | Notice of Exempt Offering of Securities | offering | Offering and capital-structure relevance | 4 | 414,181 | 414,181 | 3.325B | 6.939K / 11.378K / 24.419K / 69.187K | 252.039K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | DEF 14A | Definitive proxy statement | offering | Offering and capital-structure relevance | 4 | 40,761 | 40,759 | 43.777B | 839.205K / 2.128M / 4.250M / 8.173M | 37.714M | 40,744 | 40,742 | 9.182B | 204.135K / 381.798K / 600.195K / 1.569M | 3.959M | yes | complete_document_chunks |
| form | exact | DEF 14C | Definitive information statement | offering | Offering and capital-structure relevance | 4 | 2,889 | 2,889 | 790.934M | 129.786K / 509.053K / 2.290M / 12.453M | 24.940M | 2,889 | 2,889 | 243.360M | 45.639K / 168.188K / 650.452K / 1.947M | 5.116M | yes | complete_document_chunks |
| form | exact | DEFA14A | Additional definitive proxy soliciting material | offering | Offering and capital-structure relevance | 4 | 51,044 | 51,030 | 2.365B | 23.534K / 61.419K / 637.862K / 1.995M | 10.482M | 50,968 | 50,954 | 780.589M | 5.341K / 22.090K / 293.432K / 904.590K | 2.830M | yes | complete_document_chunks |
| form | exact | DEFC14A | Definitive proxy statement for contested solicitation | offering | Offering and capital-structure relevance | 4 | 657 | 657 | 495.464M | 344.826K / 1.934M / 4.949M / 8.645M | 8.645M | 657 | 657 | 115.324M | 127.066K / 395.133K / 631.252K / 775.496K | 775.496K | yes | complete_document_chunks |
| form | exact | DEFM14A | Definitive proxy statement for merger or acquisition | corporate_transaction | High potential transaction relevance | 5 | 1,810 | 1,810 | 9.485B | 3.112M / 11.862M / 25.777M / 48.805M | 79.601M | 1,810 | 1,810 | 2.647B | 1.178M / 2.676M / 4.024M / 5.760M | 5.834M | yes | complete_document_chunks |
| form | exact | DEFR14A | Revised definitive proxy statement | offering | Offering and capital-structure relevance | 4 | 1,477 | 1,475 | 812.332M | 114.024K / 1.449M / 6.288M / 14.170M | 15.694M | 1,477 | 1,475 | 183.526M | 28.448K / 308.496K / 1.388M / 2.518M | 2.700M | yes | complete_document_chunks |
| form | exact | F-1 | Registration statement for securities of certain foreign private issuers | offering | Offering and capital-structure relevance | 4 | 6,778 | 6,778 | 24.507B | 2.926M / 7.507M / 14.820M / 22.484M | 32.857M | 6,776 | 6,776 | 5.094B | 769.386K / 1.197M / 1.713M / 2.832M | 3.580M | yes | complete_document_chunks |
| form | exact | F-10 | Registration statement for securities of certain Canadian issuers | offering | Offering and capital-structure relevance | 4 | 808 | 808 | 377.514M | 365.607K / 834.520K / 1.818M / 3.315M | 3.315M | 807 | 807 | 144.398M | 151.529K / 293.878K / 563.363K / 767.450K | 767.450K | yes | complete_document_chunks |
| form | exact | F-3 | Registration statement for securities of certain foreign private issuers | offering | Offering and capital-structure relevance | 4 | 2,138 | 2,138 | 1.140B | 426.971K / 844.359K / 1.681M / 16.842M | 22.689M | 2,138 | 2,138 | 403.139M | 168.342K / 321.479K / 557.556K / 1.320M | 1.422M | yes | complete_document_chunks |
| form | exact | F-4 | Registration statement for securities of certain foreign private issuers issued in certain business combination transactions | corporate_transaction | High potential transaction relevance | 5 | 1,403 | 1,403 | 13.858B | 7.743M / 20.230M / 30.875M / 52.941M | 67.129M | 1,403 | 1,403 | 2.908B | 2.089M / 3.064M / 4.058M / 10.179M | 10.724M | yes | complete_document_chunks |
| form | exact | F-6 | Registration statement under the Securities Act of 1933 for depositary shares evidenced by American depositary receipts | offering | Offering and capital-structure relevance | 4 | 561 | 561 | 33.093M | 56.699K / 69.349K / 112.271K / 267.172K | 267.172K | 561 | 561 | 6.779M | 12.257K / 13.537K / 15.263K / 103.722K | 103.722K | yes | complete_document_chunks |
| form | exact | F-7 | Registration statement under the Securities Act of 1933 for securities of certain Canadian issuers offered for cash upon the exercise of rights granted to existing security holders | offering | Offering and capital-structure relevance | 4 | 17 | 17 | 5.529M | 174.020K / 697.333K / 1.684M / 1.684M | 1.684M | 17 | 17 | 2.527M | 72.887K / 298.186K / 866.351K / 866.351K | 866.351K | yes | complete_document_chunks |
| form | exact | F-8 | Registration statement under the Securities Act of 1933 for securities of certain Canadian issuers to be issued in exchange offers or a business combination | corporate_transaction | High potential transaction relevance | 5 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | F-80 | Registration statement for securities of certain Canadian issuers to be issued in exchange offers or a business combination | corporate_transaction | High potential transaction relevance | 5 | 1 | 1 | 1.385M | 1.385M / 1.385M / 1.385M / 1.385M | 1.385M | 1 | 1 | 517.373K | 517.373K / 517.373K / 517.373K / 517.373K | 517.373K | yes | complete_document_chunks |
| form | exact | F-N | Appointment of agent for service of process by foreign banks and foreign insurance companies | administrative | Administrative or regulatory | 1 | 170 | 170 | 3.629M | 19.722K / 27.021K / 43.051K / 60.526K | 60.526K | 170 | 170 | 919.130K | 4.881K / 7.197K / 8.629K / 9.777K | 9.777K | no | preserve_only |
| form | exact | F-X | Appointment of agent for service of process and undertaking | administrative | Administrative or regulatory | 1 | 1,246 | 1,246 | 28.927M | 19.905K / 34.878K / 62.639K / 64.063K | 79.591K | 1,246 | 1,246 | 6.324M | 4.794K / 6.403K / 7.456K / 10.867K | 11.545K | no | preserve_only |
| form | exact | FWP | Free writing prospectus | offering | Offering and capital-structure relevance | 4 | 164,796 | 164,796 | 22.970B | 59.805K / 323.631K / 703.235K / 5.194M | 34.957M | 163,805 | 163,805 | 5.720B | 10.834K / 91.481K / 146.695K / 666.676K | 3.579M | yes | complete_document_chunks |
| form | exact | ID | Uniform application for access codes to file on EDGAR | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | MA | Instructions for the Form MA Series | other_disclosure | Content-dependent disclosure | 2 | 1,856 | 1,856 | 229.345M | 26.329K / 336.993K / 1.111M / 1.133M | 1.134M | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | MA-I | Information Regarding Natural Persons who Engage in Municipal Advisory Activities | other_disclosure | Content-dependent disclosure | 2 | 12,205 | 12,205 | 125.391M | 9.772K / 13.258K / 17.941K / 21.595K | 23.964K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | MA-NR | Designation of U.S. Agent for Service of Process for Non-Residents | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | MA-W | Notice of Withdrawal from Registration as a Municipal Advisor | administrative | Administrative or regulatory | 1 | 228 | 228 | 836.692K | 3.470K / 4.273K / 6.637K / 8.068K | 8.068K | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | MSD | Application for registration as a municipal securities dealer or amendment to such application | administrative | Administrative or regulatory | 1 | 97 | 97 | 14.453K | 149 / 149 / 149 / 149 | 149 | 97 | 97 | 14.453K | 149 / 149 / 149 / 149 | 149 | no | preserve_only |
| form | exact | MSDW | Notice of withdrawal from registration as a municipal securities dealer | administrative | Administrative or regulatory | 1 | 7 | 7 | 1.043K | 149 / 149 / 149 / 149 | 149 | 7 | 7 | 1.043K | 149 / 149 / 149 / 149 | 149 | no | preserve_only |
| form | exact | N-14 | Form for the registration of securities issued in business combination transactions by investment companies and business development companies | corporate_transaction | High potential transaction relevance | 5 | 1,647 | 1,647 | 3.165B | 1.199M / 3.198M / 11.564M / 57.387M | 57.489M | 1,647 | 1,647 | 880.842M | 386.252K / 993.660K / 2.596M / 6.226M | 6.258M | yes | complete_document_chunks |
| form | exact | N-14 8C | Investment-company business-combination registration statement | corporate_transaction | High potential transaction relevance | 5 | 373 | 373 | 1.166B | 2.136M / 6.262M / 21.862M / 49.621M | 49.621M | 373 | 373 | 322.858M | 750.933K / 1.489M / 3.763M / 5.782M | 5.782M | yes | complete_document_chunks |
| form | exact | N-17D-1 | Report filed by small business investment company (SBIC) | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | N-17F-1 | Certificate of accounting of securities and similar investments of a management investment company in the custody of members of national securities exchanges | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | N-17F-2 | Certificate of accounting of securities and similar investments in the custody of management investment companies | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | N-18F-1 | Notification of election pursuant to Rule 18f-1 under the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | N-1A | Registration form for open-end management investment companies | other_disclosure | Content-dependent disclosure | 2 | 656 | 656 | 988.889M | 976.260K / 2.286M / 8.637M / 43.215M | 43.215M | 656 | 656 | 394.774M | 424.027K / 901.514K / 3.218M / 11.843M | 11.843M | yes | complete_document_chunks |
| form | exact | N-2 | Registration statement for closed-end management investment companies | offering | Offering and capital-structure relevance | 4 | 2,975 | 2,975 | 5.595B | 1.245M / 2.876M / 15.008M / 37.899M | 41.072M | 2,975 | 2,975 | 1.902B | 576.848K / 1.004M / 1.876M / 3.860M | 4.574M | yes | complete_document_chunks |
| form | exact | N-23C-3 | Notification of repurchase offer | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | N-27D-1 | Accounting of Segregated Trust Account | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | N-3 | Registration statement of separate accounts organized as management investment companies | offering | Offering and capital-structure relevance | 4 | 4 | 4 | 27.024M | 12.616M / 12.636M / 12.636M / 12.636M | 12.636M | 4 | 4 | 3.092M | 1.202M / 1.202M / 1.202M / 1.202M | 1.202M | yes | complete_document_chunks |
| form | exact | N-30B-2 | Periodic and interim reports sent to investment-company shareholders | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 3,347 | 3,347 | 457.732M | 11.809K / 157.641K / 3.234M / 8.302M | 11.625M | 3,318 | 3,318 | 54.046M | 1.956K / 23.402K / 275.892K / 1.002M | 1.117M | no | separate_fund_pipeline |
| form | exact | N-4 | Registration statement of separate accounts organized as unit investment trusts (with amendments adopted in 2024 RILAs release) | offering | Offering and capital-structure relevance | 4 | 625 | 625 | 2.786B | 1.686M / 12.934M / 45.788M / 54.279M | 54.279M | 625 | 625 | 424.677M | 501.329K / 1.589M / 3.939M / 4.610M | 4.610M | yes | complete_document_chunks |
| form | exact | N-5 | Registration statement of small business investment company | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | N-54A | Notification of election to be subject to Sections 55-65 of the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 156 | 156 | 1.822M | 11.214K / 14.485K / 23.507K / 24.371K | 24.371K | 156 | 156 | 458.200K | 2.917K / 3.091K / 3.696K / 6.063K | 6.063K | no | preserve_only |
| form | exact | N-54C | Notification of withdrawal of election to be subject to Sections 55-65 of the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 59 | 59 | 958.498K | 14.670K / 25.072K / 37.107K / 37.107K | 37.107K | 59 | 59 | 237.136K | 4.302K / 4.683K / 7.993K / 7.993K | 7.993K | no | preserve_only |
| form | exact | N-6 | Registration statement for separate accounts organized as unit investment trusts that offer variable life insurance policies | offering | Offering and capital-structure relevance | 4 | 364 | 364 | 1.544B | 1.784M / 12.410M / 30.062M / 32.200M | 32.200M | 364 | 364 | 235.358M | 518.989K / 1.249M / 1.722M / 2.271M | 2.271M | yes | complete_document_chunks |
| form | exact | N-6EI-1 | Notification of claim of exemption pursuant to Rule 6e-2 or 6e-3(T) under the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | N-6F | Notice of intent to elect to be subject to Sections 55-65 of the Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 62 | 62 | 618.574K | 9.413K / 11.856K / 22.771K / 22.771K | 22.771K | 62 | 62 | 130.107K | 2.028K / 2.454K / 2.667K / 2.667K | 2.667K | no | preserve_only |
| form | exact | N-8A | Notification of registration filed pursuant to Section 8(a) of Investment Company Act of 1940 | administrative | Administrative or regulatory | 1 | 870 | 870 | 10.055M | 9.269K / 16.404K / 66.929K / 229.126K | 229.126K | 868 | 868 | 2.778M | 1.751K / 3.693K / 60.373K / 95.114K | 95.114K | no | preserve_only |
| form | exact | N-8B-2 | Registration statement of unit investment trusts which are currently issuing securities | offering | Offering and capital-structure relevance | 4 | 16 | 16 | 1.133M | 8.546K / 273.005K / 275.479K / 275.479K | 275.479K | 16 | 16 | 407.487K | 1.380K / 97.722K / 99.260K / 99.260K | 99.260K | yes | complete_document_chunks |
| form | exact | N-8B-4 | Registration statement of face-amount certificate companies | offering | Offering and capital-structure relevance | 4 | 7 | 7 | 446.915K | 20.953K / 186.194K / 186.194K / 186.194K | 186.194K | 7 | 7 | 190.661K | 18.708K / 56.916K / 56.916K / 56.916K | 56.916K | yes | complete_document_chunks |
| form | exact | N-8F | Application for deregistration of certain registered investment companies | administrative | Administrative or regulatory | 1 | 1,231 | 1,231 | 85.465M | 59.841K / 103.616K / 182.966K / 247.516K | 249.339K | 1,228 | 1,228 | 14.360M | 10.648K / 14.317K / 20.226K / 91.607K | 91.743K | no | preserve_only |
| form | exact | N-CEN | Annual Report for Registered Investment Companies | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 27,919 | 27,919 | 6.869B | 46.316K / 460.732K / 3.969M / 16.879M | 42.274M | 0 | 0 | 0 | n/a | 0 | no | separate_fund_pipeline |
| form | exact | N-CR | Current Report, Money Market Fund Material Events | current_event | High potential; event-dependent | 5 | 32 | 32 | 860.165K | 24.331K / 37.115K / 94.961K / 94.961K | 94.961K | 30 | 30 | 104.036K | 3.234K / 6.121K / 7.436K / 7.436K | 7.436K | yes | complete_document_chunks |
| form | exact | N-CSR | Certified shareholder report of registered management investment companies | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 25,263 | 25,263 | 136.103B | 1.704M / 11.897M / 65.756M / 135.403M | 181.937M | 25,259 | 25,259 | 13.718B | 216.679K / 1.175M / 5.210M / 16.427M | 48.209M | no | separate_fund_pipeline |
| form | exact | N-CSRS | Certified semiannual shareholder report | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 24,223 | 24,223 | 116.292B | 1.482M / 10.571M / 56.663M / 124.897M | 150.961M | 24,218 | 24,218 | 10.764B | 159.059K / 961.813K / 4.317M / 15.320M | 46.851M | no | separate_fund_pipeline |
| form | exact | N-MFP | Monthly Schedule of Portfolio Holdings of Money Market Funds | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| form | exact | N-MFP2 | Monthly money market fund portfolio report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 25,276 | 25,276 | 10.163B | 185.919K / 877.895K / 3.665M / 6.658M | 11.436M | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| form | exact | N-MFP3 | Monthly money market fund portfolio report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 8,468 | 8,468 | 7.245B | 321.649K / 1.562M / 10.481M / 15.599M | 18.689M | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| form | exact | N-PORT | Monthly Portfolio Investments Report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| form | exact | N-PX | Annual Report of Proxy Voting Record of Registered Management Investment Company | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 35,807 | 35,807 | 31.610B | 2.666K / 893.468K / 19.808M / 91.615M | 162.378M | 13,421 | 13,421 | 11.683B | 55.819K / 1.565M / 15.742M / 47.527M | 99.049M | no | structured_extraction_only |
| form | exact | N-Q | Quarterly schedule of portfolio holdings | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 3,305 | 3,305 | 3.967B | 271.969K / 2.051M / 15.772M / 106.126M | 147.498M | 3,303 | 3,303 | 426.793M | 33.877K / 210.755K / 1.681M / 9.963M | 15.613M | no | structured_extraction_only |
| form | exact | N-RN | Current Report For Registered Management Investment Companies and Business Development Companies | current_event | High potential; event-dependent | 5 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | N-VP | Variable insurance product filing | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 3,153 | 3,153 | 531.934M | 114.007K / 458.916K / 655.309K / 814.576K | 1.015M | 3,153 | 3,153 | 62.753M | 19.218K / 41.541K / 66.205K / 108.609K | 139.226K | no | separate_fund_pipeline |
| form | exact | N-VPFS | Variable insurance product summary prospectus | fund_product_disclosure | Fund or product disclosure; low direct stock catalyst | 2 | 3,998 | 3,998 | 18.436B | 3.128M / 10.153M / 26.478M / 50.144M | 79.213M | 3,993 | 3,993 | 1.780B | 383.740K / 842.259K / 1.574M / 14.352M | 15.213M | no | separate_fund_pipeline |
| form | exact | N/A | Supplemental Information for Persons Requested to Supply Information Voluntarily to the Office of Credit Ratings’Monitoring Staff | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | NPORT-P | Monthly portfolio holdings report | fund_dataset | Fund or underlying-security disclosure; low direct stock catalyst | 2 | 341,053 | 341,053 | 171.459B | 101.414K / 893.325K / 3.932M / 14.553M | 601.341M | 0 | 0 | 0 | n/a | 0 | no | structured_extraction_only |
| form | exact | NRSRO | Application for Registration as a Nationally Recognized Statistical Rating Organization (NRSRO) | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | PF | Reporting Form for Investment Advisers to Private Funds and Certain Commodity Pool Operators and Commodity Trading Advisors | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | PILOT | Initial operation report, amendment to initial operation report and quarterly report for pilot trading systems operated by self-regulatory organizations | periodic_fundamentals | Medium-to-high fundamental relevance | 4 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | POS 8C | Post-effective amendment under Investment Company Act Rule 8c | offering | Offering and capital-structure relevance | 4 | 257 | 257 | 1.075B | 1.495M / 5.692M / 101.194M / 163.011M | 163.011M | 257 | 257 | 196.552M | 643.746K / 1.191M / 3.902M / 7.303M | 7.303M | yes | complete_document_chunks |
| form | exact | POS AM | Post-effective amendment to a registration statement | offering | Offering and capital-structure relevance | 4 | 7,649 | 7,649 | 9.489B | 216.157K / 3.858M / 10.530M / 17.296M | 42.890M | 7,649 | 7,649 | 2.140B | 72.161K / 884.657K / 1.540M / 3.085M | 3.983M | yes | complete_document_chunks |
| form | exact | POS AMI | Post-effective investment-company amendment | offering | Offering and capital-structure relevance | 4 | 1,388 | 1,388 | 1.581B | 601.812K / 2.543M / 15.793M / 22.219M | 22.281M | 1,388 | 1,388 | 498.588M | 282.256K / 759.089K / 1.835M / 4.780M | 5.029M | yes | complete_document_chunks |
| form | exact | POS EX | Post-effective investment-company amendment | offering | Offering and capital-structure relevance | 4 | 3,101 | 3,101 | 772.352M | 83.380K / 1.083M / 1.551M / 1.602M | 3.221M | 3,101 | 3,101 | 154.867M | 21.056K / 184.366K / 354.511K / 369.242K | 1.217M | yes | complete_document_chunks |
| form | exact | POSASR | Automatic shelf registration post-effective amendment | offering | Offering and capital-structure relevance | 4 | 1,029 | 1,028 | 193.462M | 36.041K / 530.111K / 1.096M / 1.925M | 3.831M | 1,029 | 1,028 | 68.113M | 8.616K / 188.316K / 389.361K / 791.288K | 813.330K | yes | complete_document_chunks |
| form | exact | PRE 14A | Preliminary proxy statement | governance | Governance relevance; usually indirect | 3 | 10,198 | 10,197 | 9.199B | 611.588K / 1.926M / 4.113M / 9.973M | 23.885M | 10,194 | 10,193 | 2.354B | 196.281K / 412.417K / 887.637K / 1.630M | 2.773M | yes | complete_document_chunks |
| form | exact | PRE 14C | Preliminary information statement | governance | Governance relevance; usually indirect | 3 | 1,740 | 1,740 | 424.331M | 119.154K / 444.810K / 1.970M / 12.455M | 14.497M | 1,740 | 1,740 | 131.561M | 39.506K / 158.556K / 654.032K / 1.366M | 1.393M | yes | complete_document_chunks |
| form | exact | PREC14A | Preliminary proxy statement for contested solicitation | governance | Governance relevance; usually indirect | 3 | 786 | 786 | 536.653M | 311.618K / 1.768M / 4.497M / 8.551M | 8.551M | 786 | 786 | 130.032M | 115.835K / 370.336K / 620.460K / 774.089K | 774.089K | yes | complete_document_chunks |
| form | exact | PREM14A | Preliminary proxy statement for merger or acquisition | corporate_transaction | High potential transaction relevance | 5 | 1,079 | 1,079 | 4.025B | 2.320M / 7.633M / 21.211M / 34.007M | 55.266M | 1,079 | 1,079 | 1.204B | 915.086K / 2.041M / 3.115M / 3.542M | 3.542M | yes | complete_document_chunks |
| form | exact | PRER14A | Revised preliminary proxy statement | governance | Governance relevance; usually indirect | 3 | 1,648 | 1,648 | 6.813B | 1.442M / 11.853M / 26.994M / 79.614M | 79.846M | 1,648 | 1,648 | 1.553B | 383.710K / 2.441M / 3.442M / 4.171M | 4.173M | yes | complete_document_chunks |
| form | exact | R31 | Form for Reporting Covered Sales and Covered Round Turn Transactions Under Section 31 of the Securities Exchange Act of 1934 | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | S-1 | Registration statement under Securities Act of 1933 | offering | Offering and capital-structure relevance | 4 | 22,604 | 22,604 | 55.864B | 2.054M / 4.741M / 11.040M / 24.081M | 31.596M | 22,604 | 22,604 | 14.325B | 681.105K / 1.071M / 1.556M / 2.314M | 3.108M | yes | complete_document_chunks |
| form | exact | S-11 | Registration of securities of certain real estate companies | offering | Offering and capital-structure relevance | 4 | 402 | 402 | 1.223B | 2.537M / 5.827M / 10.101M / 15.168M | 15.168M | 402 | 402 | 371.438M | 965.763K / 1.386M / 2.087M / 2.933M | 2.933M | yes | complete_document_chunks |
| form | exact | S-20 | Registration statement under the Securities Act of 1933 | offering | Offering and capital-structure relevance | 4 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | S-3 | Registration statement under Securities Act of 1933 | offering | Offering and capital-structure relevance | 4 | 8,749 | 8,748 | 3.882B | 336.318K / 674.278K / 2.066M / 9.161M | 83.854M | 8,749 | 8,748 | 1.325B | 118.350K / 247.204K / 695.554K / 2.507M | 13.039M | yes | complete_document_chunks |
| form | exact | S-3ASR | Automatic shelf registration statement | offering | Offering and capital-structure relevance | 4 | 4,196 | 4,194 | 1.535B | 316.094K / 601.494K / 1.058M / 2.088M | 3.778M | 4,196 | 4,194 | 588.948M | 117.582K / 256.105K / 404.631K / 799.668K | 1.696M | yes | complete_document_chunks |
| form | exact | S-4 | Registration statement under Securities Act of 1933 | corporate_transaction | High potential transaction relevance | 5 | 5,561 | 5,560 | 40.699B | 6.317M / 15.515M / 23.657M / 34.863M | 45.264M | 5,561 | 5,560 | 10.082B | 1.902M / 2.968M / 4.189M / 5.088M | 7.222M | yes | complete_document_chunks |
| form | exact | S-6 | Registration under 1933 act of securities of unit investment trusts registered on form N-8B-2 | other_disclosure | Content-dependent disclosure | 2 | 9,182 | 9,182 | 1.661B | 39.463K / 455.424K / 1.497M / 6.408M | 7.904M | 9,181 | 9,181 | 785.246M | 9.008K / 242.538K / 412.785K / 615.554K | 699.693K | yes | complete_document_chunks |
| form | exact | S-8 | Registration statement under Securities Act of 1933 to be offered to employees pursuant to certain plans | offering | Offering and capital-structure relevance | 4 | 19,012 | 19,012 | 1.502B | 67.650K / 123.670K / 251.651K / 533.526K | 1.470M | 19,011 | 19,011 | 346.702M | 17.199K / 24.152K / 73.376K / 201.976K | 533.749K | yes | complete_document_chunks |
| form | exact | S-8 POS | Post-effective amendment to employee benefit plan registration | offering | Offering and capital-structure relevance | 4 | 13,787 | 13,787 | 561.378M | 32.418K / 69.684K / 152.281K / 367.672K | 735.414K | 13,786 | 13,786 | 145.026M | 7.987K / 20.599K / 31.414K / 111.809K | 400.008K | yes | complete_document_chunks |
| form | exact | SBSE | Application for Registration of Security-based Swap Dealers and Major Security-based Swap Participants | administrative | Administrative or regulatory | 1 | 56 | 56 | 4.562M | 32.026K / 254.355K / 256.309K / 256.309K | 256.309K | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | SBSE-A | Application for Registration of Security-based Swap Dealers and Major Security-based Swap Participants that are Registered or Registering with the Commodity Futures Trading Commission as a Swap Dealer | administrative | Administrative or regulatory | 1 | 579 | 579 | 18.103M | 21.709K / 74.151K / 102.013K / 103.088K | 103.088K | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | SBSE-BD | Application for Registration of Security-based Swap Dealers and Major Security-based Swap Participants that are Registered Broker-dealers | administrative | Administrative or regulatory | 1 | 19 | 19 | 49.111K | 2.570K / 2.766K / 2.858K / 2.858K | 2.858K | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | SBSE-C | Certifications for Registration of Security-based Swap Dealers and Major Security-based Swap Participants | other_disclosure | Content-dependent disclosure | 2 | 56 | 56 | 68.939K | 1.221K / 1.295K / 1.387K / 1.387K | 1.387K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | SBSE-W | Request for Withdrawal from Registration as a Security-based Swap Dealer or Major Security-based Swap Participant | administrative | Administrative or regulatory | 1 | 4 | 4 | 9.804K | 2.387K / 3.110K / 3.110K / 3.110K | 3.110K | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | SBSEF | Security-Based Swap Execution Facility Application for Registration (with Amendment to Application) (and SBSEF Submission Cover Sheet) | administrative | Administrative or regulatory | 1 | 24 | 24 | 23.541K | 857 / 1.346K / 1.526K / 1.526K | 1.526K | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | SC 13D | Beneficial ownership report | ownership_activism | High-to-medium ownership and activism relevance | 4 | 32,385 | 32,385 | 4.212B | 89.910K / 243.753K / 638.301K / 1.584M | 25.525M | 32,384 | 32,384 | 666.791M | 15.849K / 36.049K / 83.584K / 226.020K | 1.424M | yes | complete_document_chunks |
| form | exact | SC 13E3 | Going-private transaction statement | corporate_transaction | High potential transaction relevance | 5 | 1,220 | 1,220 | 203.075M | 127.110K / 304.162K / 938.503K / 1.858M | 1.864M | 1,220 | 1,220 | 51.438M | 36.775K / 60.367K / 367.676K / 437.977K | 438.718K | yes | complete_document_chunks |
| form | exact | SC 13G | Short-form beneficial ownership report | ownership | Ownership relevance; usually delayed | 2 | 150,000 | 150,000 | 7.238B | 28.583K / 112.096K / 271.911K / 547.143K | 7.618M | 149,944 | 149,944 | 1.478B | 9.092K / 14.863K / 31.623K / 59.914K | 221.335K | yes | complete_document_chunks |
| form | exact | SC 14D9 | Target-company tender-offer recommendation | corporate_transaction | High potential transaction relevance | 5 | 1,696 | 1,696 | 221.187M | 22.555K / 515.673K / 950.315K / 2.115M | 3.442M | 1,696 | 1,696 | 85.939M | 7.363K / 239.772K / 343.263K / 415.774K | 542.625K | yes | complete_document_chunks |
| form | exact | SC TO-I | Issuer tender-offer statement | corporate_transaction | High potential transaction relevance | 5 | 8,281 | 8,281 | 413.542M | 34.729K / 96.044K / 233.596K / 365.366K | 1.057M | 8,281 | 8,281 | 120.180M | 7.138K / 37.342K / 99.444K / 107.768K | 277.576K | yes | complete_document_chunks |
| form | exact | SC TO-T | Third-party tender-offer statement | corporate_transaction | High potential transaction relevance | 5 | 1,897 | 1,897 | 91.487M | 37.646K / 84.021K / 198.624K / 613.226K | 856.339K | 1,897 | 1,897 | 21.987M | 9.976K / 17.663K / 41.403K / 103.495K | 327.365K | yes | complete_document_chunks |
| form | exact | SCI | Systems Compliance and Integrity | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | SD | Specialized Disclosure Report | other_disclosure | Content-dependent disclosure | 2 | 8,554 | 8,554 | 205.111M | 19.087K / 32.848K / 64.728K / 625.409K | 2.115M | 8,549 | 8,549 | 34.468M | 2.808K / 6.852K / 17.257K / 61.888K | 87.915K | yes | complete_document_chunks |
| form | exact | SDR | Application or Amendment to Application for Registration or Withdrawal from Registration As Security-Based Swap Data Repository Under the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | SE | Form for Submission of Paper Format Exhibits by EDGAR Electronic Filers | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | SF-1 | Registration Statement Under the Securities Act of 1933 | structured_finance | Structured-product disclosure | 2 | 108 | 108 | 150.552M | 1.327M / 1.902M / 2.341M / 2.346M | 2.346M | 108 | 108 | 60.183M | 588.269K / 638.500K / 694.693K / 700.099K | 700.099K | no | structured_extraction_only |
| form | exact | SF-3 | Registration Statement Under the Securities Act of 1933 | structured_finance | Structured-product disclosure | 2 | 286 | 286 | 969.337M | 2.720M / 5.556M / 6.898M / 26.588M | 26.588M | 286 | 286 | 316.194M | 820.551K / 1.960M / 2.361M / 2.392M | 2.392M | no | structured_extraction_only |
| form | exact | SIP | Application or amendment to application for registration as securities information processor | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | SUPPL | Voluntary prospectus or offering supplement | offering | Offering and capital-structure relevance | 4 | 870 | 870 | 665.964M | 638.802K / 1.155M / 2.670M / 12.727M | 12.727M | 870 | 870 | 257.672M | 277.320K / 409.074K / 650.841K / 1.969M | 1.969M | yes | complete_document_chunks |
| form | exact | T-1 | Statement of eligibility and qualification under the Trust Indenture Act of 1939 of corporations designated to act as trustees | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | T-2 | Statement of eligibility under the Trust Indenture Act of 1939 of an individual designated to act as trustee | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | T-3 | For applications for qualification of indentures under the Trust Indenture Act of 1939 | other_disclosure | Content-dependent disclosure | 2 | 124 | 124 | 27.430M | 158.181K / 535.061K / 883.824K / 917.456K | 917.456K | 124 | 124 | 5.877M | 37.913K / 98.357K / 187.947K / 197.851K | 197.851K | yes | complete_document_chunks |
| form | exact | T-4 | Application for exemption filed pursuant to Section 304(c) of the Trust Indenture Act of 1939 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | T-6 | Application under Section 310(a)(1) of the Trust Indenture Act of 1939 for determination of eligibility of a foreign personal to act as institutional trustee | other_disclosure | Content-dependent disclosure | 2 | 6 | 6 | 198.047K | 30.606K / 63.825K / 63.825K / 63.825K | 63.825K | 6 | 6 | 84.614K | 14.793K / 20.581K / 20.581K / 20.581K | 20.581K | yes | complete_document_chunks |
| form | exact | TA-1 | Uniform form for registration as a transfer agent and for amendment to registration | other_disclosure | Content-dependent disclosure | 2 | 1,549 | 1,549 | 169.351M | 14.585K / 343.921K / 1.024M / 1.026M | 1.026M | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | TA-2 | Form for reporting activities of transfer agents | other_disclosure | Content-dependent disclosure | 2 | 2,342 | 2,342 | 11.592M | 4.496K / 6.647K / 18.708K / 40.737K | 47.795K | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | TA-W | Notice of withdrawal from registration as transfer agent | administrative | Administrative or regulatory | 1 | 104 | 104 | 241.975K | 2.200K / 2.938K / 4.279K / 4.548K | 4.548K | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | TCR | Tip, Complaint, or Referral | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | TH | Notification of Reliance on Temporary Hardship Exemption | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | WB-APP | Application for Award for Original Information Submitted Pursuant to Section 21F of the Securities Exchange Act of 1934 | administrative | Administrative or regulatory | 1 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | no | preserve_only |
| form | exact | X-17A-19 | Report of Change in Membership Status | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART I | FOCUS Report, Part I | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART II | FOCUS Report, Part II Instructions | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART IIA | FOCUS Report Part IIa | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART IIC | FOCUS Report, Part IIC Instructions | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 PART III | FOCUS Report Part III | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | X-17A-5 SCHEDULE I | (Financial and Operational Combined Uniform Single) FOCUS Report: Information Required of All Brokers and Dealers Pursuant to Rule 17a-5 | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |
| form | exact | X-17F-1A | Missing/Lost/Stolen/Counterfeit Securities Report | other_disclosure | Content-dependent disclosure | 2 | 0 | 0 | 0 | n/a | 0 | 0 | 0 | 0 | n/a | 0 | yes | complete_document_chunks |

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
