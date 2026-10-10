"""Fixed terminal canvas: write changed cell spans without clearing frames."""
import os
from rich.console import Console
from rich.cells import cell_len
from rich.style import Style
from rich.text import Text


def changed_spans(previous,current):
    for row,line in enumerate(current):
        old=previous[row] if row<len(previous) else ''
        if old==line:continue
        first=0
        while first<min(len(old),len(line)) and old[first]==line[first]:first+=1
        last=len(line)
        if len(old)==len(line):
            while last>first and old[last-1]==line[last-1]:last-=1
        yield row+1,cell_len(line[:first])+1,line[first:last]


class FixedTerminal:
    def __init__(self,console):self.console=console;self.previous=[];self.size=None

    def write(self,value):
        # Unbuffered Windows console streams can accept only a bounded native
        # write. TextIOWrapper does not reliably retry a partial raw write.
        # Keep even a maximized full frame below that limit per write.
        for offset in range(0,len(value),4096):
            self.console.file.write(value[offset:offset+4096])
        self.console.file.flush()

    def dimensions(self):
        # Windows can retain a wider scrollback buffer than the visible viewport.
        # Rich/shutil may report that buffer; absolute cursor output needs the window.
        if os.name=='nt' and self.console.is_terminal:
            import ctypes
            import msvcrt
            from ctypes import wintypes
            class Info(ctypes.Structure):
                _fields_=[('size',wintypes._COORD),('cursor',wintypes._COORD),
                          ('attributes',wintypes.WORD),('window',wintypes.SMALL_RECT),
                          ('maximum',wintypes._COORD)]
            try:
                info=Info();handle=msvcrt.get_osfhandle(self.console.file.fileno())
                if ctypes.windll.kernel32.GetConsoleScreenBufferInfo(wintypes.HANDLE(handle),ctypes.byref(info)):
                    return info.window.Right-info.window.Left+1,info.window.Bottom-info.window.Top+1
            except (AttributeError,OSError,ValueError):pass
        return self.console.width,self.console.height

    def update(self,renderable):
        width,height=self.dimensions()
        # Build layout and render from ONE size snapshot, even during maximization.
        if callable(renderable):renderable=renderable(width,height)
        if isinstance(renderable,str):renderable=Text(renderable)
        canvas=Console(width=width,height=height,color_system=self.console.color_system,
                       no_color=self.console.no_color,force_terminal=True,legacy_windows=False)
        segments=canvas.render_lines(renderable,canvas.options.update_dimensions(width,height))
        blank=(' ',Style.null());lines=[]
        for line in segments[:height]:
            cells=[]
            for segment in line:
                if segment.control:continue
                style=segment.style or Style.null()
                if canvas.no_color:style=style.without_color
                for char in segment.text:
                    length=cell_len(char)
                    if not length and cells:
                        previous,previous_style=cells[-1];cells[-1]=(previous+char,previous_style)
                    elif length:
                        cells.append((char,style));cells.extend([('',style)]*(length-1))
            lines.append((cells+[blank]*width)[:width])
        lines.extend([[blank]*width for _ in range(height-len(lines))])
        resized=self.size!=(width,height)
        previous=[] if resized or getattr(self,'settle_resize',False) else self.previous
        output=[]
        for row,cells in enumerate(lines):
            old=previous[row] if row<len(previous) else []
            if old==cells:continue
            first=0;last=width
            while first<min(len(old),width) and old[first]==cells[first]:first+=1
            if len(old)==width:
                while last>first and old[last-1]==cells[last-1]:last-=1
            # Include both cells of any changed wide character.
            while first and (cells[first][0]=='' or (first<len(old) and old[first][0]=='')):first-=1
            while last<width and (cells[last][0]=='' or (last<len(old) and old[last][0]=='')):last+=1
            output.append(f'\x1b[{row+1};{first+1}H')
            start=first
            while start<last:
                style=cells[start][1];end=start+1
                while end<last and cells[end][1]==style:end+=1
                value=''.join(cell[0] for cell in cells[start:end])
                output.append(style.render(value,color_system=canvas._color_system))
                start=end
        # A size change while building the frame invalidates every cached cell.
        if self.dimensions()!=(width,height):
            self.previous=[];self.size=None;return
        if output:
            self.write(''.join(output))
        had_size=self.size is not None
        self.previous=lines;self.size=(width,height)
        # Console hosts may reflow the old screen asynchronously after resize.
        # Reconcile once more on the next tick; ordinary ticks remain cell-only.
        self.settle_resize=resized and had_size

    def __enter__(self):
        # Alternate screen is entered once. No erase-screen/erase-line on refresh.
        self.console.file.write('\x1b[?1049h\x1b[?25l\x1b[?7l');self.console.file.flush()
        return self

    def __exit__(self,*args):
        self.console.file.write('\x1b[?7h\x1b[?25h\x1b[?1049l');self.console.file.flush()
