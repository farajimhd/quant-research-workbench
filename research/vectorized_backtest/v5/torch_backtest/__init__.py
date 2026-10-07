"""Independent parameterized Torch research simulator, never live execution."""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

# Install the pinned compiler before custom-op registration can import Dynamo
# or Inductor. Their optional-import globals cannot be repaired by clearing
# discovery caches after those modules have already loaded.
from .runtime import configure_compiler

configure_compiler()

from .grid import Candidate, Settings, build_grid, grid_manifest
from .runner import SqueezeRunner
from .tape import SqueezeTape
from .genome import StrategySpace
from .search_runner import SearchRunner
from .search_objective import SessionObjective

__all__ = [
    "Candidate",
    "Settings",
    "build_grid",
    "grid_manifest",
    "SqueezeRunner",
    "SqueezeTape",
    "StrategySpace",
    "SearchRunner",
    "SessionObjective",
]
