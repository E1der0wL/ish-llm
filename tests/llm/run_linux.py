"""현재 소스를 Linux 파일시스템에 복사해 Python 3.12.14로 검증한다. 호스트 파일은 수정하지 않는다."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys


def main():
    if sys.platform != "linux":
        raise SystemExit("Run the test suite on Linux or WSL2")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--modules", nargs="*")
    args = parser.parse_args()
    version = subprocess.check_output([args.python, "-c", "import platform; print(platform.python_version())"], text=True).strip()
    if version != "3.12.14":
        raise SystemExit("Python 3.12.14 required")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="ish-provider-", dir=str(cache)))
    report_root = args.source / "tests/llm/reports"
    report = report_root / target.name
    report.mkdir(parents=True, exist_ok=True)
    files = {}
    for directory in ("llm", "tests/llm", "examples/llm", "docs/llm", "ish.platform/src"):
        for path in (args.source / directory).rglob("*"):
            if path.is_file() and not {"__pycache__", "reports", "manual"}.intersection(path.parts) and path.suffix in (".py", ".md", ".json"):
                relative = path.relative_to(args.source)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("pyproject.toml", ".python-version", "README.md", "AGENTS.md",
                 "tests/__init__.py", "examples/__init__.py"):
        if (args.source / name).exists():
            shutil.copy2(args.source / name, target / name)
    (target / ".venv-linux312").symlink_to(Path(args.python).parent.parent, target_is_directory=True)
    (report / "snapshot.json").write_text(json.dumps({"root": str(target), "python": args.python, "files": files}, indent=2))
    env = dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="True", LITELLM_MODE="PRODUCTION")
    print(target, flush=True)
    modules = args.modules or ["tests.llm.test_provider_runtime", "tests.llm.test_rag_resilience",
                               "tests.llm.test_explicit_configuration", "tests.llm.test_error_boundaries",
                               "tests.llm.test_project_activity", "tests.llm.test_tool_packages",
                               "tests.llm.test_tool_workers", "tests.llm.test_observability",
                               "tests.llm.test_run_transitions", "tests.llm.test_graph_decisions",
                               "tests.llm.test_interaction_decisions"]
    failed = False
    for name, command in [("focused", modules), *([("suite", ["discover", "-s", "tests/llm", "-t", "."])] if args.full else [])]:
        with (report / (name + ".txt")).open("w") as output:
            result = subprocess.run([args.python, "-m", "unittest", *command, "-v"], cwd=target, env=env,
                                    stdout=output, stderr=subprocess.STDOUT)
        failed |= result.returncode != 0
        print(name, result.returncode, flush=True)
    print("Reports:", report, flush=True)
    (report_root / "latest.json").write_text(json.dumps({"directory": report.name,
                                                       "snapshot": str(target),
                                                       "failed": failed}, indent=2))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
