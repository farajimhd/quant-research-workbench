"""Bounded V60 two-session replay plus production reproduction profile."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
def main():
    import sys
    if '--reproduce' not in sys.argv:sys.argv.append('--reproduce')
    from .profile_global import main as run
    run()
if __name__=='__main__':main()
