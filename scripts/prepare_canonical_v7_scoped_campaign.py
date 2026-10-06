"""Export or preflight a certified development-only canonical prefix campaign.

No producer is launched by this command. Reviewed exports use the unchanged
filtered campaign runner after this fresh scope preflight succeeds.
"""
import os
import sys
from pathlib import Path
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
from research.level_book.v7.canonical_scoped_campaign import export_manifest, verify_export


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('plan', 'preflight'), nargs='?', default='plan')
    parser.add_argument('--archive-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--development-scope', type=Path, required=True)
    parser.add_argument('--development-scope-sha256', required=True)
    parser.add_argument('--exclusions', type=Path, required=True)
    parser.add_argument('--canary-ticker', help='Explicit retained ticker; full union parent inventory remains pinned')
    args = parser.parse_args(argv)
    try:
        from src.runtime_paths import runtime_root, WORKSTATION_RUNTIME_ROOT
        destination = args.output.resolve()
        allowed = (runtime_root().resolve(), WORKSTATION_RUNTIME_ROOT.resolve())
        if (destination.is_relative_to(Path(__file__).resolve().parents[1])
                or not any(root.is_dir() and destination.is_relative_to(root) for root in allowed)):
            raise ValueError('Output must remain beneath an available operational runtime root')
        if (args.output.resolve().parent != (args.archive_root/'filtered-preparation-campaigns').resolve()
                or args.output.name in ('.', '..')):
            raise ValueError('Output must be one named campaign under the operational archive root')
        operation = export_manifest if args.command == 'plan' else verify_export
        plan = operation(args.archive_root, args.output, args.development_scope,
                         args.development_scope_sha256, args.exclusions,
                         canary_ticker=args.canary_ticker)
        proof = plan['scoped_authority']
        print(f"{args.command}: verified | {len(proof['retained_parent_inventory'])} retained parents | "
              f"{len(plan['rows'])} queued units | {sum(r['sessions'] for r in plan['rows'])} source sessions")
        print(f"Manifest {plan['manifest_hash']} | {args.output / 'plan.json'}")
        print('No producer launched. Checkpoints and financial readiness remain pending.')
        return 0
    except (ValueError, OSError) as exc:
        # SQL/transport exceptions may contain private connection detail.
        print(f'Blocked: {type(exc).__name__}. No producer launched.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
