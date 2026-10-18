"""Keep noninteractive Windows compiler helpers out of the desktop."""
import os,subprocess
from pathlib import Path
COMPILERS={'ptxas.exe','cl.exe','link.exe','ninja.exe','nvcc.exe','gcc.exe','g++.exe','tcc.exe'}

def quiet_compilers():
    if os.name!='nt' or getattr(subprocess.Popen,'_v30_quiet',False):return
    original=subprocess.Popen
    class CompilerProcess(original):
        _v30_quiet=True
        def __init__(self,args,*a,**kw):
            executable=kw.get('executable') or (args[0] if isinstance(args,(list,tuple)) and args else '')
            if Path(str(executable)).name.lower() in COMPILERS:
                flags=kw.get('creationflags',0)
                if not flags&(subprocess.CREATE_NEW_CONSOLE|subprocess.DETACHED_PROCESS):kw['creationflags']=flags|subprocess.CREATE_NO_WINDOW
            super().__init__(args,*a,**kw)
            audit=os.environ.get('V30_COMPILER_AUDIT')
            if audit and Path(str(executable)).name.lower() in COMPILERS:
                import json
                target=Path(audit);target.mkdir(parents=True,exist_ok=True)
                log=target/('compiler-'+str(os.getpid())+'.jsonl')
                if not log.exists() or log.stat().st_size<8192:
                    with log.open('a') as stream:stream.write(json.dumps(dict(compiler=Path(str(executable)).name,pid=self.pid,creationflags=kw.get('creationflags',0),no_window=bool(kw.get('creationflags',0)&subprocess.CREATE_NO_WINDOW)))+'\n')
    subprocess.Popen=CompilerProcess
