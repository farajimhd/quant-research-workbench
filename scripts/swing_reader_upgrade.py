"""Explicit legacy identity bridge, guarded by live reader/checkpoint parity."""
import json
from math import prod
from hashlib import sha256
from itertools import product

from src.backend.swing_book_source import read_session as legacy_read, session_bounds, HISTORICAL_POLICY
from src.backend.swing_book_indexed_source import read_session as indexed_read, READER_VERSION
from src.market_engine.swing_book_v6 import StreamingSwingBookV6, VERSION

# Exact pre-upgrade builder bytes (CRLF and LF). Every other identity input
# must still match; this is not permission to accept arbitrary source changes.
LEGACY_BUILDERS = (
    '04e4895f5b928e53abd7716fd67d2979087123fea02cc5e69d3ec45d4152ecc5',
    '139e61ebb6447f418c52a2c6b68b1cb611c8793298a91919d29b393a13ee0671',
    '2afee112036c500021b78de743eb6374044b5fb4e3f1c23d8e284472aa263003',
)
LEGACY_CONTROLLERS = (
    '79cb1d19ed4f07a02ac4e09d08d1a95c25ba91856a4e0ffccb564c84bc6fc1c9',
    'e6ed95e374cdaa709431af546daabd88adc01ffc006dc0776e4a244db55110e2',
    '883be0cc9a43aa2087ff6f655b317a93dc7b33fc60d2b1e015d5524257b50e6b',
    '7993266343d422a845743c49d1a733a7d48b79d163446cdf5596c7eb5a920e02',
)
UPGRADE_PATHS = (
    'src/backend/swing_book_indexed_source.py',
    'scripts/swing_reader_upgrade.py',
    'scripts/prototype_structure_book_clickhouse.py',
)

# Exact pre-path-repair bytes. Every engine/source file outside this set must
# match. Path changes preserve old book identities and retain checkpoint parity.
PATH_BASELINE = {
    'scripts/build_swing_book_campaign.py': ('d9c1b3396873d4e6f8eb74dc1dc9966e575c53c50832adbb60d16cc046614b95', '65247006f47109b8fd422d6ea39479c08cb136579e96503469f82a4e121e18be'),
    'scripts/build_swing_structure_book.py': ('d87b5bd53ac8b01e49b1a68a3cbd1016d99941cb9b1740e46f92e775fc573138', 'cedfdecac05f4bb19611a79ba2dee119f37542320f67e1baf488069229d38f5a',
        '9fbdba2ac9b37debc327dd6364d6faf89b97e40b36f8bfa090fbd59dc4100234'),  # deployed mixed line endings; identical normalized source
    'scripts/swing_book_paths.py': ('789eb42cc7a8970859d43cd9029250e3e95b6103b8f624f845a80b7713645730', '6af290dad31bf8035f6593afd673aabe6fa59aa26929af5747ec8e3fe81cfea1'),
    'scripts/swing_reader_upgrade.py': ('55ad90cee68f824fe9160d6a5beebe3918fa976e1b63797fd4948d78af9f5cbc', '78be0b0e61ce36b2e1d68cea62c5110adb19ce72e5786aabb586a113b0882f89'),
}


def path_baselines(hashes):
    baseline = dict(hashes)
    if not any(k.replace('\\', '/') == 'scripts/build_swing_book_campaign.py' for k in hashes):
        # The old standalone builder did not hash its path helper.
        baseline = {k:v for k,v in baseline.items() if k.replace('\\','/') != 'scripts/swing_book_paths.py'}
    keys = [k for k in baseline if k.replace('\\', '/') in PATH_BASELINE]
    for values in product(*(PATH_BASELINE[k.replace('\\', '/')] for k in keys)):
        yield dict(baseline, **dict(zip(keys, values)))

# Exact pre-transport-fix files, LF/CRLF. All algorithm, reader and source
# identities outside this small set must match; daily parity remains mandatory.
TRANSPORT_BASELINE = {
    'scripts/prototype_structure_book_clickhouse.py': (
        '3aef19d9c77ca5e905e33a53d9c5e77bef9a7f3abffa2ae088d39e29ee6e1224',
        '6768743f8b019dd5a7b89c51dcc2fa502c70548407b07fa72929d2988b6019bb',
        '7541c6f5e6f668a5eb01af969d050b4eb90214427cff43cb1c3f3029eaa38f43',
        'dcd9d78760f5aa8bb68e74554c3e264715d81bcb2dfe1aafaac53062fe7cc3c0'),
    'scripts/build_swing_structure_book.py': (
        '80b8569d6fc3acf79792082886e937d446f29f21c1f367e0612652a9d58d9742',
        '582d9058d6c42547ed698eeb75d39bb9aa763607b7741200724f8579becfb42e'),
    'scripts/build_swing_book_campaign.py': (
        '96545eb1b3e40eedd58c43caaca5998f9ac6bd7d3ea9348ddccb79509ad6d60e',
        '41a4d6f388604f0b686bb64286b60e9cf34c7aa9348e93b058e0a4765ab29a5f',
        '272c33289d2d1f677460364d5433bd8aad2a2fdb6cb2a7a87d33a892dcce9ecb',
        '4339ffe0adfe8e65b74c16b6a103a407dfe40741b61096d72495a429d0f0a3ee'),
    'scripts/swing_reader_upgrade.py': (
        '296b429f0c06dfbaabf3fdeec6535451d5a49bf328f948b522fbb0b5b57cfdee',
        '7e81f939695acc5c4967e0a4efee6d28205582da0eed9d845f1bba9191f11737',
        '86a05afd8f51360ea7ceb32473e63b04e59075a8d51b916d09ad26aab1ee9cad',
        '3255e335024595150aae5c5642b7eee3d9e9cdf3d11e2deef9a2f246b76da67a'),
}


