# RL trading V2

- This version learns only from its own account trajectories and net rewards. Never import teacher actions, teacher account states, hindsight eligibility, or return targets into observations or objectives.
- Use the certified ARTE read-only observation functions shared with V1 without changing V1. Read the entire certified population; do not silently restrict it to V1 teacher winners or listings with nonempty structural levels. Missing required certification fails closed.
- All ranks use completed one-second observations and stable listing identity. Entry ranks 1–100, hold-only buffer 101–120, sticky mandatory exits outside 120. Keep out-of-universe holdings visible until flat.
- Artifacts belong under the configured runtime root. Workstation execution or synchronization is outside the initial V2 implementation task: V1 is running there.
- Costs are explicit uncalibrated assumptions until measured. Do not claim executable profitability from synthetic or price-only validation.
