"""Put the repo root on sys.path for the test suite.

The packages are normally importable via the editable install, but that install writes a
.pth file which has proved fragile — repeated `uv sync --reinstall-package` calls can leave
it duplicated and inert, at which point every import fails. Tests shouldn't depend on it.

If `uv run <command>` ever fails with ModuleNotFoundError, repair the install with:

    uv pip install -e .
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
