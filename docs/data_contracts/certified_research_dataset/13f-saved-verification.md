# Saved dataset verification command

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

The existing launcher now accepts `verify --dataset-id EXISTING_UUID --database arte --env-file .env --format json`. Replace `EXISTING_UUID` with an installed dataset ID. Text output is the default. Acquisition/staging flags are rejected for this read-only command.

Verification loads the immutable saved plan and reconstructs its frozen runtime. Each canonical day binding must match fresh import/reporting certificates, and the complete canonical/reporting epoch must match the saved source hash. Incomplete/mutating sources or changed day bindings block verification. It independently rereads all planned product authorities through the normal controller verifiers.

The final computed root must match exactly one published campaign seal, including plan hash, source hash and unit count. An unsealed set of rows is not reported as a verified dataset. Missing producers/prerequisites, incompatible runtime metadata or schemas, wrong disk placement and changed outputs fail closed.

Readers use ClickHouse `readonly=1`; worker factories use the saved query-thread/memory configuration. Keeper coordination leases are acquired and released, including dictionary leases where required, but no ingestion, producers, schema installation or seal publication is invoked. Clients and leases close on failure/interruption. This command does not require a future-write capacity certificate.

Parser/guard tests cover required dataset IDs, acquisition flag separation, frozen epoch mismatch, missing canonical bindings, missing seals and absence of producer/admission calls. The CLI help path was exercised. Successful end-to-end verification of a fully populated production dataset remains untested; current missing original source/reference evidence is not bypassed. Full `run/resume` and initial plan publication remain unfinished.
