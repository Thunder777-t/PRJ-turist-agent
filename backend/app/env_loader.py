from __future__ import annotations

from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


_LOADED = False
_LOADED_PATHS: tuple[str, ...] = ()


def _candidate_env_paths() -> Iterable[Path]:
    # backend/app/env_loader.py -> backend/app -> backend -> project root
    backend_dir = Path(__file__).resolve().parents[1]
    project_root = Path(__file__).resolve().parents[2]
    cwd = Path.cwd()

    return (
        project_root / ".env",
        backend_dir / ".env",
        cwd / ".env",
    )


def load_project_dotenv() -> tuple[str, ...]:
    global _LOADED
    global _LOADED_PATHS

    if _LOADED:
        return _LOADED_PATHS

    loaded: list[str] = []
    seen: set[Path] = set()

    for path in _candidate_env_paths():
        try:
            resolved = path.resolve()
        except Exception:
            continue

        if resolved in seen or not resolved.exists() or not resolved.is_file():
            continue
        seen.add(resolved)
        if load_dotenv(dotenv_path=resolved, override=False):
            loaded.append(str(resolved))

    _LOADED_PATHS = tuple(loaded)
    _LOADED = True
    return _LOADED_PATHS