def transport_hash_matches(value, hashes):
    keys = [k for k in hashes if k.replace('\\','/') in TRANSPORT_BASELINE]
    for values in product(*(TRANSPORT_BASELINE[k.replace('\\','/')] for k in keys)):
        if digest(dict(hashes, **dict(zip(keys, values)))) == value:
            return True
    return False


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def legacy_hash_matches(value, hashes, *, campaign=False):
    baseline = {k:v for k,v in hashes.items() if k.replace('\\','/') not in UPGRADE_PATHS}
    builder_key = next((k for k in baseline if k.replace('\\','/') == 'scripts/build_swing_structure_book.py'), None)
    if builder_key is None:
        return False
    for builder in LEGACY_BUILDERS:
        baseline[builder_key] = builder
        for controller in LEGACY_CONTROLLERS if campaign else (None,):
            if campaign:
                baseline['scripts/build_swing_book_campaign.py'] = controller
            if digest(baseline) == value:
                return True
    return False


def build_identity(hashes, previous, *, indexed):
    current = digest(hashes)
    prior = previous.get('code_hash')
    if prior is None or prior == current:
        return current
    if indexed and (legacy_hash_matches(prior,hashes) or transport_hash_matches(prior,hashes)):
        # Retain the database/source fingerprint. The caller MUST run verify()
        # before any new book/session writes, even on subsequent resumptions.
        return prior
    if indexed and any(prior == digest(old) or legacy_hash_matches(prior, old)
                       or transport_hash_matches(prior, old) for old in path_baselines(hashes)):
        return prior  # verify() remains mandatory before new session writes.
    raise ValueError('Unsupported build code change; preserve the existing runtime')


def checkpoint(client, db, marker):
    rows = client.query(f"SELECT state_json FROM {db}.book FINAL WHERE valid_from_us={int(float(marker['closed_at'])*1000000)} ORDER BY level_id",'upgrade_checkpoint')
    state = dict(version=VERSION,closed_at=float(marker['closed_at']),sequence=int(marker['sequence']),
        levels=[json.loads(r['state_json']) for r in rows])
    if digest(state) != marker['state_hash']:
        raise ValueError('Reader upgrade found a corrupt saved checkpoint')
    return state


def verify(ticker, days, done, client, db, splits, *, stop_file=None):
    """Bounded per-ticker gate; no book writes. Repeated on every indexed resume.

    Compare the last certified session and the next unfinished session with
    the unchanged reference SQL, then reproduce the certified closing state.
    The normal builder subsequently verifies every completed prefix marker.
    """
    sessions = [d['source_date'] for d in days]
    if set(done) != set(sessions[:len(done)]):
        raise ValueError('Completed sessions are not a certified contiguous prefix')
    indices = sorted({i for i in (len(done)-1,len(done)) if 0 <= i < len(sessions)})
    evidence = []
    for index in indices:
        if stop_file is not None and stop_file.exists():
            raise KeyboardInterrupt('Stopped before reader verification session')
        session = sessions[index]
        print(f'{ticker} {session} | verifying indexed reader against reference',flush=True)
        original, old_revision = legacy_read(ticker,session,client,policy=HISTORICAL_POLICY,query_workers=1)
        if stop_file is not None and stop_file.exists():
            raise KeyboardInterrupt('Stopped after reference reader verification')
        optimized, revision = indexed_read(ticker,session,client)
        if original != optimized or old_revision != revision:
            raise ValueError(f'Indexed reader parity failed: {ticker} {session}')
        item = dict(session=session,bars=len(optimized),bars_hash=digest(optimized),source_revision=revision)
        if session in done:
            marker = done[session]
            if json.loads(marker['source_revision']) != revision:
                raise ValueError('Certified checkpoint source revision changed')
            seed = checkpoint(client,db,done[sessions[index-1]]) if index else None
            opening,closing = session_bounds(session)
            factor = prod(float(s['split_from'])/float(s['split_to']) for s in splits
                if seed is not None and seed['closed_at'] < session_bounds(s['execution_date'])[0].timestamp() <= opening.timestamp())
            engine = StreamingSwingBookV6(seed,opening.timestamp(),factor)
            for bar in optimized:
                engine.observe(*bar)
            state = engine.closing_state(closing.timestamp())
            checkpoint(client,db,marker)
            if digest(state) != marker['state_hash']:
                raise ValueError('Indexed reader did not reproduce certified closing state')
            item['state_hash'] = marker['state_hash']
        evidence.append(item)
    return dict(status='passed',reader=READER_VERSION,sessions=evidence,
        scope='last_certified_and_next_unfinished_session')
