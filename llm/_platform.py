"""실행 진입점에서 공유하는 Linux 지원 조건. 도메인에는 OS 분기를 전파하지 않는다."""

import sys


def require_linux() -> None:
    """영속 데이터 변경이나 프로세스 생성 전에 지원 OS를 확인한다."""
    if sys.platform != "linux":
        raise RuntimeError("ish llm supports Linux only; run it on Linux or WSL2")
