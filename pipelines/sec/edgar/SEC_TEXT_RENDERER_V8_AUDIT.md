# SEC Text Renderer v8 Audit

## Verdict

`sec_packed_text_renderer_v8` is the canonical renderer for both historical and
live SEC ingestion. The audit found and fixed three material defects:

1. The active producer used `sec_text_normalizer_v1` instead of the packed
   renderer, and skipped every XML source document.
2. XML rendering repeated full ancestor paths for every leaf and could make a
   document larger than its source.
3. HTML table rendering discarded empty cells and `colspan`/`rowspan`, which
   could attach values to the wrong column names.

The final fresh-sample loop found no remaining safe, general-purpose renderer
optimization. Further reduction would require semantic deletion of legal text,
short headers, form instructions, or numeric-looking lines and is therefore not
part of deterministic normalization.

The source table was not modified. Existing rows in
`q_live.sec_filing_text_rendered_v3` are still historical
`sec_text_normalizer_v1` output and must be rebuilt from
`q_live.sec_filing_text_v3` before v3 SEC embeddings are generated.

## Audit Scope

- Audit date: `2026-07-15`
- ClickHouse: `26.3.12.3`
- Source: `q_live.sec_filing_text_v3 FINAL`
- Existing derivative: `q_live.sec_filing_text_rendered_v3 FINAL`
- Renderer after remediation: `sec_packed_text_renderer_v8`
- Source rows: `24,378,949`
- Source accessions: `5,886,650`
- Source characters: `8,796,397,696,813` (about `8.80T`)
- Existing rendered rows: `21,223,844`
- Existing rendered accessions: `3,298,268`
- Existing rendered characters: `730,923,914,677` (about `730.92B`)

All distribution queries used logical current rows through `FINAL`. Full-text
sample fetches used archive date plus `(cik, accession_number, document_id,
content_format)` so ClickHouse could prune by partition and primary key.

## Source Size Distribution

| Statistic | Characters |
| --- | ---: |
| Minimum | 4 |
| P50 | 22,507 |
| P75 | 58,476 |
| P90 | 170,747 |
| P95 | 407,577 |
| P99 | 3,007,422 |
| P99.9 | 91,882,500 |
| Maximum | 601,341,005 |

| Format | Rows | Row Share | Characters | Character Share | P50 | P90 | P95 | P99 | P99.9 | Maximum |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| HTML | 21,075,094 | 86.45% | 4.551T | 51.74% | 26,812 | 179,391 | 418,073 | 2,999,422 | 25,240,210 | 309,667,314 |
| XML | 3,105,891 | 12.74% | 4.239T | 48.19% | 5,764 | 76,597 | 377,199 | 3,742,038 | 239,631,340 | 601,341,005 |
| Plain text | 197,964 | 0.81% | 6.398B | 0.07% | 3,524 | 11,651 | 12,773 | 294,351 | 4,468,199 | 134,677,623 |

The row count is dominated by HTML, but almost half of all source characters
are XML. The extreme XML tail is primarily NPORT/N-CEN/N-MFP fund data and
EX-102 asset-level datasets, not ordinary filing prose.

| Format | Size Band | Rows | Characters |
| --- | --- | ---: | ---: |
| HTML | <10K | 3,870,039 | 24.742B |
| HTML | 10K-100K | 13,587,539 | 466.674B |
| HTML | 100K-1M | 3,043,273 | 780.277B |
| HTML | 1M-10M | 511,845 | 1,378.046B |
| HTML | 10M-100M | 59,980 | 1,521.757B |
| HTML | >=100M | 2,418 | 379.922B |
| XML | <10K | 2,272,189 | 10.764B |
| XML | 10K-100K | 555,027 | 15.440B |
| XML | 100K-1M | 215,049 | 80.000B |
| XML | 1M-10M | 37,390 | 83.555B |
| XML | 10M-100M | 5,798 | 387.793B |
| XML | >=100M | 20,438 | 3,661.028B |
| Plain text | <10K | 160,231 | 0.473B |
| Plain text | 10K-100K | 33,754 | 0.461B |
| Plain text | 100K-1M | 3,272 | 0.957B |
| Plain text | 1M-10M | 610 | 1.699B |
| Plain text | 10M-100M | 93 | 2.313B |
| Plain text | >=100M | 4 | 0.495B |

## Existing Rendered Data

Every current rendered row audited was produced by
`sec_text_normalizer_v1` using `html_text_v1` or `plain_text_v1`. There are no
XML rendered rows from that producer.

