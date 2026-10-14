#!/usr/bin/env python3
"""Plan/check/apply one narrowly scoped, readonly SEC runtime bind mount."""
import argparse,json,os,re,subprocess,sys
from pathlib import Path,PurePosixPath

os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
RUNTIME=PurePosixPath('/mnt/d/TradingML/runtimes')
FILES=PurePosixPath('/mnt/d/market-data')
DEFAULT_SOURCE=RUNTIME/'certified-research/sec'
DEFAULT_ALIAS=FILES/'certified-sec-runtime'


def validate(source,alias):
    source=PurePosixPath(source);alias=PurePosixPath(alias)
    if ('..' in source.parts or RUNTIME not in source.parents or source.name!='sec'
            or '..' in alias.parts or alias.parent!=FILES
            or not re.fullmatch(r'certified-sec-runtime(?:-[a-z0-9-]{1,32})?',alias.name)):
        raise ValueError('Mount scope must be a SEC subfolder in the operational runtime tree and a dedicated certified-sec-runtime alias')
    return source,alias


def plan(source,alias):
    source,alias=validate(source,alias)
    return dict(source=str(source),alias=str(alias),readonly=True,server_restart=False,
        commands=[['mount','--bind',str(source),str(alias)],
            ['mount','-o','remount,bind,ro',str(alias)]])


def run(command):
    return subprocess.run(command,capture_output=True,text=True,timeout=15,check=False)


def mounted(alias):
    result=run(['findmnt','--mountpoint',str(alias),'--json','--output','TARGET,VFS-OPTIONS'])
    if result.returncode==1:return None
    if result.returncode:raise RuntimeError('Cannot inspect runtime alias mount')
    rows=json.loads(result.stdout).get('filesystems',[])
    if len(rows)!=1 or rows[0]['target']!=str(alias):raise ValueError('Runtime alias mount metadata is ambiguous')
    return rows[0]


def same_directory(source,alias):
    left=source.stat();right=alias.stat()
    return (left.st_dev,left.st_ino)==(right.st_dev,right.st_ino)


def operate(source,alias,*,apply=False):
    source,alias=validate(source,alias);source=Path(source);alias=Path(alias)
    if os.name!='posix':raise ValueError('Mount check/apply must run on the Linux ClickHouse host')
    if apply and os.geteuid()!=0:raise ValueError('Mount apply requires root on the ClickHouse host')
    for base in (Path(RUNTIME),Path(FILES)):
        if not base.is_dir() or base.resolve()!=base:raise ValueError('Required D runtime/file authority is absent or redirected')
    if source.resolve()!=source or alias.resolve()!=alias:raise ValueError('Runtime source/alias must not redirect through symlinks')
    ancestor=source
    while not ancestor.exists():ancestor=ancestor.parent
    if ancestor.stat().st_dev!=Path(RUNTIME).stat().st_dev:
        raise ValueError('SEC runtime is on a different filesystem')
    state=mounted(alias)
    if state is not None:
        if not source.is_dir() or not same_directory(source,alias):
            raise ValueError('Existing alias belongs to another directory; it will not be replaced or unmounted')
    elif alias.exists() and (not alias.is_dir() or any(alias.iterdir())):
        raise ValueError('Unmounted alias is nonempty or not a directory; it will not be replaced')
    if apply:
        source.mkdir(parents=True,exist_ok=True);alias.mkdir(exist_ok=True)
        if source.stat().st_dev!=Path(RUNTIME).stat().st_dev:raise ValueError('SEC runtime is on a different filesystem')
        commands=plan(str(source),str(alias))['commands']
        for command in (commands if state is None else commands[1:]):
            result=run(command)
            if result.returncode:raise RuntimeError('Runtime mount operation failed; inspect the existing alias before retry')
        state=mounted(alias)
    if state is None or not same_directory(source,alias) or 'ro' not in state['vfs-options'].split(','):
        raise ValueError('SEC runtime alias is not the expected readonly bind mount')
    for flag in ('-r','-x'):
        if run(['sudo','-u','clickhouse','test',flag,str(alias)]).returncode:
            raise ValueError('ClickHouse cannot read/search the runtime alias; existing permissions were preserved')
    return dict(status='verified_mount',source=str(source),alias=str(alias),readonly=True,
        note='The launcher still requires exact CH nonce readback before acquisition')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('plan','check','apply'),nargs='?',default='plan')
    parser.add_argument('--source',default=str(DEFAULT_SOURCE));parser.add_argument('--alias',default=str(DEFAULT_ALIAS))
    args=parser.parse_args(argv)
    try:
        result=plan(args.source,args.alias) if args.command=='plan' else operate(args.source,args.alias,apply=args.command=='apply')
        print(json.dumps(result,indent=2));return 0
    except (ValueError,RuntimeError,OSError) as error:
        print(str(error),file=sys.stderr);return 2


if __name__=='__main__':raise SystemExit(main())
