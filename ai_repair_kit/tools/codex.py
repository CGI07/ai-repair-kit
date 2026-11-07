"""Discover local Codex locations without running Codex or opening its files."""

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..local_files import ordinary_path

DISPLAY_NAME = "OpenAI Codex"


@dataclass(frozen=True)
class Locations:
    home: Path
    executable: Path | None
    sqlite_override: Path | None


def discover(environ: Mapping[str, str] | None = None, profile: Path | None = None) -> Locations:
    env = os.environ if environ is None else environ
    configured_home = env.get("CODEX_HOME")
    home = Path(configured_home) if configured_home else (profile or Path.home()) / ".codex"
    sqlite_override = env.get("CODEX_SQLITE_HOME")
    return Locations(home, _find_cli(env), Path(sqlite_override) if sqlite_override else None)


def _find_cli(env: Mapping[str, str]) -> Path | None:
    names = ("codex.exe", "codex.cmd", "codex.bat") if os.name == "nt" else ("codex",)
    directories = list(env.get("PATH", "").split(os.pathsep))
    if env.get("CODEX_INSTALL_DIR"):
        directories.append(env["CODEX_INSTALL_DIR"])
    if env.get("LOCALAPPDATA"):
        directories.append(str(Path(env["LOCALAPPDATA"]) / "Programs" / "OpenAI" / "Codex" / "bin"))
    for value in directories:
        directory = Path(value.strip('"'))
        try:
            ordinary_path(directory, directory=True)
        except OSError:
            continue
        for name in names:
            candidate = directory / name
            try:
                if stat.S_ISREG(candidate.lstat().st_mode):
                    return candidate
            except OSError:
                continue
    return None
