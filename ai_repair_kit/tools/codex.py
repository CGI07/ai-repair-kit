"""Discover local Codex locations without running Codex or opening its files."""

import ctypes
import os
import re
import stat
import subprocess
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


def installed_desktop_version() -> str | None:
    """Query only the registered x64 MSIX version for the current Windows user."""
    if os.name != "nt":
        return None
    system = ctypes.create_unicode_buffer(32768)
    if not 0 < ctypes.windll.kernel32.GetSystemDirectoryW(system, len(system)) < len(system):
        return None
    powershell = Path(system.value) / "WindowsPowerShell/v1.0/powershell.exe"
    script = (
        "Import-Module (Join-Path $PSHOME 'Modules/Appx/Appx.psd1') -ErrorAction Stop; "
        "$p = @(Appx\\Get-AppxPackage -Name OpenAI.Codex -PackageTypeFilter Main -ErrorAction Stop); "
        "if ($p.Count -eq 1 -and $p[0].Architecture -eq 'X64' -and $p[0].Status -eq 'Ok' "
        "-and $p[0].PackageFamilyName -eq 'OpenAI.Codex_2p2nqsd0c76g0' "
        "-and $p[0].SignatureKind -eq 'Store') { $p[0].Version.ToString() }"
    )
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=4, creationflags=subprocess.CREATE_NO_WINDOW, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = result.stdout.strip().decode("ascii", errors="replace")
    return version if re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){3}", version) else None


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
