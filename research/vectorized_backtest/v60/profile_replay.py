"""V60 full two-session profiling entrypoint; no old kernel source loading."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
from .profile_global import main
if __name__=='__main__':main()
