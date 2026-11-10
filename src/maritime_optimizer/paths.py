"""Filesystem locations.

Read-only data (regulatory CSVs, ports, example fleet) ships inside the package.
Downloaded cartography and caches live in a user-writable directory, because the
installed package directory may be read-only:

- ``$MARITIME_OPT_HOME`` if set;
- otherwise ``%LOCALAPPDATA%\\maritime-optimizer`` on Windows;
- otherwise ``$XDG_CACHE_HOME/maritime-optimizer`` or ``~/.cache/maritime-optimizer``.
"""

import os
import sys
from pathlib import Path

PACKAGE_DATA_DIR = Path(__file__).parent / "data"
REGULATORY_DIR = PACKAGE_DATA_DIR / "regulatory"


def _user_home() -> Path:
    env = os.environ.get("MARITIME_OPT_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "maritime-optimizer"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "maritime-optimizer"


USER_DIR = _user_home()
GEODATA_DIR = USER_DIR / "geodata"
CACHE_DIR = USER_DIR / "cache"
