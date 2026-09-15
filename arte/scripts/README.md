# Project-owned entry points

`validate.ps1` runs offline formatting, tests, static checks and copied-source hashes.
`deploy.ps1` installs an immutable offline CLI release without service activation.
`run.ps1` verifies a release and reports its blocked service plan. Start is rejected.
Full selective service orchestration remains unimplemented.

All generated output goes to an explicit external runtime root. Python helpers must
set `PYTHONDONTWRITEBYTECODE=1`. No script calls a parent launcher.
