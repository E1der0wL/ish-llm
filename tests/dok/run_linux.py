"""Hash-verified native Linux snapshot runner for dok only."""

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time


def main():
    if len(sys.argv) != 1:
        raise SystemExit("This runner accepts no arguments and runs only dok tests")
    if sys.platform != "linux" or platform.python_version() != "3.12.14":
        raise SystemExit("Linux Python 3.12.14 required")
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from tests.source_tree import source_bytes

    target = Path(tempfile.mkdtemp(prefix="dok-"))
    report = root / "tests/dok/reports" / target.name
    report.mkdir(parents=True)
    hashes = {}
    directories = ("dok", "tests/dok", "docs/dok", "ish.platform/src")
    for directory in directories:
        for source in (root / directory).rglob("*"):
            relative = source.relative_to(root)
            if not source.is_file() or {"reports", "manual", "__pycache__"}.intersection(relative.parts):
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            content = source_bytes(source)
            destination.write_bytes(content)
            hashes[relative.as_posix()] = {"sha256": hashlib.sha256(content).hexdigest()}
    for name in ("tests/__init__.py", "ish.platform/pyproject.toml"):
        content = source_bytes(root / name)
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(content)
        hashes[name] = {"sha256": hashlib.sha256(content).hexdigest()}
    for name, values in hashes.items():
        if hashlib.sha256((target / name).read_bytes()).hexdigest() != values["sha256"]:
            raise RuntimeError(f"Snapshot mismatch: {name}")
    (report / "snapshot.json").write_text(json.dumps({"root": str(target), "python": sys.executable,
                                                     "files": hashes}, indent=2))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LITELLM_LOCAL_MODEL_COST_MAP="True", LITELLM_MODE="PRODUCTION")
    for name in ("WSL_DISTRO_NAME", "WSL_INTEROP", "DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        env.pop(name, None)
    suites = [("dok", ["-m", "unittest", "discover", "-s", "tests/dok", "-t", ".", "-v"])]
    results = {}
    print(f"Snapshot: {target}\nReports: {report}", flush=True)
    for name, command in suites:
        started = time.monotonic()
        with (report / f"{name}.txt").open("w") as output:
            result = subprocess.run([sys.executable, *command], cwd=target, env=env,
                                    stdout=output, stderr=subprocess.STDOUT)
        results[name] = {"exit_code": result.returncode, "seconds": time.monotonic() - started}
        print(f"{name}: {results[name]}", flush=True)
    (report / "results.json").write_text(json.dumps(results, indent=2))
    return int(any(item["exit_code"] for item in results.values()))


if __name__ == "__main__":
    raise SystemExit(main())
