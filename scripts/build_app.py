"""Build the native SEC Financials desktop application for this OS."""

from __future__ import annotations

import shutil
import subprocess
import sys
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    for directory in (ROOT / "build", ROOT / "dist"):
        if directory.exists():
            shutil.rmtree(directory)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(ROOT / "SECFinancials.spec"),
    ]
    env = os.environ.copy()
    env["PYINSTALLER_CONFIG_DIR"] = str(ROOT / ".pyinstaller")
    return subprocess.call(command, cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
