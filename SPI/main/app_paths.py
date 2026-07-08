"""Path helpers for source and PyInstaller builds."""
from pathlib import Path
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """Directory that should hold user-writable app data."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_path(*parts: str) -> Path:
    """Path to bundled read-only resources such as JSON config files."""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parent
    return base.joinpath(*parts)


def user_data_path(filename: str) -> Path:
    return app_dir() / filename
