# Reference Data Experiments

This folder contains standalone validation scripts. They do not write to
reference gateway production tables.

## SEC Public Float Estimate Test

Script:

```powershell
python pipelines\reference_data\experiments\sec_public_float_estimate_test.py --sample-size 100 --candidate-pool-size 2000 --progress-every 10 --request-min-interval-seconds 0.12
```

Purpose:

1. Select active uniquely mapped SEC CIK to market symbols.
2. Read SEC `EntityPublicFloat` in USD from
   `q_live.sec_xbrl_company_fact_v1`.
3. Read latest Massive `free_float` from `q_live.market_security_float_v1`.
4. Fetch Massive daily aggregate close price around the SEC public-float
   measurement date.
5. Estimate public float shares:

```text
sec_estimated_float_shares = EntityPublicFloat / daily_close_price
```

6. Apply known split factors from `q_live.market_stock_split_v1` between the
   SEC measurement date and the Massive float effective date.
7. Compare estimated shares to Massive `free_float`.

Outputs are written under:

```text
prepared/reference_data/experiments/sec_public_float_estimate_test/<run_id>/
```

Files:

- `sec_public_float_estimate_rows.jsonl`
- `sec_public_float_estimate_summary.json`

Interpretation:

- Low median absolute percent error means SEC `EntityPublicFloat` can be a
  useful float proxy when combined with historical price.
- Negative `sec_lead_days` means the SEC filing arrived after the current
  Massive float effective date for that row.
- Positive `sec_lead_days` means SEC could have provided the float estimate
  before the Massive float effective date.
- This experiment validates the idea only; it does not populate
  `security_share_supply_fact_v1`.
