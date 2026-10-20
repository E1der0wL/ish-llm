"""Fail-closed CI prerequisites with safe, reproducible namespace diagnostics."""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tomllib

from tests.llm.checks_output import emit_annotation


def command(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
        return {"command": arguments, "exit": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": arguments, "exit": None, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-revision", required=True)
    parser.add_argument("--suite", choices=("llm", "hub"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    expected_python = tomllib.loads((root / "pyproject.toml").read_text())["project"]["requires-python"]
    host = command(["git", "-C", str(root / "ish.platform"), "rev-parse", "HEAD"])
    host_status = command(["git", "-C", str(root / "ish.platform"), "status", "--porcelain",
                           "--untracked-files=no"])
    errors = []
    if expected_python != "==" + platform.python_version():
        errors.append(f"Python {platform.python_version()} does not match {expected_python}")
    if host.get("stdout", "").strip() != args.host_revision:
        errors.append("external host revision mismatch or checkout missing")
    if host_status["exit"] != 0 or host_status["stdout"].strip():
        errors.append("external host has modified tracked sources or cannot be inspected")
    if not (root / "ish.platform/src/ish/plugin/manager.py").is_file():
        errors.append("expected host import path ish.platform/src/ish is missing")
    report = {"python": platform.python_version(), "host": host, "host_status": host_status,
              "kernel": platform.release(),
              "suite": args.suite, "errors": errors}
    report["bubblewrap"] = command(["bwrap", "--version"])
    report["sandbox_smoke"] = command(["bwrap", "--unshare-all", "--ro-bind", "/", "/",
                                       "--", "/usr/bin/true"])
    if report["sandbox_smoke"]["exit"] != 0:
        errors.append("required unprivileged namespace isolation is unavailable")
        report["namespace_smoke"] = command(["unshare", "--user", "--map-root-user",
                                             "--net", "/usr/bin/true"])
        if os.environ.get("GITHUB_ACTIONS") == "true":
            # Read-only diagnosis; the Tool process itself is never run as sudo.
            report["kernel_denials"] = command(["sudo", "-n", "journalctl", "-k",
                "--since=-5min", "--no-pager", "-g", 'apparmor="DENIED"|type=1326'])
    report["namespace_policy"] = {}
    for name in ("kernel/unprivileged_userns_clone", "user/max_user_namespaces",
                 "kernel/apparmor_restrict_unprivileged_userns"):
        path = Path("/proc/sys") / name
        report["namespace_policy"][name] = path.read_text().strip() if path.exists() else None
    path = Path("/proc/self/status")
    report["process_security"] = [line for line in path.read_text().splitlines()
                                  if line.startswith(("CapEff:", "NoNewPrivs:", "Seccomp:"))]
    path = Path("/proc/self/attr/current")
    try:
        report["apparmor_context"] = path.read_text().strip() if path.exists() else None
    except OSError as error:
        report["apparmor_context"] = {"unavailable": str(error)}
    report["bwrap_profiles"] = {str(path): path.read_text() for path in
                                Path("/etc/apparmor.d").glob("*bwrap*") if path.is_file()}
    if args.suite == "hub":
        for executable, variable in (("Xvfb", "HUB_TEST_XVFB"), ("xclip", "HUB_TEST_XCLIP")):
            path = os.environ.get(variable) or shutil.which(executable)
            report[executable] = path
            if not path or not os.access(path, os.X_OK):
                errors.append(f"required {executable} executable is missing")
    if args.suite == "llm":
        report["tesseract"] = command(["tesseract", "--list-langs"])
        languages = report["tesseract"].get("stdout", "").splitlines()
        if report["tesseract"]["exit"] != 0 or "eng" not in languages:
            errors.append("required Tesseract CLI or English language data is missing")
    report["ok"] = not errors
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    brief = {key: value for key, value in report.items() if key not in ("kernel_denials", "bwrap_profiles")}
    emit_annotation("Required test environment", brief, level="error" if errors else "notice")
    denials = report.get("kernel_denials", {})
    for line in denials.get("stdout", "").splitlines()[-4:]:
        emit_annotation("Kernel denial", {"line": line}, level="error")
    if report.get("bwrap_profiles"):
        emit_annotation("Installed Bubblewrap profiles", report["bwrap_profiles"], level="notice")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
