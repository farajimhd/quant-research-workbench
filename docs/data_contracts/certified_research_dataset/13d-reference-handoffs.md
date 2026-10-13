# Existing reference authority handoffs

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

`ReferenceAuthorityHandlers` registers `corporate_actions`, `float_allocation` and `reported_supply` as **read-only handoffs of existing certified scopes**. These names do not acquire or manufacture missing history.

Exact plan arguments are `entity_key`, `start`, `end` and `receipt`. The locator must identify the current dataset, security, family, exact range and current fact catalog. The handler validates storage policy/actual placement and independently rechecks stored receipt/output fingerprints. Missing, changed, mismatched or cross-dataset locators block. A private reader is closed on success or failure; no facts, provenance or receipts are inserted.

The handoff preserves an upstream scope's qualification. It does not establish original public vintages, acquisition completeness or a verified absence of corporate actions. Current Gateway split rows cannot become certified historical corporate data merely by passing through this handler. Producers for missing original evidence remain unfinished; qualified cross-dataset import is described below.

Capacity classification reflects this concrete contract: these handoffs contribute campaign metadata but no new product storage. Acquisition/import stages require a distinct registered product and their own qualified write-rate demand; they cannot hide writes behind the handoff names.

Focused tests cover missing authority, identity/range/catalog mismatch, direct cross-dataset rejection and reader cleanup. A bounded live fixture reuses all three scopes without inserts before exercising the float consumer. It validates handoff mechanics, not the completeness of production corporate or allocation history.

## Qualified import producer

`reference_import` is now a separate registered **write product**. It imports an
already-qualified corporate, allocation or reported-supply scope from another
dataset in the same database. It does not certify current Gateway observations
or manufacture missing original publication history. A producer for obtaining
and qualifying missing original corporate/allocation evidence remains unfinished.

The importer independently verifies the source receipt, actual facts, normalized
value states and every referenced provenance record. Missing, duplicated or
changed proofs block even a previously completed import. Dataset and attempt IDs
are replaced server-side; values, units, periods, availability, precision,
confidence, reasons and original evidence hashes remain intact.

A deterministic target attempt makes interrupted imports resumable. Partial rows
must be an exact subset of the source projection; changed or duplicate rows are
rejected. Only missing rows are inserted, followed by exact output fingerprint
verification and receipt publication. A complete verified resume writes nothing.
Shared provenance is reused without a dictionary insert. All target parts remain
on `live_market_ssd` and no data leaves ClickHouse.

Discovery adds an explicit import unit and a dependent read-only family handoff
when a suitable donor exists. Missing or competing donors remain explicit
requirements. Capacity must include a qualified `reference_import` write rate;
import costs cannot hide behind read-only authority names. The imported family
handoff uses `entity_key`, `start`, `end` and `import_parent_hash` and validates
its single verified import receipt parent.

Before initial membership publication, adjustment-context discovery uses the
qualified Gateway interval projection and the donor corporate scope. Runtime
adjustment resolution still verifies the published target membership. This
preserves the same causal action hash without requiring early membership writes.

A real isolated ClickHouse test verifies partial restart, value/availability
preservation, no-insert receipt reuse and rejection after provenance mutation.
This proves import mechanics; it does not qualify missing production evidence.
