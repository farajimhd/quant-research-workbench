# Execution liquidity priced 1b labels

V3 preserves original 1a fields and target clocks, spread fees, discounting,
earliest qualifying entry selection and overlap grouping. It replaces V2's
exact-second quote-event requirement with the certified execution standing book.
Output is a new review-only `<source>/labels/1a1b-spread-v3` dataset; V2 remains
unchanged. BarGPT, optimizer authority and 1c are not changed.

For each label/target clock, take the latest available execution snapshot in
the same session. Retain timestamped invalid and zero-size snapshots so an
older valid book cannot survive a withdrawal. No age cutoff applies, matching
the existing persistent execution contract. Prices must be positive and
uncrossed, the book valid, and displayed sizes at least one share. There is no
cross-session fill, future lookup, database fallback, or close-price fallback.
The canonical liquidity producer already excludes malformed/crossed source
quotes; V3 does not reconstruct events rejected by that producer.

Missing/invalid evidence and unsupported original EXIT targets produce
UNLABELLED, zero teacher probabilities and economic_label_valid=false rather
than economic WAIT supervision. Quote source, book availability, original
quote timestamp, age, validity reason and target evidence are saved for review.
Reference schedules remain distinct from economic validity.

Run from the hash-verified committed workstation deployment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe -B -m research.market_labels.v3.run_build --workers 4 --publish
```

Use `--days 2026-01-05 2026-07-06 --output <separate-runtime-canary-root>` for a
bounded canary. Partial inventories cannot publish. STOP drains admitted days;
resume verifies seals. Only a complete inventory updates the review app registry.
The original label dataset remains hash-pinned. Review at
`http://localhost:5173/?research=v66-labels#research-workspace`, phase 1b.
