"""Explicit workstation launcher; no scheduled automation."""
import os,subprocess,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'


def main():
    command=[sys.executable,'-B','-u','-m','research.bar_gpt.phase1_v26.train',*sys.argv[1:]]
    print(subprocess.list2cmdline(command),flush=True)
    raise SystemExit(subprocess.call(command))


if __name__=='__main__':main()
