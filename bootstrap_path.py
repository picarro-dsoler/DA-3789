"""Shared path setup for DA-3789 / locallib scripts."""

import os
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_ROOT.parent


def _locallib_roots() -> list[Path]:
    """Return package roots that contain a ``locallib`` package dir."""
    roots: list[Path] = []
    seen: set[Path] = set()
    for candidate in (
        REPO_ROOT / "locallib_packages",
        REPO_ROOT / "KPIHub",
        REPO_ROOT / "KPIHubDev",
        REPO_ROOT / "packages",
        REPO_ROOT,
        SCRIPT_ROOT,
    ):
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "locallib").is_dir():
            roots.append(resolved)
    return roots


def activate() -> Path:
    """Configure sys.path and cwd so local modules and locallib import cleanly."""
    path_entries = [str(SCRIPT_ROOT)]
    for root in _locallib_roots():
        path_entries.append(str(root))

    sys.path[:] = path_entries + [p for p in sys.path if p not in path_entries]
    os.chdir(SCRIPT_ROOT)
    (SCRIPT_ROOT / "output").mkdir(parents=True, exist_ok=True)
    return SCRIPT_ROOT
