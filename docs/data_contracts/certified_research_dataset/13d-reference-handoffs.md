# Existing reference authority handoffs

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

`ReferenceAuthorityHandlers` registers `corporate_actions`, `float_allocation` and `reported_supply` as **read-only handoffs of existing certified scopes**. These names do not acquire or manufacture missing history.

Exact plan arguments are `entity_key`, `start`, `end` and `receipt`. The locator must identify the current dataset, security, family, exact range and current fact catalog. The handler validates storage policy/actual placement and independently rechecks stored receipt/output fingerprints. Missing, changed, mismatched or cross-dataset locators block. A private reader is closed on success or failure; no facts, provenance or receipts are inserted.

The handoff preserves an upstream scope's qualification. It does not establish original public vintages, acquisition completeness or a verified absence of corporate actions. Current Gateway split rows cannot become certified historical corporate data merely by passing through this handler. Producers for missing original evidence and explicit cross-dataset certified import remain unfinished.

Capacity classification reflects this concrete contract: these handoffs contribute campaign metadata but no new product storage. A future acquisition/import stage must have a distinct registered product and its own qualified write-rate demand; it cannot hide writes behind the handoff names.

Focused tests cover missing authority, identity/range/catalog mismatch, cross-dataset rejection and reader cleanup. A bounded live fixture reuses all three scopes without inserts before exercising the float consumer. It validates handoff mechanics, not the completeness of production corporate or allocation history.
