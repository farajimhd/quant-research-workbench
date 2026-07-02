# Temporal Event Model v3

`temporal_event_model/v3` trains the multimodal temporal model described in
`TRAINER_MODEL_DESIGN_GUIDE.md` on the ticker-month rolling cache.

## Main Entry Points

- `run_train.py` is the recommended launcher. It embeds workstation defaults and
  forwards any override arguments to `train.py`.
- `train.py` contains the stateful trainer, checkpointing, W&B logging, local
  JSONL metrics, Rich progress panels, and model artifact export.
- `test_smoke.py` runs a small CPU shape/loss/artifact smoke test.
- `plot_dummy_batch_shapes.ipynb` creates a dummy batch, prints nested tensor
  shapes, runs a forward pass, and prints losses.
- `plot_model_diagram.ipynb` exports the same model artifacts that a run writes
  under `artifacts/model`.

## Default Training Command

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\temporal_event_model\v3\run_train.py
```

Useful overrides:

```powershell
python research\temporal_event_model\v3\run_train.py -- --months 2019-02,2019-03,2019-04 --batch-size 512 --max-steps 10000
```

For a local shape smoke:

```powershell
python research\temporal_event_model\v3\test_smoke.py
```

For a one-step trainer smoke:

```powershell
python research\temporal_event_model\v3\train.py --dummy-data --wandb-mode disabled --progress-layout text --batch-size 2 --max-steps 1 --validation-steps 1 --validation-batches 1 --d-model 32 --event-layers 1 --event-heads 4 --fusion-layers 1 --fusion-heads 4 --output-root C:\tmp\temporal_v3_train_smoke
```

## Data Contract

The trainer uses `AsyncTickerMonthBatchLoader` in `raw_stream` event mode.
The v3 loader config explicitly requests this event column order:

```text
event_meta, price_primary_int, price_secondary_int, size_primary,
size_secondary, exchange_primary, exchange_secondary, condition_token_1..5,
utc/session time features
```

Labels are grouped by task:

- `bar_inputs["ticker_intraday_bars"]`: backward same-session intraday context
  bars for `trade`, `quote_bid`, and `quote_ask`, aligned to the same horizon
  list as intraday labels but clipped backward to the session start.
- `future_bar_values`: separate `trade`, `quote_bid`, and `quote_ask` regression heads.
- `intraday_labels`: halt/resume/news-risk/LULD and future news/SEC arrival flags.
- `corporate_action_labels`: daily corporate-action classification horizons.

Losses are unweighted by default. Each active task contributes one masked mean
term; the final loss is the mean of active task losses.

## Run Artifacts

Every training run writes one run directory under `output-root/run-name`:

```text
artifacts/model/model_details.json
artifacts/model/model_parameters.jsonl
artifacts/model/model_summary.txt
artifacts/model/model_architecture.mmd
artifacts/model/model_architecture.md
checkpoints/checkpoint_latest.pt
metrics.jsonl
run_manifest.json
```

Optional `torchinfo` and `torchview` files are written when those packages are
installed. If not, the corresponding `*_error.txt` files explain why.

## Stateful Training

Checkpoints include:

- model, optimizer, scaler, and RNG state
- train and validation loader state
- model card payload with dataset id, period/months, sample counts, data groups,
  latest metrics, and run root

Resume with:

```powershell
python research\temporal_event_model\v3\run_train.py -- --resume-checkpoint D:\TradingML\runtimes\temporal_event_model\v3\train\<run>\checkpoints\checkpoint_latest.pt
```
