"""Immutable deployment and workstation ownership admission for this campaign."""
import json,os,subprocess,time
from pathlib import Path
import psutil,torch
from ..v5.shards.phase1_contract import file_hash,write_json


def preflight(args):
    if not args.output.resolve().is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Runtime authority required')
    if (args.output/'STOP').exists() or (args.output.parent/'STOP').exists():raise RuntimeError('STOP remains in force')
    if args.device=='cuda':
        if os.environ.get('COMPUTERNAME','').upper()!='DESKTOP-SAAI85T':raise RuntimeError('Workstation GPU required')
        proof=json.loads((args.output.parent/f'deployment-{args.source_revision[:9]}.json').read_text())
        root=Path(__file__).resolve().parents[3]
        if proof['revision']!=args.source_revision:raise ValueError('Revision mismatch')
        for name,expected in proof['files'].items():
            if file_hash(root/name)!=expected:raise ValueError(f'Snapshot changed: {name}')
    args.output.mkdir(parents=True,exist_ok=args.resume)
    own=psutil.Process();write_json(args.output/'process.json',dict(pid=own.pid,created=own.create_time(),command=own.cmdline()))
    if os.name=='nt':own.nice(psutil.NORMAL_PRIORITY_CLASS);own.ionice(psutil.IOPRIO_NORMAL)
    if args.device=='cuda':
        desktop={'dwm.exe','shellhost.exe','crossdeviceresume.exe','startmenuexperiencehost.exe','searchhost.exe','explorer.exe',
            'shellexperiencehost.exe','msedgewebview2.exe','lockapp.exe','logioptionsplus_agent.exe','textinputhost.exe','taskmgr.exe',
            'chatgpt.exe','codex-computer-use-swift.exe','windowsterminal.exe'}
        other=[]
        for line in subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name','--format=csv,noheader'],text=True).splitlines():
            fields=line.split(',',1)
            if not fields[0].strip().isdigit():continue
            pid=int(fields[0]);name=fields[1].strip().lower()
            if pid==own.pid or pid==4:continue
            # Verified Windows sign-in surface can appear in WDDM compute inventory.
            if name in {'c:\\windows\\system32\\logonui.exe', 'c:\\windows\\system32\\applicationframehost.exe', 'c:\\windows\\immersivecontrolpanel\\systemsettings.exe'}:continue
            if name.startswith(('c:\\windows\\','c:\\program files\\','c:\\program files (x86)\\')) and Path(name).name in desktop:continue
            other.append(dict(pid=pid,name=name))
        write_json(args.output/'gpu-ownership.json',dict(other_compute_owners=other))
        if other:raise RuntimeError(f'GPU owned by another process: {other}')
        if psutil.virtual_memory().available<64*2**30 or psutil.disk_usage(str(args.output)).free<150*2**30:raise RuntimeError('RAM/disk reserve unavailable')
        write_json(args.output/'hardware.json',dict(host=os.environ['COMPUTERNAME'],gpu=torch.cuda.get_device_name(),
            memory_bytes=torch.cuda.get_device_properties(0).total_memory,torch=str(torch.__version__),cuda=torch.version.cuda,
            cpu_physical=psutil.cpu_count(logical=False),cpu_logical=psutil.cpu_count(),ram_available=psutil.virtual_memory().available))
