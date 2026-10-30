# V59: fresh global search with logarithmic profit targets

Fresh 6144 founders (3072 templates and 3072 random), then 1024 candidates,
200 generations, 24 training and 12 display-only validation days, two workstation
replay lanes. No V58 genomes or financial receipts are reused.

Score = 0.5 * sum(ticker-date reward) + 0.5 * sum(session reward) - sum(session account DD).
Reward(p,C) = p below C; C + 0.1*C*log(1+(p-C)/C) above C.
Targets: ticker-date $500, session $1000. All losses and drawdowns remain counted.
Targets never stop execution. Ticker specialists use the same ticker reward.

Ten percent each of crossover slots use ordered sticky entry routing, reciprocal
entry confirmation/veto, or entry/management block exchange. Remaining slots keep
existing mask and Boolean methods. Routing is flattened, A-first when both gates
fire, and parent ownership persists while held. Graph crossover on routed parents
chooses a branch; routed mutation edits one branch. Total allocation remains 1024
nodes, including all branches. No individual-position state is shared across parents.

Search excludes split-age/event identifiers and raw/log price-level features in
all channels. Precise float/share quantities become decade buckets before absolute
and cross-sectional projection. Execution prices and split adjustments are untouched.

Validation display retains the latest available validation snapshot while the next
training generation is incomplete. Initial founders are read from generation 1.

Launch: python -B -m research.vectorized_backtest.v59.full_launch --global-only
--source-job <V58-job.json> --output <new-runtime> --code <committed-deployment>
Then python -B -m research.vectorized_backtest.v59.run_train --job <new-runtime/global-job.json>.
PYTHONDONTWRITEBYTECODE=1 is required. Deployment must be committed and hash verified.
