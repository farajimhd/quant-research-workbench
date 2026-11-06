# Selected position trajectories for 1c

Derive one ENTRY and one EXIT per accepted execution-priced 1b position, without
changing 1a opportunities, 1b economics, decisions, groups or selection. Publish
an additive review dataset under `<source>/labels/1c-trajectory-v1`.

Every saved valid-price candle receives `action_1c`: WAIT outside positions,
ENTRY at the selected entry, HOLD strictly between endpoints, EXIT at the
selected target. Missing candles are not fabricated. Duplicate, missing,
overlapping or shared-clock position endpoints fail closed.

`position_state_before` is 0 at ENTRY and 1 at EXIT; `position_state_after` is
1 at ENTRY and 0 at EXIT. `position_age_seconds` and `position_age_candles`
are zero when flat and at ENTRY, and elapsed time since entry when held.
With this fixed 1s dataset, candles means scheduled one-second intervals,
not observed-row count. Sparse gaps do not compress time.

`remaining_duration_seconds` and `remaining_duration_candles` count down to
the chosen EXIT, including the full duration at ENTRY and zero at EXIT.
They are null outside a position and accompanied by `remaining_duration_valid`.
They are hindsight targets, never model inputs. The reference exit is known
even if the enclosing MACD episode is right-censored; this duration does not
claim the uncensored lifetime of an economic opportunity.

`position_id_1c`, entry and exit clocks identify the selected reference position.
`teacher_probabilities_1c` is one-hot in ENTRY, WAIT, HOLD, EXIT order;
`label_value_1c=1` and `trajectory_label_valid=true` describe the reference
schedule, not economic certainty. Preserve the independent 1b economic mask:
a reference WAIT does not certify that every alternative entry is bad.
Reference state/age must be replaced with actual account state/age during
agent execution; this is not a simulated-fill dataset.

The input 1b dataset is SHA-pinned to the user-reviewed v3 publication. All
parent rows remain identical and ancillary files are copied byte-for-byte.
All 188 days must pass before publishing the app registry; 1a/1b remain
available in the same inspector. No training is started.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe -B -m research.market_labels.v4.run_build --workers 4 --publish
```

`--days` and a separate `--output` support a non-publishable canary. STOP
drains admitted work; resume verifies completed seals and the source identity.