| Statistic | Existing Rendered Characters |
| --- | ---: |
| Minimum | 27 |
| P50 | 6,519 |
| P75 | 15,410 |
| P90 | 49,950 |
| P95 | 90,555 |
| P99 | 501,145 |
| P99.9 | 2,645,405 |
| Maximum | 99,049,261 |

This table is useful as a baseline, not as accepted model input. It omits XML,
uses the stale parser, and predates the table-grid correction.

## Iteration 1: XML and Layout

The first audit used one approximately 30M-character source document from each
format.

| Format | Accession | Source | v6 Packed | v8 Packed | v8 / Source | Result |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| HTML N-PX | `0001379491-21-003793` | 29,998,701 | 13,044,335 | 13,044,335 | 43.48% | Existing labeled vote tables were already sound |
| XML N-PX | `0000930413-24-002522` | 29,984,061 | 38,162,820 | 16,904,601 | 56.38% | Full-path expansion removed; all record fields retained |
| Plain N-PX | `0000930413-19-002351` | 29,941,230 | 13,546,923 | 12,490,308 | 41.72% | Separator and explicit page-marker lines removed |

### XML Before

```text
<proxyVoteTable/proxyTable/issuerName>: AFCON Holdings Ltd.
<proxyVoteTable/proxyTable/cusip>: M01870126
<proxyVoteTable/proxyTable/vote/voteRecord/howVoted>: FOR
```

### XML After

```text
<proxyVoteTable>
<proxyTable> issuerName=AFCON Holdings Ltd.; cusip=M01870126; isin=IL0005780130; meetingDate=07/03/2023; voteDescription=Reelect Israel Raif as Director; voteCategories/voteCategory/categoryType=DIRECTOR ELECTIONS; voteSource=ISSUER; sharesVoted=32.000000; sharesOnLoan=0; vote/voteRecord/howVoted=FOR; vote/voteRecord/sharesVoted=32.000000; vote/voteRecord/managementRecommendation=FOR; voteSeries=S000001857
```

The compact form keeps tags as structural labels while avoiding the repeated
root path on every value. Repeated top-level XML children are rendered as one
tagged record per line. Non-record XML still uses compact parent/leaf paths.

### Plain Text Before

```text
College Retirement Equities Fund
- ------------------------------------------------------------------------------
(Exact name of registrant as specified in charter)
```

### Plain Text After

```text
College Retirement Equities Fund
(Exact name of registrant as specified in charter)
```

Only separator-only lines and explicit `<PAGE>` markers are removed. Paragraph
text on either side is retained.

## Iteration 2: HTML Table Grid

The second loop used a large 10-K, 8-K, material agreement, proxy XML, and
EX-102 XML. It found a material table defect in accession
`0001193125-26-076632`: empty spacer cells and HTML spans had been discarded,
so values shifted left under the wrong labels.

### Incorrect v6 Row

```text
Row=Superman Holdings, LLC; Reference Rate and Spread=S +; Interest Rate (3)=4.50 %; Maturity Date=8.17 %; Par Amount/Shares (4)=08/29/2031; Cost (5)=6,504; Fair Value=6,455
```

### Correct v8 Row

```text
Investments (1) (2)=Superman Holdings, LLC; Footnotes=(6) (9); Investment=First Lien Debt; Reference Rate and Spread=S + 4.50 %; Interest Rate (3)=8.17 %; Maturity Date=08/29/2031; Par Amount/Shares (4)=6,504; Cost (5)=6,455; Fair Value=6,504; Percentage of Net Assets=0.37
```

The fix builds a positional table grid before header inference:

- `colspan` is expanded across its actual columns.
- `rowspan` values are carried into subsequent rows.
- Empty internal cells remain positional instead of being removed.
- Values in adjacent cells under one spanning header are combined.
- A label row appearing after values is not treated as a forward header.
- Rows before a true header are retained as table preamble text.

The same 29.98M-character 10-K produced 1,115,243 packed characters. Its stale
v1 database row has 1,614,040 characters. The reduction comes mostly from
removing inline-XBRL resources and layout markup; the corrected grid adds some
necessary labels back compared with the unsafe v6 output.

The EX-102 sample `0000950131-24-002948` fell from 29,531,355 source
characters to 15,827,014 packed characters while preserving its asset fields.

## Iteration 3: Fresh Samples

None of these documents was used to design the fixes.

