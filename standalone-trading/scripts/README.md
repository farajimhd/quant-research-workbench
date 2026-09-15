# Project-owned entry points

Implement one deploy entry point and one run entry point under this boundary.
Build and validation helpers must not call parent launchers.

All generated output goes to an explicit external runtime root. Python helpers must
set `PYTHONDONTWRITEBYTECODE=1`. No runnable scripts are provided in phase 0.
