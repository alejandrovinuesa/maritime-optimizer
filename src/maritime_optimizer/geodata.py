"""Natural Earth cartography download (public domain, 1:50M, ~1 MB).

Only the land polygons are needed: they drive the land-crossing test that decides
which straight-line edges of the maritime graph are navigable. Files are stored in
the user data directory (see :mod:`maritime_optimizer.paths`) and downloaded once.

Source: https://www.naturalearthdata.com (public domain).
"""

import sys
import zipfile
from pathlib import Path
from urllib.request import urlretrieve

from .paths import GEODATA_DIR

NATURAL_EARTH_BASE = "https://naciscdn.org/naturalearth/50m/physical"
REQUIRED_DATASETS = {
    "ne_50m_land": f"{NATURAL_EARTH_BASE}/ne_50m_land.zip",
}


def get_dataset_path(name: str) -> Path:
    """Path of a dataset's shapefile."""
    return GEODATA_DIR / f"{name}.shp"


def has_geodata() -> bool:
    """True if every required dataset is available locally."""
    return all(get_dataset_path(name).exists() for name in REQUIRED_DATASETS)


def ensure_geodata(verbose: bool = True) -> bool:
    """Download any missing dataset. Returns True if all are available afterwards."""
    GEODATA_DIR.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for name, url in REQUIRED_DATASETS.items():
        if get_dataset_path(name).exists():
            continue
        zip_path = GEODATA_DIR / f"{name}.zip"
        try:
            if verbose:
                print(f"Downloading {url} ...", file=sys.stderr)
            urlretrieve(url, zip_path)
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(GEODATA_DIR)
        except Exception as exc:  # network or archive errors
            print(f"Could not download {name}: {exc}", file=sys.stderr)
            all_ok = False
        finally:
            zip_path.unlink(missing_ok=True)
    return all_ok
