"""Exercise atomic frame writes against a real hidden Windows console."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows console contract')
def test_atomic_console_write_readback_and_unchanged_skip():
    root=Path('D:/TradingML/runtimes/bar_gpt/panel-tests');root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        folder=Path(temporary);script=folder/'console_test.py';result=folder/'result.json'
        repository=Path(__file__).resolve().parents[3]
        script.write_text('''import ctypes,json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from rich.text import Text
from research.bar_gpt.v5.progress_windows import WindowsFrame,Cell,Coord,Rect
f=WindowsFrame()
try:
 assert f.draw(Text('First stable row'))
 assert not f.draw(Text('First stable row'))
 assert f.draw(Text('Updated'))
 w,h=f.size();cells=(Cell*(w*h))();area=Rect(f.info.window.Left,f.info.window.Top,f.info.window.Right,f.info.window.Bottom)
 f.api.ReadConsoleOutputW.argtypes=[ctypes.c_void_p,ctypes.POINTER(Cell),Coord,Coord,ctypes.POINTER(Rect)]
 assert f.api.ReadConsoleOutputW(f.handle,cells,Coord(w,h),Coord(0,0),ctypes.byref(area))
 text=''.join(c.character.UnicodeChar for c in cells)
 assert text.startswith('Updated'+(' '*(w-7))),repr(text[:w])
 assert f.frames==2
 from research.bar_gpt.v5.shards.progress import render
 import time
 snapshot=dict(state='running',updated=time.time(),workers=[dict(id=i+1,ticker='TEST',phase='query',done=1,total=250) for i in range(64)])
 panel=render(snapshot,w,h)
 assert f.draw(panel)
 assert not f.draw(panel)
 Path(sys.argv[2]).write_text(json.dumps(dict(passed=True,frames=f.frames,width=w,height=h)))
finally:f.close()
''')
        startup=subprocess.STARTUPINFO();startup.dwFlags=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
        child=subprocess.Popen([sys.executable,'-B',str(script),str(repository),str(result)],
                               creationflags=subprocess.CREATE_NEW_CONSOLE,startupinfo=startup,
                               env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        try:
            assert child.wait(timeout=20)==0
            assert json.loads(result.read_text())['passed']
        finally:
            if child.poll() is None:child.terminate();child.wait(timeout=5)
