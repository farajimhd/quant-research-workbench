from research.market_labels.v3.contract import SCOPE as PARENT_SCOPE

VERSION = 'v66-hindsight-execution-spread-trajectory-v4'
SOURCE_SHA = 'fc34dad3028af2368276b86d62329e8b511cb0d451f04ef190f1466eecac9b53'
SCOPE = dict(PARENT_SCOPE, trajectory='One ENTRY and one EXIT per accepted 1b position; pre-action state; elapsed scheduled 1s intervals; no fabricated candles',
    duration='Remaining seconds/candles to the selected reference exit; null while flat outside entry; hindsight target only')
