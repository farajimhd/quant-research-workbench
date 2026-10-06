"""Prepare a bounded canonical archive consolidation manifest without DB access."""
from __future__ import annotations
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import argparse
from dataclasses import fields
import json

from research.level_book.v7.canonical_archive_publication import (
    ArchiveRequest, prepare_archive_plan, VERSION,
)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root',type=Path,required=True,
                        help='Existing published V7 catalog root; no raw market files')
    parser.add_argument('--requests',type=Path,required=True,
                        help='Explicit ordered pre-certified scope references; no implied DB approval')
    parser.add_argument('--output',type=Path,required=True,
                        help='New immutable manifest beneath operational runtimes')
    parser.add_argument('--apply',action='store_true',help='Publish only after independent review/source/storage certification')
    parser.add_argument('--review',type=Path,help='Hash-pinned external apply review; required for apply')
    parser.add_argument('--review-sha256',help='Exact reviewed file SHA256; required for apply')
    parser.add_argument('--exclusions',type=Path,help='Bound dated runtime exclusion file; required for apply')
    args=parser.parse_args(argv)
    if args.apply and not all((args.review,args.review_sha256,args.exclusions)):
        parser.error('Apply requires --review, --review-sha256 and --exclusions; no database access performed')
    try:
        from src.runtime_paths import runtime_root, WORKSTATION_RUNTIME_ROOT
        destination=args.output.resolve()
        allowed=(runtime_root().resolve(),WORKSTATION_RUNTIME_ROOT.resolve())
        if not any(destination.is_relative_to(root) for root in allowed):
            raise ValueError('Output must remain beneath an operational runtime root')
        if destination.exists():
            raise ValueError('Output already exists; retain it and select a new manifest filename')
        data=json.loads(args.requests.read_text(encoding='utf-8-sig'))
        if type(data) is not dict or set(data)!={'schema','requests'} or data['schema']!='canonical-v7-archive-requests@1':
            raise ValueError('Request file schema differs')
        expected={field.name for field in fields(ArchiveRequest)}
        raw=data['requests']
        if type(raw) is not list or not raw or any(type(row) is not dict or set(row)!=expected for row in raw):
            raise ValueError('Request fields differ or inventory empty')
        requests=tuple(ArchiveRequest(**row) for row in raw)
        print(f'{"Reviewed apply" if args.apply else "Plan only"} | queued {len(requests)} dated archive requests',flush=True)
        plan=prepare_archive_plan(args.archive_root,requests)
        payload={'schema':VERSION,'consolidation_hash':plan.token,'plan':plan.payload(),
                 'authority':'Prepared archive equivalence only; upstream market/source and database admission not installed'}
        if args.apply:
            from research.level_book.v7.canonical_archive_clickhouse import apply_archive_plan
            payload['publication_commit']=apply_archive_plan(args.archive_root,plan,
                review_path=args.review,review_hash=args.review_sha256,exclusion_path=args.exclusions)
            payload['authority']='Committed producer inventory; not financial admission authority'
        destination.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive creation prevents accidental overwrite of an earlier frozen plan.
        with destination.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(payload,indent=2,allow_nan=False)+'\n')
        print(f'Verified {len(plan.members)} dated members | failed 0 | {plan.token}',flush=True)
        print(f'Manifest: {destination}',flush=True)
        print('Publication verified; financial admission remains separate.' if args.apply else
              'Plan only; no publication performed and no database access.',flush=True)
        return 0
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:
        # Apply transport errors can include private endpoint/server text. Never
        # print that text or its digest; retain only the bounded failure class.
        detail=f'Apply blocked ({type(exc).__name__}); no automatic retry' if args.apply else f'Plan blocked: {str(exc)[:400]}'
        print(detail,file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
