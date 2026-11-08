"""Manifest-based cleanup of explicitly approved V2..V62 output files only."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import time

RUNTIME=Path('D:/TradingML/runtimes')
DATA_FILES={'source.json','training.json','validation.json','market.parquet','history.parquet','features.parquet','candles.parquet','bars.parquet'}


def native_path(path):
    value=os.fspath(path)
    if os.name!='nt' or value.startswith('\\\\?\\'):return value
    if value.startswith('\\\\'):return '\\\\?\\UNC\\'+value[2:]
    return '\\\\?\\'+str(Path(value).absolute())


def write_json(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2),encoding='utf-8');os.replace(temporary,path)


class Cleanup:
    def __init__(self,proposal,output,base,expected_sha):
        self.proposal=Path(proposal);self.output=Path(output).resolve();self.base=Path(base).resolve(strict=True)
        if not self.base.is_relative_to(RUNTIME.resolve()) or not self.output.is_relative_to(RUNTIME.resolve()):raise ValueError('Runtime paths required')
        raw=self.proposal.read_bytes();self.sha=hashlib.sha256(raw).hexdigest()
        if self.sha!=expected_sha:raise ValueError('Proposal hash differs from approved snapshot')
        self.config=json.loads(raw);self.roots={}
        for item in self.config['first_pass']['roots']:
            root=Path(item['path']).resolve();match=re.fullmatch('torch_backtest_v([0-9]+)',root.name)
            if root.parent!=self.base or not match or not 2<=int(match[1])<=62:raise ValueError('Root outside approved retired version range')
            if root.name in self.roots:raise ValueError('Duplicate cleanup root')
            self.roots[root.name]=root
        self.output.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.output/'files.sqlite');self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY,path TEXT UNIQUE,size INTEGER,mtime INTEGER,status TEXT DEFAULT \'pending\',reason TEXT)')
        self.db.execute('CREATE INDEX IF NOT EXISTS files_queue ON files(status,id)')
        self.db.execute('CREATE TABLE IF NOT EXISTS kept (path TEXT PRIMARY KEY,kind TEXT,size INTEGER,mtime INTEGER)')
        self.meta_path=self.output/'plan.json';self.progress_path=self.output/'progress.json'
        if self.meta_path.exists():
            self.meta=json.loads(self.meta_path.read_bytes())
            if self.meta['proposal_sha256']!=self.sha or self.meta['base']!=str(self.base):raise ValueError('Cleanup journal belongs to another plan')
        else:self.meta=dict(proposal_sha256=self.sha,base=str(self.base),phase='new')
        self.counts=Counter();self.sizes=Counter()
        for status,n,b in self.db.execute('SELECT status,count(*),sum(size) FROM files GROUP BY status'):
            self.counts[status]=n;self.sizes[status]=b or 0
        self.started=time.time();self.last_publish=0

    def protected(self,path):
        relative=path.relative_to(self.base)
        names=set(self.config['excluded_directory_names']);prefixes=tuple(self.config['excluded_directory_prefixes'])
        if any(p.lower() in names or p.lower().startswith(prefixes) for p in relative.parts[1:-1]):return True
        return path.suffix.lower() in self.config['preserved_file_extensions'] or path.name.lower() in DATA_FILES or path.name.lower().startswith(tuple(self.config['excluded_file_prefixes']))

    def guard(self,path):
        relative=path.relative_to(self.base)
        if not relative.parts or relative.parts[0] not in self.roots or self.protected(path):raise ValueError('File outside deletable output scope')
        root=self.roots[relative.parts[0]]
        if path.parent.resolve()!=path.parent or not path.parent.is_relative_to(root):raise ValueError('Parent path was redirected')

    def active_users(self):
        import psutil
        found=[]
        for proc in psutil.process_iter(['pid','cmdline']):
            if proc.pid==os.getpid():continue
            try:
                args=proc.info['cmdline'] or []
                for arg in args:
                    value=arg.replace('\\','/').lower()
                    if any(str(root).replace('\\','/').lower() in value for root in self.roots.values()):
                        found.append(proc.pid);break
            except (psutil.AccessDenied,psutil.NoSuchProcess):continue
        if found:raise RuntimeError('Approved retired roots are referenced by live processes: '+str(found))

    def publish(self,stage,force=False):
        now=time.time()
        if not force and now-self.last_publish<10:return
        self.db.commit();counts=dict(self.counts);sizes=dict(self.sizes)
        report=dict(stage=stage,counts=counts,bytes=sizes,kept=self.db.execute('SELECT count(*) FROM kept').fetchone()[0],elapsed_seconds=now-self.started,updated_epoch=now)
        write_json(self.progress_path,report)
        print(f"{stage}: deleted {counts.get('deleted',0):,} files / {sizes.get('deleted',0)/2**30:,.2f} GiB | queued {counts.get('pending',0):,} | retained {report['kept']:,} | changed {counts.get('changed',0):,} | failed {counts.get('failed',0):,}",flush=True)
        self.last_publish=now

    def plan(self):
        if self.meta['phase'] in ('planned','deleting','complete','complete_with_errors'):return
        self.active_users();self.db.execute('DELETE FROM files');self.db.execute('DELETE FROM kept')
        self.counts.clear();self.sizes.clear()
        names=set(self.config['excluded_directory_names']);prefixes=tuple(self.config['excluded_directory_prefixes'])
        for root in self.roots.values():
            if not root.is_dir():continue
            stack=[root]
            while stack:
                folder=stack.pop()
                if folder.resolve()!=folder:raise ValueError('Directory redirection during planning')
                with os.scandir(native_path(folder)) as entries:
                    for entry in entries:
                        path=folder/entry.name;s=entry.stat(follow_symlinks=False);attrs=getattr(s,'st_file_attributes',0)
                        if attrs&1024 or entry.is_symlink():kind='linked path'
                        elif entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() in names or entry.name.lower().startswith(prefixes):kind='protected directory'
                            else:stack.append(path);continue
                        elif self.protected(path) or s.st_nlink>1:kind='input or linked file'
                        elif stat.S_ISREG(s.st_mode):
                            self.db.execute('INSERT INTO files(path,size,mtime) VALUES (?,?,?)',(str(path),s.st_size,s.st_mtime_ns))
                            self.counts['pending']+=1;self.sizes['pending']+=s.st_size;continue
                        else:kind='nonregular file'
                        self.db.execute('INSERT INTO kept VALUES (?,?,?,?)',(str(path),kind,s.st_size,s.st_mtime_ns))
                self.publish('Enumerating approved outputs')
        self.db.commit();self.meta['phase']='planned';write_json(self.meta_path,self.meta);self.publish('Plan sealed',True)

    def execute(self):
        import psutil
        if self.meta['phase'] not in ('planned','deleting','complete','complete_with_errors'):raise ValueError('Seal the file plan before deletion')
        self.active_users()
        if 'disk_free_before' not in self.meta:self.meta['disk_free_before']=psutil.disk_usage(self.base.anchor).free
        self.meta['phase']='deleting';write_json(self.meta_path,self.meta);last_guard=0
        # Older Windows calls can report long cache paths as absent. Revisit
        # those exact manifest entries with the extended API; never re-enumerate
        # new files into an already sealed deletion plan.
        for identity,name,size in self.db.execute("SELECT id,path,size FROM files WHERE status='absent'").fetchall():
            if os.path.lexists(native_path(name)):
                self.db.execute("UPDATE files SET status='pending',reason=NULL WHERE id=?",(identity,))
                self.counts['absent']-=1;self.sizes['absent']-=size;self.counts['pending']+=1;self.sizes['pending']+=size
        if self.counts['failed']:
            self.meta['retried_files']=self.meta.get('retried_files',0)+self.counts['failed']
            self.db.execute("UPDATE files SET status='pending',reason=NULL WHERE status='failed'")
            self.counts['pending']+=self.counts['failed'];self.sizes['pending']+=self.sizes['failed'];self.counts['failed']=0;self.sizes['failed']=0
        while True:
            rows=self.db.execute("SELECT id,path,size,mtime FROM files WHERE status='pending' ORDER BY id LIMIT 2000").fetchall()
            if not rows:break
            if (self.output/'STOP').exists():self.publish('Stopped; safe to resume',True);return
            if time.time()-last_guard>30:self.active_users();last_guard=time.time()
            checked=set()
            for identity,name,size,mtime in rows:
                path=Path(name);outcome='deleted';reason=None
                try:
                    if path.parent not in checked:self.guard(path);checked.add(path.parent)
                    elif self.protected(path):raise ValueError('Protected file in delete manifest')
                    s=os.stat(native_path(path),follow_symlinks=False)
                    if not stat.S_ISREG(s.st_mode) or getattr(s,'st_file_attributes',0)&1024 or s.st_nlink>1:outcome='changed';reason='Type, link or attribute changed'
                    elif (s.st_size,s.st_mtime_ns)!=(size,mtime):outcome='changed';reason='File differs from sealed plan'
                    else:
                        if getattr(s,'st_file_attributes',0)&1:os.chmod(native_path(path),stat.S_IWRITE)
                        os.unlink(native_path(path))
                except FileNotFoundError:outcome='absent';reason='Already absent at execution'
                except OSError as e:outcome='failed';reason=str(e)
                self.db.execute('UPDATE files SET status=?,reason=? WHERE id=?',(outcome,reason,identity))
                self.counts['pending']-=1;self.sizes['pending']-=size;self.counts[outcome]+=1;self.sizes[outcome]+=size
            self.db.commit();self.publish('Deleting approved outputs')
        changed=[]
        for name,kind,size,mtime in self.db.execute('SELECT * FROM kept'):
            path=Path(name)
            if not os.path.lexists(native_path(path)):changed.append(dict(path=name,reason='Protected path missing'))
            elif kind=='input or linked file':
                s=os.stat(native_path(path),follow_symlinks=False)
                if (s.st_size,s.st_mtime_ns)!=(size,mtime):changed.append(dict(path=name,reason='Protected file metadata changed'))
        self.meta.update(phase='complete_with_errors' if self.counts['failed'] or changed else 'complete',disk_free_after=psutil.disk_usage(self.base.anchor).free,protected_changes=changed)
        write_json(self.meta_path,self.meta);self.publish('Complete' if self.meta['phase']=='complete' else 'Complete with errors; inspect retained files',True)
        if changed:raise RuntimeError('Protected paths changed; inspect plan.json')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal',type=Path,required=True);parser.add_argument('--proposal-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--execute',action='store_true')
    parser.add_argument('--base',type=Path,default=RUNTIME/'vectorized_backtest')
    args=parser.parse_args()
    if not args.output.resolve().is_relative_to(RUNTIME.resolve()):parser.error('Output must remain under the runtime root')
    args.output.mkdir(parents=True,exist_ok=True)
    import msvcrt
    with (args.output/'controller.lock').open('a+b') as lock:
        lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        job=Cleanup(args.proposal,args.output,args.base,args.proposal_sha256)
        try:
            job.plan()
            if args.execute:job.execute()
        except KeyboardInterrupt:job.publish('Interrupted; safe to resume',True);raise
        finally:job.db.close()


if __name__=='__main__':main()
