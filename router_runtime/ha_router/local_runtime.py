"""Optional dependencies installed only inside this project."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def enable():
    vendor = ROOT / 'runtime_semantic'
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))

