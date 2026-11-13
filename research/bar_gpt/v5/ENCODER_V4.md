# V4: pretrained encoder and small masked-column MLP

The large configuration retains the exact V3 ColumnEncoderV3: width512,
6layers,8heads,feedforward2048. A single512-dimensional embedding is the only
market-information route into the decoder. The shared decoder projects to128,
adds feature identity and the same known UTC timing projection, then applies
LayerNorm,128-to128 linear,GELU,128-to64 linear. There is no decoder attention,
query interaction or market skip connection. It predicts requested masked
columns only. V3 preparation,60percent column masking,valid-masked-only
feature-balanced SmoothL1,local-reference transformations and raw/relative
validation metrics are reused unchanged.

run_encoder_v4 and campaign_v4 are isolated orchestration copies of V3; the
running V3 entry points are untouched. Changes to shared orchestration should
be reconciled explicitly in future; preprocessing remains version3 intentionally.

## Initialization

The initial planned parent was V3 epoch50; the latest user instruction chooses
the last durable checkpoint instead (epoch30 for the 2026-11-13 launch). Choose its immutable path with
--encoder-checkpoint; no latest/best discovery silently changes the parent.
The path can change before launch. Strict encoder keys/shapes/finiteness and
before/after file SHA checks precede transfer. Only encoder weights transfer;
decoder and optimizer start fresh. encoder-initialization.json records the
source epoch,path,SHA and transfer policy. A V4 --resume instead restores its
own full state and does not reinitialize from V3.

## Balanced data dependency

The currently extracting source is phase1-coarse-supplement-v1-r2. Its raw
result deliberately has training_ready=false. A gracefully stopped partial run
may be released with run_release_partial_v4: every sealed publication receives
hash/schema/cutoff audits, a separate immutable coarse member and a sealed
supplement-snapshot.json. Unsealed work is excluded. The original corrected
members and exact prior split membership remain intact. Do not point the ordinary V3 loader directly at raw supplement
files or claim equal timeframe counts when finite inventory falls short.

V4 defaults to D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4.
It requires an explicit audited split and balanced-release.json. The release
body contains version=bargpt-balanced-release-v4,training_ready=true,cohort_hash,
supplement_result_sha256,member_contracts (name to contract-file SHA256),
split_sha256. Its hash is the shared canonical digest of the body without hash.
The cohort must contain at least corrected base,liquid supplement,new coarse
component. Existing member completion/seal checks apply. The split must name
the exact cohort. Missing/incomplete releases fail before output creation or
GPU initialization. No old-cohort fallback is permitted.

Release creation remains an explicit prerequisite. A partial snapshot is not
extraction completion or a claim of equal timeframe counts. The 2026-11-13
release adds 521,583 training and 255,824 validation windows from 26,092 sealed
publications. Total split counts are 186,575,722 and 47,750,007 respectively.
The learning guard uses an independent seeded random model before encoder
transfer; fresh decoder/optimizer state and the random-control baseline are
not inherited from V3.

Example after release and explicit checkpoint selection:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.bar_gpt.v5.run_encoder_v4 --mode profile --model large --models large --output D:/TradingML/runtimes/bar_gpt/encoder-v4-pilot --dataset D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4 --split-manifest D:/TradingML/runtimes/bar_gpt/phase1-v6-cohort-balanced-v4/split.json --encoder-checkpoint <IMMUTABLE_V3_EPOCH50_CHECKPOINT>
```

No training launch or GPU profiling is performed by importing or testing V4.
