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
    report = args.source / "llm/tests/reports"
    report.mkdir(exist_ok=True)
    files = {}
    for directory in ("llm", "tests", "examples", "ish.platform/src"):
        for path in (args.source / directory).rglob("*"):
            if path.is_file() and not {"__pycache__", "reports"}.intersection(path.parts) and path.suffix in (".py", ".md", ".json"):
                relative = path.relative_to(args.source)
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                files[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in ("pyproject.toml", "README.md", "AGENTS.md", ".ishrc.py"):
        if (args.source / name).exists():
            shutil.copy2(args.source / name, target / name)
    (target / ".venv-linux312").symlink_to(Path(args.python).parent.parent, target_is_directory=True)
    (report / "snapshot.json").write_text(json.dumps({"root": str(target), "python": args.python, "files": files}, indent=2))
    env = dict(os.environ, LITELLM_LOCAL_MODEL_COST_MAP="True", LITELLM_MODE="PRODUCTION")
    print(target, flush=True)
    modules = args.modules or ["llm.tests.test_provider_runtime", "llm.tests.test_rag_resilience"]
    for name, command in [("focused", modules), *([("suite", ["discover", "-s", "tests"])] if args.full else [])]:
        with (report / (name + ".txt")).open("w") as output:
            result = subprocess.run([args.python, "-m", "unittest", *command, "-v"], cwd=target, env=env,
                                    stdout=output, stderr=subprocess.STDOUT)
        print(name, result.returncode, flush=True)
    print("Reports:", report, flush=True)


if __name__ == "__main__":
    main()
