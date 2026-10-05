"""Validate Hub on a hash-recorded Linux filesystem snapshot with Python 3.12.14."""

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true", help="Render PNGs; requires Pillow and Windows fonts")
    args = parser.parse_args()
    if sys.platform != "linux" or platform.python_version() != "3.12.14":
        raise SystemExit("Linux Python 3.12.14 required")
    source = Path(__file__).resolve().parents[2]
    target = Path(tempfile.mkdtemp(prefix="hub-ui-"))
    report = source / "tests/hub/reports"
    report.mkdir(exist_ok=True)
    hashes = {}
    for directory in ("hub", "llm", "examples/hub", "tests/hub", "tests/llm", "ish.platform/src"):
        for path in (source / directory).rglob("*.py"):
            if {"__pycache__", "reports", "manual"}.intersection(path.parts):
                continue
            relative = path.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            hashes[str(relative)] = hashlib.sha256(destination.read_bytes()).hexdigest()
    for name in ("examples/__init__.py", "tests/__init__.py"):
        shutil.copy2(source / name, target / name)
    (report / "snapshot.json").write_text(json.dumps({"root": str(target), "files": hashes}, indent=2))
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/hub", "-t", ".", "-v"],
        cwd=target, capture_output=True, text=True,
        env=dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="True", LITELLM_MODE="PRODUCTION"),
    )
    log = result.stdout + result.stderr
    (report / "suite.txt").write_text(log)
    print(log, end="")
    if result.returncode:
        return result.returncode
    if args.capture:
        subprocess.run(
            [sys.executable, str(source / "tests/hub/manual/render_preview.py")],
            cwd=target, env=dict(os.environ, PYTHONPATH=str(target)), check=True,
        )
    print(f"Snapshot: {target}\nReports: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
