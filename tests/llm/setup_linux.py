"""Create a Linux test environment from the reviewed, hash-verified lock."""

import argparse
from pathlib import Path
import subprocess
import sys

from tests.llm.dependencies import LOCK, ROOT, check, declarations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    if sys.platform != "linux":
        raise SystemExit("Linux is required")
    expected = declarations()[0]["requires-python"]
    actual = subprocess.check_output(
        [args.python, "-c", "import platform; print(platform.python_version())"], text=True).strip()
    if expected != "==" + actual:
        raise SystemExit(f"Python {actual} does not match {expected}")
    if errors := check():
        raise SystemExit("\n".join(errors))
    if args.environment.exists():
        raise SystemExit("Choose a new environment directory; existing environments are preserved")
    subprocess.run([args.uv, "venv", "--python", args.python, str(args.environment)], check=True)
    python = args.environment / "bin/python"
    subprocess.run([args.uv, "pip", "install", "--python", str(python),
                    "--require-hashes", "-r", str(LOCK)], check=True)
    subprocess.run([str(python), "-m", "tests.llm.dependencies", "--installed"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
