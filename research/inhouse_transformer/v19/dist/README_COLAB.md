# Colab Package for v19

Files in this folder are generated for Colab training.

## Drive Paths

- Code package in Colab: `/content/drive/MyDrive/quant-research-workbench/colab_code/v19`
- Data root in Colab: `/content/drive/MyDrive/quant-research-workbench/colab_data/v17_june2025/market_data`

## Secrets

Add `WANDB_API_KEY` in Colab Secrets before running `train_colab.ipynb`. No API keys are stored in the notebook, manifest, or zip package.

## Default Dates

- train: `2025-06-02` to `2025-06-30`
- validation: `2025-07-01` to `2025-07-07`
- test: `2025-07-01` to `2025-07-07`
- tickers: `ALL`
- allow target across session: `True`
- default epochs: `3`

## Resume and Checkpoints

- output name: `v19_generalization_june2025_all_tickers`
- wandb run name: `v19-generalization-june2025-all-tickers`
- resume latest: `True`
- fresh start default: `False`
- checkpoint policy: `last_only`

## Training Setup

- optimizer: `adamw`
- loss: `binary_cross_entropy_with_logits`
- learning rate: `0.0003`
- weight decay: `0.0001`
- scheduler: `cosine_warm_restarts` (T_0 steps `500`, T_mult `2`)