| Format / Type | Accession | Source | v8 Packed | Ratio | Tables | Duplicates | Audit Result |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| HTML 10-Q | `0001587987-22-000052` | 29,512,290 | 2,410,553 | 8.17% | 7,674 | 39 | Multi-column financial tables aligned; no date/rate swap |
| HTML 8-K | `0001104659-26-071926` | 13,209,514 | 3,159 | 0.02% | 17 | 0 | Source size was dominated by hidden inline-XBRL resources; visible filing retained |
| XML N-PX | `0000898745-24-000619` | 29,759,547 | 16,946,611 | 56.95% | 0 | 70 | 469,617 simple leaves accounted for; 840 fields replaced only inside 70 exact duplicate records |
| XML EX-102 | `0001917698-24-000045` | 29,270,399 | 15,292,629 | 52.25% | 0 | 0 | All 468,295 simple leaf values represented as fields |
| Plain N-PX | `0001193125-19-221231` | 29,547,752 | 11,278,346 | 38.17% | 0 | 16,426 | Layout removed; filing and vote text retained |

The apparently extreme 8-K ratio was checked against the stale v1 derivative,
which contains only 3,481 characters. This confirms that the 13.2M source is
mostly hidden inline-XBRL infrastructure rather than 13.2M characters of visible
8-K prose.

## Duplicate Policy

Duplicate detection remains deliberately conservative:

- Normalize whitespace and case only for the comparison key.
- Compare complete blocks, not fuzzy substrings.
- Require at least `200` normalized characters.
- Keep the first block unchanged.
- Replace later exact blocks with `DUPLICATE of [first 15 char]`.
- Preserve up to five duplicate examples in audit output.

Short headings, company names, table labels, and form captions are not removed
because the agreed 200-character threshold intentionally protects them.

## Production Integration

`pipelines/sec/edgar/sec_filing_text_extract_parts.py::build_rows` is shared by:

- historical daily-archive extraction; and
- `pipelines/sec/edgar/sec_pipeline/live_pipeline.py`, used by SEC gateway live
  ingestion.

That shared row builder now invokes v8 for HTML, plain text, and eligible XML.
The obsolete extractor-local HTML/plain normalizer was deleted. Production calls
disable the audit-only intermediate string to avoid duplicating large rendered
documents in memory.

Structured NPORT, N-CEN, and N-MFP XML remains in the complete source table but
is explicitly excluded from model text. Those machine datasets belong in their
structured SEC/XBRL products. N-PX proxy records and EX-102 asset records are
not blanket-excluded; v8 renders them as tagged records.

## Rejected Optimizations

The following were evaluated and rejected as unsafe generic rules:

- Removing SEC legal, certification, signature, risk, or exhibit language.
- Removing all repeated blocks below 200 characters.
- Removing standalone numbers without explicit page-break evidence.
- Rewriting narrative prose with an LLM.
- Truncating source or packed text.
- Guessing table headers after an earlier multi-value row.
- Dropping XML tags or XML values to reduce token count.

The renderer removes a numeric HTML footer only when it is immediately followed
by explicit `page-break` markup. This is structural evidence, not a numeric-line
heuristic.

## Stop Condition and Next Step

The renderer reached the stop condition after the fresh-sample loop:

- no unresolved table-column shift in inspected large documents;
- no unexplained XML leaf-field loss;
- no source truncation or packed-text cap;
- duplicate replacement follows the agreed exact/200-character policy;
- remaining possible reductions require semantic deletion rather than layout
  normalization.

Before tokenization or embedding, rebuild only
`q_live.sec_filing_text_rendered_v3` from the already complete
`q_live.sec_filing_text_v3`. Do not rerun archive acquisition or source
extraction. Use `sec_filing_text_rendered_v3_rebuild.py`; it processes monthly
source partitions through server-side Parquet exchange, applies v8 in bounded
parallel workers, inserts into a resumable staging table, validates complete
source accounting plus renderer/hash/length/key invariants, and performs an
explicit atomic cutover while retaining the stale table as a backup.

The production rebuild additionally binds resume to a logical-row metadata
hash, isolates staging per run, verifies the Python/ClickHouse file mount before
creating tables, joins the parent filing form by `filing_id`, and permits only
the renderer's explicit structured-fund-XML exclusion. Any other empty render
is a hard failure with document-level diagnostics.

After the first full rebuild exposed a 32 GiB query failure, parent form lookup
was moved out of each monthly large-text export. A follow-up live query proved
that large-text `FINAL` itself also performs an unbounded cross-partition
revision merge. One compact, watermarked SQLite authority now records both
filing forms and the exact current source version for each logical text key.
Monthly workers stream one physical partition without `FINAL`, select the
cross-partition authority locally, and perform no global join or text sort. The
production ingestion contract also removed both the minimum-length skip and
every rendered-text cap argument.
