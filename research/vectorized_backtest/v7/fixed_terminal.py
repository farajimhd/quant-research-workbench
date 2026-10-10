"""Fixed terminal canvas: write changed cell spans without clearing frames."""
import io
from rich.console import Console
from rich.cells import cell_len


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

    def update(self,renderable):
        width,height=self.console.width,self.console.height
        stream=io.StringIO()
        canvas=Console(file=stream,width=width,height=height,color_system=None,no_color=True)
        canvas.print(renderable,end='')
        lines=stream.getvalue().splitlines()[:height]
        lines=[line+' '*max(0,width-cell_len(line)) for line in lines]
        lines+=[' '*width]*(height-len(lines))
        if self.size!=(width,height):self.previous=[];self.size=(width,height)
        spans=list(changed_spans(self.previous,lines))
        if spans:
            self.console.file.write(''.join(f'\x1b[{row};{column}H{text}' for row,column,text in spans))
            self.console.file.flush()
        self.previous=lines

    def __enter__(self):
        # Alternate screen is entered once. No erase-screen/erase-line on refresh.
        self.console.file.write('\x1b[?1049h\x1b[?25l\x1b[?7l');self.console.file.flush()
        return self

    def __exit__(self,*args):
        self.console.file.write('\x1b[?7h\x1b[?25h\x1b[?1049l');self.console.file.flush()
