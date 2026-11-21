"""Run a command list. Kept apart from environment lookups in _lib."""
from __future__ import annotations

import subprocess
from pathlib import Path


def execute(
    cmd: list[str],
    *,
    capture: bool,
    timeout: float | None,
    cwd: str | Path | None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
    )
