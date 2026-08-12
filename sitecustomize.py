"""Repository-local Python hygiene defaults.

Python imports ``sitecustomize`` automatically when the repository root is on
``sys.path``. Keep bytecode out of the source checkout even when a developer
runs Python directly instead of using a managed launcher.
"""

from __future__ import annotations

import sys


sys.dont_write_bytecode = True
