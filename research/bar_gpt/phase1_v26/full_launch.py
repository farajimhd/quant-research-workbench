"""Logging entry point for an independently launched workstation process."""
import os,sys,argparse
from pathlib import Path
os.environ.update(PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')


def main():
    p=argparse.ArgumentParser(add_help=False);p.add_argument('--console-log',type=Path,required=True);a,rest=p.parse_known_args()
    path=a.console_log.resolve();root=Path('D:/TradingML/runtimes').resolve()
    if not path.is_relative_to(root) or not path.parent.is_dir():raise ValueError('Existing workstation runtime log directory required')
    with path.open('a',encoding='utf-8',buffering=1) as stream:
        os.dup2(stream.fileno(),1);os.dup2(stream.fileno(),2)
        # A WMI-created process can inherit invalid console handles. Replacing
        # CRT descriptors alone does not repair Python's WindowsConsoleIO or
        # GetStdHandle, which child processes/libraries may still consult.
        sys.stdout=sys.__stdout__=stream;sys.stderr=sys.__stderr__=stream
        if os.name=='nt':
            import ctypes,msvcrt
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.SetStdHandle.argtypes=(ctypes.c_uint32,ctypes.c_void_p);kernel.SetStdHandle.restype=ctypes.c_int
            sys.stdin=sys.__stdin__=open(os.devnull,'r');os.dup2(sys.stdin.fileno(),0)
            for identifier,fd in ((-10,0),(-11,1),(-12,2)):
                os.set_inheritable(fd,True)
                if not kernel.SetStdHandle(identifier&0xffffffff,msvcrt.get_osfhandle(fd)):raise ctypes.WinError(ctypes.get_last_error())
        sys.argv=[sys.argv[0],*rest]
        from .run_full import main as run
        run()


if __name__=='__main__':main()
