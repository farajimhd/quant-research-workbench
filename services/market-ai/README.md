# Market AI Service

Market AI is intentionally **not implemented as a runnable service** at this
stage.

The boundary is reserved for the future model-dependent inference service. Its
final shape depends on the trained ML model, input windows, multimodal cache,
feature contracts, and prediction publishing contract.

## Future Responsibility

Once the final model is selected, Market AI should:

- consume QMD compact market-event streams or replay iterators
- consume Text Embed Gateway outputs such as news and SEC embeddings
- manage the model-specific multimodal cache
- run the selected inference pipeline
- expose prediction APIs or streams
- optionally persist prediction rows if a durable prediction contract is defined

## Current Policy

Do not run `services/market-ai/run_service.py` or `scripts/run_market_ai.ps1`.
Both entrypoints intentionally exit with a message explaining that the service
is disabled.

Prototype batching code under `services/market-ai/src/market_ai` remains in the
repo only as prior exploratory code. It is not the approved production service
contract.
