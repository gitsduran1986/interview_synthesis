"""Where things live.

The package ships **code**; the **data** lives in whatever directory you run from. That split
is what makes this containerizable: build the image once, mount a workspace at `/work`, and
nothing in the image needs to know about it.

    interview_synthesis/ui/index.html   ships in the wheel   -> ui_template()
    structured/  out/  raw/  .env       the workspace        -> workspace()
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path

ENV_VAR = "INTERVIEW_SYNTHESIS_WORKSPACE"


def workspace() -> Path:
    """The directory holding raw/, structured/ and out/. Defaults to the cwd."""
    return Path(os.environ.get(ENV_VAR, ".")).resolve()


def raw_dir() -> Path:
    return workspace() / "raw"


def structured_dir() -> Path:
    return workspace() / "structured"


def out_dir() -> Path:
    return workspace() / "out"


def env_file() -> Path:
    return workspace() / ".env"


def ui_template() -> Path:
    """The UI page, shipped as package data rather than read from the repo."""
    return Path(str(resources.files("interview_synthesis") / "ui" / "index.html"))
