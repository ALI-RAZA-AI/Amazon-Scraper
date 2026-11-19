"""Project paths. Relative paths in the config files are resolved against the
project root, so the CLI, tests and the Streamlit app all read and write the
same folders no matter which directory they are started from."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


def resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else ROOT / p
