"""격리 실행기의 신뢰된 시작 게이트. stdin 수신 전에는 사용자 명령을 실행하지 않는다.

부모 사망 신호와 자원 제한을 설정한 뒤 명령을 시작한다. -I로 실행하며 llm/외부 패키지를 import하지 않는다.
"""

import json
import os
import subprocess
import sys


def main():
    import ctypes
    import signal
    # 백엔드가 강제 종료되어도 게이트/그룹을 정리한다. bwrap은 부모 사망 시
    # 별도 PID namespace의 자식까지 정리한다. 부모 확인은 설정 전후 경쟁을 막는다.
    signal.signal(signal.SIGTERM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot configure parent-death signal")
    if os.getppid() != int(sys.argv[1]):
        return 1
    data = json.load(sys.stdin.buffer)
    import resource
    memory = data["memory_bytes"]
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    process = subprocess.Popen(data["argv"], cwd=data["cwd"], env=data["env"],
                               stdin=subprocess.PIPE, stdout=sys.stdout.buffer, stderr=sys.stderr.buffer)
    process.communicate(json.dumps(data["payload"], ensure_ascii=True, allow_nan=False).encode("utf-8"))
    return process.returncode


if __name__ == "__main__":
    sys.exit(main())
