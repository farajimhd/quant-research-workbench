"""Journaled copy, SHA-256 verification, and optional path-preserving retirement.

Backup never removes sources. Finalize requires an explicit writer-quiesced flag,
rescans all sources, and keeps D paths as junctions to verified G trees. A failed
copy/verification prevents cutover; a failed retirement retains the old directory.
"""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat
import time


RUNTIME = Path('D:/TradingML/runtimes')
SOURCES = (
    Path('D:/TradingML/runtimes/vectorized_backtest'),
    Path('D:/TradingML/runtimes/level-book-v7'),
    Path('D:/TradingML/runtimes/fundamentals-qualified-20261023'),
    Path('D:/market-data'),
)


def native(p):
    value = os.path.abspath(p)
    if os.name != 'nt' or value.startswith('\\\\?\\'):
        return value
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


def write_json(path, value):
    temporary = Path(str(path) + '.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def fingerprint(path, info=None):
    s = info if info is not None else os.stat(native(path), follow_symlinks=False)
    if getattr(s, 'st_file_attributes', 0) & 0x400 or stat.S_ISLNK(s.st_mode):
        return 'link', 0, 0, os.readlink(native(path))
    if stat.S_ISDIR(s.st_mode):
        return 'dir', 0, 0, ''
    if not stat.S_ISREG(s.st_mode):
        raise ValueError(f'Unsupported file type: {path}')
    return 'file', s.st_size, s.st_mtime_ns, ''


def hash_file(path):
    h = hashlib.sha256()
    with open(native(path), 'rb') as stream:
        for data in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def junction(source, destination):
    import _winapi
    _winapi.CreateJunction(str(destination), str(source))


class Relocation:
    def __init__(self, output, archive, sources=SOURCES, workers=4, keep_containers=()):
        self.output, self.archive = Path(output), Path(archive)
        self.output.mkdir(parents=True, exist_ok=True)
        if not (self.output/'state.json').exists() and self.archive.exists() and any(self.archive.iterdir()):
            raise ValueError('Refusing to overwrite a pre-existing nonempty archive without its journal')
        if self.archive.exists() and self.archive.resolve()!=self.archive:
            raise ValueError('Archive root is redirected')
        self.sources = tuple(Path(p) for p in sources)
        self.keep_containers = tuple(keep_containers)
        if any(i not in range(len(self.sources)) for i in self.keep_containers):
            raise ValueError('Invalid retained container index')
        self.workers = workers
        self.db = sqlite3.connect(self.output/'journal.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS entries(root INTEGER,rel TEXT,kind TEXT,size INTEGER,mtime INTEGER,target TEXT,status TEXT,digest TEXT,seen TEXT,PRIMARY KEY(root,rel)) WITHOUT ROWID')
        self.db.execute('CREATE INDEX IF NOT EXISTS queue ON entries(root,status)')
        self.state_path = self.output/'state.json'
        expected = dict(sources=[str(p) for p in self.sources], archive=str(self.archive))
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else dict(**expected, roots={})
        if any(self.state[k] != v for k, v in expected.items()):
            raise ValueError('Journal is bound to different source/archive roots')
        if 'keep_containers' in self.state and self.state['keep_containers']!=list(self.keep_containers):
            raise ValueError('Journal has a different path-compatibility policy')
        if 'keep_containers' not in self.state and any(v.get('phase') in ('cutover_intent','cutover','complete') for v in self.state['roots'].values()):
            raise ValueError('Cannot adopt a new path policy after an older cutover')
        self.state['keep_containers']=list(self.keep_containers)
        self.save()
        self.started = time.time()
        self.last = 0
        self.stage = 'starting'
        self.active = ''
        self.failures = []
        self.completed = 0
        self.verified_bytes = 0
        self.queue_total = 0
        for index in range(len(self.sources)):
            state = self.state['roots'].get(str(index), {})
            if state.get('phase') == 'cutover_intent':
                source, old = self.sources[index], self.retiring(index)
                if index in self.keep_containers and old.is_dir() and source.is_dir():
                    # Roll back only links created by this cutover. Unknown or new
                    # files are preserved and prevent automatic reconciliation.
                    for p in source.iterdir():
                        if fingerprint(p)[0]!='link' or p.resolve()!=(self.destination(index)/p.name).resolve():
                            raise RuntimeError('Unexpected entry in interrupted container cutover')
                        if os.stat(native(p),follow_symlinks=False).st_file_attributes&0x10:os.rmdir(native(p))
                        else:os.unlink(native(p))
                    os.rmdir(native(source));os.rename(native(old),native(source));state['phase']='backed_up'
                elif source.is_dir() and source.resolve() == self.destination(index).resolve() and old.is_dir():
                    state['phase'] = 'cutover'
                elif not os.path.lexists(native(source)) and old.is_dir() and old.resolve() == old:
                    os.rename(native(old), native(source)); state['phase'] = 'backed_up'
                elif source.is_dir() and source.resolve() == source and not old.exists():
                    state['phase'] = 'backed_up'
                else:
                    raise RuntimeError('Ambiguous interrupted cutover; no automatic deletion permitted')
                self.save()

    def destination(self, index):
        return self.archive/f'tree-{index:02d}'/self.sources[index].name

    def retiring(self, index):
        p = self.sources[index]
        return p.with_name(p.name + '.retiring-20261108')

    def install_aliases(self, index):
        source,destination,old=self.sources[index],self.destination(index),self.retiring(index)
        if index not in self.keep_containers:
            junction(source,destination)
            return
        os.mkdir(native(source))
        with os.scandir(native(old)) as entries:
            for entry in entries:
                target=destination/entry.name;alias=source/entry.name
                if entry.is_dir(follow_symlinks=False):junction(alias,target)
                else:os.symlink(str(target),native(alias),target_is_directory=False)

    def aliases_valid(self,index):
        source,destination=self.sources[index],self.destination(index)
        if index not in self.keep_containers:
            return source.resolve()==destination.resolve()
        if source.resolve()!=source:return False
        with os.scandir(native(destination)) as entries:
            return all((source/entry.name).resolve()==(destination/entry.name).resolve() for entry in entries)

    def save(self):
        write_json(self.state_path, self.state)

    def publish(self, force=False):
        now = time.time()
        if not force and now-self.last < 10:
            return
        self.last = now
        value = dict(stage=self.stage, active=self.active, completed_this_stage=self.completed,
                     queued_at_stage_start=self.queue_total, verified_bytes_this_stage=self.verified_bytes,
                     failures=self.failures[-12:], failed=len(self.failures), elapsed_seconds=round(now-self.started,1),
                     updated=now, roots=self.state['roots'])
        write_json(self.output/'progress.json', value)
        print(f"{self.stage} | {self.active} | completed {self.completed:,}/{self.queue_total:,} | verified {self.verified_bytes/2**30:,.2f} GiB | failed {len(self.failures)}", flush=True)

    def inventory(self, index):
        root = self.sources[index]
        if root.resolve() != root or not root.is_dir():
            raise ValueError(f'Source is absent or redirected: {root}')
        target = self.destination(index)
        if target.exists() and target.resolve() != target:
            raise ValueError('Archive target is redirected')
        if self.retiring(index).exists():
            raise ValueError('Unexpected retirement directory before cutover')
        self.stage = 'Inventory'; self.active = root.name; self.completed = 0; self.queue_total = 0
        token = str(time.time_ns())
        stack = [root]
        batch = []
        while stack:
            folder = stack.pop()
            with os.scandir(native(folder)) as entries:
                for entry in entries:
                    path = folder/entry.name
                    kind, size, mtime, link = fingerprint(path, entry.stat(follow_symlinks=False))
                    rel = path.relative_to(root).as_posix()
                    batch.append((index,rel,kind,size,mtime,link,'pending','',token))
                    if kind == 'dir':
                        stack.append(path)
                    if len(batch) >= 2000:
                        self.store(batch); self.completed += len(batch); batch=[]; self.publish()
        self.store(batch); self.completed += len(batch)
        missing = self.db.execute('SELECT rel FROM entries WHERE root=? AND seen<>? LIMIT 1',(index,token)).fetchone()
        if missing:
            raise RuntimeError(f'Source entry disappeared since earlier pass: {missing[0]}')
        self.publish(True)

    def store(self, rows):
        self.db.executemany('''INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(root,rel) DO UPDATE SET
          status=CASE WHEN entries.kind=excluded.kind AND entries.size=excluded.size AND entries.mtime=excluded.mtime AND entries.target=excluded.target THEN entries.status ELSE 'pending' END,
          digest=CASE WHEN entries.kind=excluded.kind AND entries.size=excluded.size AND entries.mtime=excluded.mtime AND entries.target=excluded.target THEN entries.digest ELSE '' END,
          kind=excluded.kind,size=excluded.size,mtime=excluded.mtime,target=excluded.target,seen=excluded.seen''', rows)
        self.db.commit()

    def copy_one(self, row):
        index, rel, kind, size, mtime, target = row
        source, destination = self.sources[index]/rel, self.destination(index)/rel
        if fingerprint(source) != (kind,size,mtime,target):
            raise RuntimeError('Source changed before copy: '+str(source))
        os.makedirs(native(destination.parent), exist_ok=True)
        if kind == 'dir':
            os.makedirs(native(destination), exist_ok=True)
            return ''
        if kind == 'link':
            if os.path.lexists(native(destination)):
                if fingerprint(destination) != ('link',0,0,target):
                    raise RuntimeError('Archive link conflicts: '+str(destination))
            else:
                s = os.stat(native(source),follow_symlinks=False)
                if getattr(s,'st_reparse_tag',0) == 0xA0000003:
                    junction(destination, target.removeprefix('\\\\?\\'))
                else:
                    os.symlink(target,native(destination),target_is_directory=bool(s.st_file_attributes&0x10))
            if fingerprint(destination) != ('link',0,0,target):
                raise RuntimeError('Link readback mismatch: '+str(destination))
            return ''
        part_root = self.archive/'.parts'
        os.makedirs(native(part_root), exist_ok=True)
        temp = part_root/hashlib.sha256(f'{index}/{rel}'.encode()).hexdigest()
        if os.path.lexists(native(destination)) and fingerprint(destination)[0] != 'file':
            raise RuntimeError('Archive entry type conflicts: '+str(destination))
        digest = hashlib.sha256()
        with open(native(source),'rb') as src, open(native(temp),'wb') as dst:
            for data in iter(lambda:src.read(4*1024*1024), b''):
                dst.write(data);digest.update(data)
        digest = digest.hexdigest()
        if fingerprint(source) != (kind,size,mtime,target) or os.stat(native(temp)).st_size != size:
            raise RuntimeError('Source changed during copy: '+str(source))
        if hash_file(temp) != digest:
            raise RuntimeError('Archive SHA-256 readback failed: '+str(destination))
        shutil.copystat(native(source),native(temp),follow_symlinks=False)
        os.replace(native(temp),native(destination))
        return digest

    def copy_pending(self, index):
        self.stage='Copy and verify';self.active=self.sources[index].name;self.completed=0;self.verified_bytes=0
        self.queue_total=self.db.execute("SELECT count(*) FROM entries WHERE root=? AND status<>'verified'",(index,)).fetchone()[0]
        required=self.db.execute("SELECT coalesce(sum(size),0) FROM entries WHERE root=? AND status<>'verified'",(index,)).fetchone()[0]
        if os.name=='nt' and shutil.disk_usage(self.archive.anchor).free < required + 2*1024**3:
            raise RuntimeError('Archive volume has insufficient free space for the pending tree plus reserve')
        self.publish(True)
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            while True:
                rows=self.db.execute("SELECT root,rel,kind,size,mtime,target FROM entries WHERE root=? AND status<>'verified' LIMIT 128",(index,)).fetchall()
                if not rows:break
                futures=[pool.submit(self.copy_one,row) for row in rows]
                for row,future in zip(rows,futures):
                    try:digest=future.result()
                    except Exception as exc:
                        self.failures.append(dict(path=row[1],reason=str(exc)));self.publish(True);raise
                    self.db.execute("UPDATE entries SET status='verified',digest=? WHERE root=? AND rel=?",(digest,index,row[1]))
                    self.completed+=1;self.verified_bytes+=row[3];self.publish()
                self.db.commit()
        self.publish(True)

    def backup(self):
        for index in range(len(self.sources)):
            if self.state['roots'].get(str(index),{}).get('phase')=='complete':continue
            self.inventory(index);self.copy_pending(index)
            self.state['roots'][str(index)]=dict(phase='backed_up',source=str(self.sources[index]),destination=str(self.destination(index)))
            self.save()
        self.stage='Backup verified; awaiting writer quiescence';self.active='all four trees';self.publish(True)

    def check_entry(self, index, root, row, check_hash=False):
        rel,kind,size,mtime,target,digest=row
        path=root/rel
        if fingerprint(path)!=(kind,size,mtime,target):
            raise RuntimeError('Retained source or archive metadata changed: '+str(path))
        if check_hash and kind=='file' and hash_file(path)!=digest:
            raise RuntimeError('Archive hash changed: '+str(path))

    def finalize(self):
        # The owning operator must stop writers before this stage. Backup performs
        # a fresh full inventory and re-verifies every new or changed source entry.
        for index in range(len(self.sources)):
            phase=self.state['roots'].get(str(index),{}).get('phase')
            if phase in ('cutover','complete'):continue
            self.inventory(index);self.copy_pending(index)
        for index,source in enumerate(self.sources):
            state=self.state['roots'].setdefault(str(index),{})
            if state.get('phase')=='complete':continue
            destination,old=self.destination(index),self.retiring(index)
            self.stage='Validate and retire';self.active=source.name;self.completed=0;self.queue_total=self.db.execute('SELECT count(*) FROM entries WHERE root=?',(index,)).fetchone()[0]
            if state.get('phase')!='cutover':
                for row in self.db.execute('SELECT rel,kind,size,mtime,target,digest FROM entries WHERE root=?',(index,)):
                    self.check_entry(index,source,row);self.check_entry(index,destination,row,check_hash=True)
                    self.completed+=1;self.publish()
                # Persist intent before the rename, so interruption is recoverable.
                state.update(phase='cutover_intent',source=str(source),destination=str(destination),retiring=str(old));self.save()
                os.rename(native(source),native(old))
                try:self.install_aliases(index)
                except Exception:
                    # Leave cutover_intent intact if a partially installed
                    # container needs link-by-link recovery on restart.
                    if not os.path.lexists(native(source)):
                        os.rename(native(old),native(source));state['phase']='backed_up';self.save()
                    raise
                state['phase']='cutover';self.save()
            if not self.aliases_valid(index) or not old.is_dir() or old.resolve()!=old:
                raise RuntimeError('Cutover paths do not match the sealed plan')
            self.stage='Remove verified D copy';self.completed=0;self.publish(True)
            for row in self.db.execute("SELECT rel,kind,size,mtime,target,digest FROM entries WHERE root=? AND kind<>'dir'",(index,)):
                rel,kind,*_=row;path=old/rel
                if not os.path.lexists(native(path)):continue
                self.check_entry(index,old,row)
                self.check_entry(index,destination,row)
                if kind=='link' and os.stat(native(path),follow_symlinks=False).st_file_attributes&0x10:
                    os.rmdir(native(path))
                else:
                    if os.name=='nt' and not os.stat(native(path),follow_symlinks=False).st_mode&stat.S_IWRITE:
                        os.chmod(native(path),stat.S_IWRITE)
                    os.unlink(native(path))
                self.completed+=1;self.publish()
            # Remove only empty real directories, never traverse a reparse point.
            stack=[(old,False)]
            while stack:
                folder,visited=stack.pop()
                if visited:os.rmdir(native(folder));continue
                stack.append((folder,True))
                with os.scandir(native(folder)) as entries:
                    for entry in entries:
                        p=folder/entry.name
                        if fingerprint(p)[0]!='dir':raise RuntimeError('Unexpected retained entry; refusing recursive deletion: '+str(p))
                        stack.append((p,False))
            state['phase']='complete';self.save();self.publish(True)
        self.stage='Complete';self.active='four G trees verified; D payloads retired with compatible paths';self.publish(True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--stage',choices=['backup','finalize'],default='backup')
    parser.add_argument('--writers-quiesced',action='store_true')
    parser.add_argument('--exclude-backtests',action='store_true',help='Use a separate journal/archive for only level books, fundamentals and market data')
    parser.add_argument('--exclude-level-book',action='store_true',help='Exclude level-book data from this separately journaled archive')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if os.name!='nt':parser.error('Workstation Windows only')
    if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':parser.error('Run on DESKTOP-SAAI85T workstation only')
    if not 1<=args.workers<=8:parser.error('Workers must be 1..8')
    if not args.output.resolve().is_relative_to(RUNTIME) or args.output.resolve()==RUNTIME:parser.error('Journal must be below workstation runtime root')
    if args.archive.drive.upper()!='G:' or not args.archive.resolve().is_relative_to(Path('G:/TradingML/backups')):parser.error('Archive must be below G:/TradingML/backups')
    if args.stage=='finalize' and not args.writers_quiesced:parser.error('Finalize requires --writers-quiesced')
    args.output.mkdir(parents=True,exist_ok=True)
    import msvcrt
    with (args.output/'controller.lock').open('a+b') as lock:
        lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        sources=tuple(p for i,p in enumerate(SOURCES) if not (i==0 and args.exclude_backtests or i==1 and args.exclude_level_book))
        job=Relocation(args.output,args.archive,sources=sources,workers=args.workers,keep_containers=() if args.exclude_backtests else (0,))
        try:
            if args.stage=='backup':job.backup()
            else:job.finalize()
        except (Exception,KeyboardInterrupt) as exc:
            job.stage='Stopped safely; sources retained unless already verified and retired'
            job.failures.append(dict(reason=str(exc)));job.publish(True);raise
        finally:job.db.close()


if __name__=='__main__':main()
