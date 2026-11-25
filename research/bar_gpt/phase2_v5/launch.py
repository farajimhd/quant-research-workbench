"""Detached Windows entry point with durable stdout/stderr through process exit."""

import os
import sys
import argparse
import traceback
from pathlib import Path

os.environ.update(PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")


def main():
    """Repair WMI-inherited console handles before importing worker libraries.

WMI can create a persistent process without a terminal, but Python and spawned
workers still expect valid standard handles. Keep the log stream alive through
atexit; closing it when main returns would hide late failures.
"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--console-log", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    path = args.console_log.resolve()
    if not path.is_relative_to(Path("D:/TradingML/runtimes").resolve()) or not path.parent.is_dir():
        raise ValueError("Existing runtime directory required for console log")
    stream = path.open("a", encoding="utf-8", buffering=1)
    os.dup2(stream.fileno(), 1)
    os.dup2(stream.fileno(), 2)
    sys.stdout = sys.__stdout__ = stream
    sys.stderr = sys.__stderr__ = stream
    if os.name == "nt":
        import ctypes
        import msvcrt
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetStdHandle.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
        kernel.SetStdHandle.restype = ctypes.c_int
        sys.stdin = sys.__stdin__ = open(os.devnull, "r")
        os.dup2(sys.stdin.fileno(), 0)
        for identifier, descriptor in ((-10, 0), (-11, 1), (-12, 2)):
            os.set_inheritable(descriptor, True)
            if not kernel.SetStdHandle(identifier & 0xffffffff, msvcrt.get_osfhandle(descriptor)):
                raise ctypes.WinError(ctypes.get_last_error())
    try:
        sys.argv = [sys.argv[0], *remaining]
        from .run_train import main as run
        run()
    except BaseException:
        traceback.print_exc(file=stream)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

