# Original listed-security cover evidence

`sec_security_cover.extract` preserves inline-XBRL `Security12bTitle`,
`TradingSymbol` and `SecurityExchangeName` from exact retained SEC document text.
It checks source hashes, strict XML bounds, issuer CIK, DEI namespace, context
period and explicit dimensions. Continuations, transforms, nil values, excluded
text, typed dimensions and ambiguous contexts remain explicit rejections.

Fields are grouped by complete dimensions and context period. Different values
for a field remain unresolved; missing fields remain incomplete. Exact source
positions, values, period and context identifiers survive. No ticker aliases,
exchange names or stock-class descriptions are guessed. A live retained Apple
10-K read produced its AAPL cover evidence; fixtures tested conflicting and
continued fields.

The fundamental extraction path now gathers the same cover observations during
its existing XML pass and preserves them once in document extraction metadata.
They therefore flow into original fundamental source publication through the
unified launcher without a second payload fetch or parser pass. The standalone
cover extractor also provides candidate grouping for mapping work.

This evidence is not issuer-class completeness or an allocation certificate.
The current Gateway float resolver uses `tradable_classes=1` as an estimate
condition. Certified historical research cannot infer all issuer classes from
that count: unlisted/nontradable classes and dated listing changes remain relevant.
The next mapping producer must connect original class contexts and cover fields
to dated canonical security identifiers, independently prove any issuer-wide
allocation/completeness claim, preserve unresolved mappings, and handle class
changes by effective intervals. Cover context periods are source periods, not
automatically listing-validity intervals. A current conid does not supply that
missing dated evidence. No existing Gateway behavior is modified by this step.

`sec_class_mapping.resolve` now supplies the bounded deterministic candidate matcher.
It independently checks the cover hash and archive public-time evidence, requires
an admitted dated universe and unique CIK-to-issuer bridge, and uses explicit
canonical exchange aliases. A match requires the exact reported ticker, issuer
and exchange and one eligible listing interval covering the context-end day's
04:00–20:00 New York market window. Expired listings, missing evidence, incomplete
cover fields, exchange ambiguity and multiple matches remain unresolved. Context
dates are only candidate matching points; they do not prove the cover fields were
effective throughout that period or establish allocation intervals.

The result binds universe, dictionary, document/time and cover hashes and retains
class dimensions, positions and rejection reasons. It remains
`allocation_certified=false` and `class_completeness_proven=false`. Fixtures verify
exact matching, expired listings, ambiguous exchanges and source mutation. This
matcher has not yet been connected to a live historical mapping publication or
used to generate an allocation receipt; that integration and independent issuer
completeness evidence remain unfinished.
