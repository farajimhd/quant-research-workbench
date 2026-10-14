# Original-document fundamental observations

`sec_fundamental_context.extract` applies the frozen app metric catalog to exact
retained document text. It reuses the bounded strict XML parser and source hash
checks used for shares. The default share extraction contract stays unchanged.

The result retains every selected observation or rejection, source position,
concept namespace, context identifier, explicit dimensions, unit identifier,
decimals, scale, exact decimal value, instant/duration period and document hash.
The extraction hash binds this result and the metric catalog hash. Supported
concept namespaces currently cover reviewed DEI and US GAAP namespace forms;
other namespaces are rejected explicitly. No HTML recovery or entity expansion
is enabled. Currency, shares, pure ratios and currency-per-share units are
supported; unsupported transformations, typed dimensions or malformed periods
remain visible rejections. This output is bounded in memory and is not persisted
by this module.

A live bounded read of the retained Apple accession `0000320193-25-000079`
passed extraction, including more than 50 observations, duration contexts and
USD units. This establishes parser behavior for that filing, not full catalog,
issuer, taxonomy or historical-period coverage. Synthetic fixtures separately
verify exact negative scaled decimals, per-share units and rejected contexts.

These observations are explicitly `certified=false` and
`original_vintage_qualified=false`. Publication must still connect each exact
document to its archive witness and public-time qualification, reconcile original
values against existing bulk/company-fact records with matching concept, unit,
period and dimensions, preserve revision conflicts, and certify coverage.
Class/issuer allocation and corporate-action basis are separate dependencies for
float. Existing bulk fundamental projection does not acquire original-document
parity merely because this parser succeeds.
