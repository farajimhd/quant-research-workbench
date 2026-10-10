"""Provision the causal feature product's private Keeper identities and ACL tree."""
import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import sys
import time

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kazoo.security import make_digest_acl
from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _restrict_secret_file
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from src.market_engine.completed_return_insert_authority import ROOT
from src.market_engine.completed_return_keeper_provision import provision_product_keeper

SECRET_PATH = SECRET_ROOT / 'completed_return_product_keeper.env'
RUNTIME_ROOT = Path('D:/TradingML/runtimes')
KEYS = {'PRODUCT_KEEPER_PRODUCER_USER', 'PRODUCT_KEEPER_PRODUCER_PASSWORD',
        'PRODUCT_KEEPER_CUSTODIAN_USER', 'PRODUCT_KEEPER_CUSTODIAN_PASSWORD'}
IDENTITIES = {'PRODUCT_KEEPER_PRODUCER_USER': 'completed_return_product_producer',
              'PRODUCT_KEEPER_CUSTODIAN_USER': 'completed_return_product_custodian'}


def credentials(path, *, root_exists, restrict=_restrict_secret_file):
    if path.exists():
        restrict(path)
        lines = path.read_text(encoding='utf-8').splitlines()
        if any('=' not in line for line in lines):
            raise RuntimeError('Private Keeper identity is malformed')
        values = dict(line.split('=', 1) for line in lines)
        if len(lines) != 4 or set(values) != KEYS or any(values[k] != v for k,v in IDENTITIES.items()):
            raise RuntimeError('Private Keeper identity differs')
        if any(len(values[k]) < 40 for k in KEYS if k.endswith('_PASSWORD')):
            raise RuntimeError('Private Keeper credential is incomplete')
        return values
    if root_exists:
        raise RuntimeError('Existing product root lacks its private identities; no rotation')
    values = {**IDENTITIES, 'PRODUCT_KEEPER_PRODUCER_PASSWORD': secrets.token_urlsafe(48),
              'PRODUCT_KEEPER_CUSTODIAN_PASSWORD': secrets.token_urlsafe(48)}
    with path.open('x', encoding='utf-8'):
        pass
    restrict(path)
    path.write_text(''.join(f'{k}={values[k]}\n' for k in sorted(values)), encoding='utf-8')
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args(argv)
    if not args.apply:
        print('Dry run: four product-tree nodes; exact private digest ACLs. No connections or writes.')
        return 0
    if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
        parser.error('Apply requires the managed workstation and existing private secrets root')
    if (args.receipt is None or not args.receipt.resolve().is_relative_to(RUNTIME_ROOT.resolve())
            or args.receipt.exists() or not args.receipt.parent.is_dir()):
        parser.error('Apply requires a new receipt under the existing runtime root')
    start = time.monotonic()
    session = None
    result = dict(schema='completed-return-keeper-provision@1', status='running',
                  product_root=ROOT, clickhouse_writes=0, market_content_reads=0,
                  validation_exposure=0, automatic_retry=False)
    stage = 'connect'
    try:
        session = open_workstation_keeper_session(timeout_seconds=5)
        stage = 'private_identity'
        values = credentials(SECRET_PATH, root_exists=session.client.exists(ROOT) is not None)
        producer = make_digest_acl(values['PRODUCT_KEEPER_PRODUCER_USER'],
                                   values['PRODUCT_KEEPER_PRODUCER_PASSWORD'], all=True)
        custodian = make_digest_acl(values['PRODUCT_KEEPER_CUSTODIAN_USER'],
                                    values['PRODUCT_KEEPER_CUSTODIAN_PASSWORD'], all=True)
        session.client.add_auth('digest', values['PRODUCT_KEEPER_CUSTODIAN_USER'] + ':' +
                                values['PRODUCT_KEEPER_CUSTODIAN_PASSWORD'])
        stage = 'acl_tree'
        def progress(row):
            result['last_progress'] = row
            print(json.dumps({'stage': stage, **row}), flush=True)
        result['units'] = provision_product_keeper(session, producer_acl=producer,
                                                  custodian_acl=custodian, progress=progress)
        stage = 'producer_authentication'
        # Verify the actual producer through an independent managed session.
        from src.market_engine.completed_return_keeper_bootstrap import open_scoped_product_keeper
        with open_scoped_product_keeper(username=values['PRODUCT_KEEPER_PRODUCER_USER'],
                password=values['PRODUCT_KEEPER_PRODUCER_PASSWORD'],
                custodian_digest_id=custodian.id.id) as adapter:
            assert adapter.connected
        result.update(status='provisioned_and_producer_authenticated', exact_acls_verified=True)
    except Exception as exc:
        result.update(status='failed_requires_reconciliation', stage=stage,
                      error_type=type(exc).__name__)
    finally:
        if session is not None:
            session.close()
        result['elapsed_seconds'] = time.monotonic() - start
        with args.receipt.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps(result), flush=True)
    return 0 if result['status'] == 'provisioned_and_producer_authenticated' else 1


if __name__ == '__main__':
    raise SystemExit(main())
