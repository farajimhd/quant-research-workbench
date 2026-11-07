"""Atomic Windows-console frames for the read-only BarGPT observer.

Rich supplies layout and colors; one WriteConsoleOutputW call replaces the
visible character cells without clearing, scrolling or streaming escape codes.
This module is outside the shard recipe: it never reads or changes market data.
"""
import ctypes
from ctypes import wintypes
from io import StringIO

from rich.console import Console


class Coord(ctypes.Structure):
    _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]


class Rect(ctypes.Structure):
    _fields_ = [('Left', ctypes.c_short), ('Top', ctypes.c_short), ('Right', ctypes.c_short), ('Bottom', ctypes.c_short)]


class BufferInfo(ctypes.Structure):
    _fields_ = [('size', Coord), ('cursor', Coord), ('attributes', wintypes.WORD), ('window', Rect), ('maximum', Coord)]


class Character(ctypes.Union):
    _fields_ = [('UnicodeChar', wintypes.WCHAR), ('AsciiChar', ctypes.c_char)]


class Cell(ctypes.Structure):
    _fields_ = [('character', Character), ('attributes', wintypes.WORD)]


class CursorInfo(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('visible', wintypes.BOOL)]


# Windows console attribute indices use BGR order, not ANSI color order.
PALETTE = [(0,0,0),(0,0,128),(0,128,0),(0,128,128),(128,0,0),(128,0,128),(128,128,0),(192,192,192),
           (128,128,128),(0,0,255),(0,255,0),(0,255,255),(255,0,0),(255,0,255),(255,255,0),(255,255,255)]


class WindowsFrame:
    """Own only the observer's stdout screen buffer and restore cursor visibility."""

    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.GetStdHandle.argtypes = [wintypes.DWORD]
        self.api.GetStdHandle.restype = wintypes.HANDLE
        self.api.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(BufferInfo)]
        self.api.WriteConsoleOutputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Cell), Coord, Coord, ctypes.POINTER(Rect)]
        self.api.GetConsoleCursorInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(CursorInfo)]
        self.api.SetConsoleCursorInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(CursorInfo)]
        self.handle = self.api.GetStdHandle(-11 & 0xffffffff)
        self.info = BufferInfo()
        self.previous = None
        self.frames = 0
        self.original_cursor = CursorInfo()
        self.size()  # Fail before changing anything when stdout is redirected.
        if not self.api.GetConsoleCursorInfo(self.handle, ctypes.byref(self.original_cursor)):
            raise ctypes.WinError(ctypes.get_last_error())
        hidden = CursorInfo(self.original_cursor.size, False)
        self.api.SetConsoleCursorInfo(self.handle, ctypes.byref(hidden))

    def size(self):
        """Visible viewport [columns,rows], independent of scrollback buffer height."""
        if not self.api.GetConsoleScreenBufferInfo(self.handle, ctypes.byref(self.info)):
            raise ctypes.WinError(ctypes.get_last_error())
        r = self.info.window
        return r.Right-r.Left+1, r.Bottom-r.Top+1

    @staticmethod
    def _color(color, default):
        if color is None or color.is_default:
            return default
        rgb = color.get_truecolor()
        return min(range(16), key=lambda i: sum((a-b)**2 for a,b in zip(rgb,PALETTE[i])))

    def draw(self, renderable):
        """Render offscreen then atomically replace [height,width] visible cells.

        Unchanged frames are skipped. A full-height space-filled destination
        clears obsolete rows in the same write, never as a separate blank frame.
        """
        width, height = self.size()
        console = Console(file=StringIO(), width=width, height=height, force_terminal=True, color_system='standard')
        lines = console.render_lines(renderable, console.options.update(width=width, height=height), pad=True)
        cells = (Cell * (width*height))()
        default = self.info.attributes
        for cell in cells:
            cell.character.UnicodeChar = ' '
            cell.attributes = default
        for y,line in enumerate(lines[:height]):
            x = 0
            for segment in line:
                if segment.control:
                    continue
                style = segment.style
                foreground = self._color(style.color if style else None, default & 15)
                background = self._color(style.bgcolor if style else None, (default >> 4) & 15)
                if style and style.bold:
                    foreground |= 8
                if style and style.reverse:
                    foreground, background = background, foreground
                for char in segment.text:
                    if x >= width: break
                    cells[y*width+x].character.UnicodeChar = char if ord(char)<=0xffff else '?'
                    cells[y*width+x].attributes = foreground | (background << 4)
                    x += 1
        signature = (width, height, self.info.window.Top, bytes(cells))
        if signature == self.previous:
            return False
        target = Rect(self.info.window.Left,self.info.window.Top,self.info.window.Right,self.info.window.Bottom)
        if not self.api.WriteConsoleOutputW(self.handle,cells,Coord(width,height),Coord(0,0),ctypes.byref(target)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.previous = signature
        self.frames += 1
        return True

    def close(self):
        self.api.SetConsoleCursorInfo(self.handle, ctypes.byref(self.original_cursor))
