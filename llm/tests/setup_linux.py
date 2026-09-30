"""사내 SDK 버전 재현용 별도 Linux 환경. 기존 가상환경은 변경하지 않는다."""

import os
from pathlib import Path
import subprocess
import tempfile
import argparse
import sys

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-python", required=True, help="기존 검증된 의존성 환경의 Python")
    parser.add_argument("--python", default=sys.executable, help="새 환경에 사용할 Python 3.12.14")
    parser.add_argument("--uv", default=str(Path.home() / ".local/bin/uv"))
    args = parser.parse_args()
    if subprocess.check_output([args.python, "-c", "import platform; print(platform.python_version())"], text=True).strip() != "3.12.14":
        raise SystemExit("Python 3.12.14 required")
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="ish-provider-sdk-", dir=str(cache)))
    env = dict(os.environ, UV_LINK_MODE="copy")
    env.pop("UV_CACHE_DIR", None)
    requirements = subprocess.check_output([args.uv, "pip", "freeze", "--python", args.base_python], text=True, env=env)
    lines = [line for line in requirements.splitlines() if not line.startswith(("litellm=", "openai=", "ish-", "-e "))]
    lines += ["litellm==1.103.1", "openai==2.54.0"]
    (root / "requirements.txt").write_text("\n".join(lines))
    print(root, flush=True)
    subprocess.run([args.uv, "venv", "--python", args.python, str(root / ".venv-linux312")], check=True, env=env)
    subprocess.run([args.uv, "pip", "install", "--python", str(root / ".venv-linux312/bin/python"),
                    "-r", str(root / "requirements.txt")], check=True, env=env)


if __name__ == "__main__":
    main()
