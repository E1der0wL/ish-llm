"""격리 실행기의 신뢰된 시작 게이트. stdin 수신 전에는 사용자 명령을 실행하지 않는다.

부모 사망 신호와 자원 제한을 설정한 뒤 명령을 시작한다. -I로 실행하며 llm/외부 패키지를 import하지 않는다.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import os
import subprocess
import sys


@dataclass(frozen=True, slots=True)
class ProcessLaunch:
    """Only transport; the parent runner owns environment, isolation and limit policy."""

    NO_PAYLOAD = None
    UNLIMITED_MEMORY = None
    ENSURE_ASCII = False

    argv: list[str]
    cwd: str
    env: dict[str, str]
    payload: object
    memory_bytes: int | None

    @classmethod
    def read(cls, value):
        return cls(**deepcopy(value))

    def to_dict(self):
        return asdict(self)

    def encode(self):
        return json.dumps(self.to_dict(), ensure_ascii=self.ENSURE_ASCII,
            allow_nan=ProcessGate.ALLOW_NON_FINITE).encode(ProcessGate.JSON_ENCODING)


class ProcessGate:
    ENTRYPOINT = "__main__"
    CURRENT_PROCESS = None
    USE_ERRNO = True
    ISOLATED_ARGUMENTS = ("-I", "-S")
    PARENT_PID_ARGUMENT = 1
    PR_SET_PDEATHSIG = 1
    PRCTL_UNUSED = 0
    PRCTL_SUCCESS = 0
    PARENT_CHANGED_EXIT = 1
    SIGNAL_ERROR = "Cannot configure parent-death signal"
    JSON_ENCODING = "utf-8"
    ENSURE_ASCII = True
    ALLOW_NON_FINITE = False
    ENVIRONMENT = {}

    @classmethod
    def environment(cls):
        return dict(cls.ENVIRONMENT)

    @classmethod
    def command(cls, python, parent_pid):
        return [python, *cls.ISOLATED_ARGUMENTS, str(Path(__file__)), str(parent_pid)]


def main():
    import ctypes
    import signal
    # 백엔드가 강제 종료되어도 게이트/그룹을 정리한다. bwrap은 부모 사망 시
    # 별도 PID namespace의 자식까지 정리한다. 부모 확인은 설정 전후 경쟁을 막는다.
    signal.signal(signal.SIGTERM, lambda *_: os.killpg(os.getpgrp(), signal.SIGKILL))
    libc = ctypes.CDLL(ProcessGate.CURRENT_PROCESS, use_errno=ProcessGate.USE_ERRNO)
    if libc.prctl(ProcessGate.PR_SET_PDEATHSIG, signal.SIGTERM,
                  ProcessGate.PRCTL_UNUSED, ProcessGate.PRCTL_UNUSED, ProcessGate.PRCTL_UNUSED) != ProcessGate.PRCTL_SUCCESS:
        raise OSError(ctypes.get_errno(), ProcessGate.SIGNAL_ERROR)
    if os.getppid() != int(sys.argv[ProcessGate.PARENT_PID_ARGUMENT]):
        return ProcessGate.PARENT_CHANGED_EXIT
    data = ProcessLaunch.read(json.load(sys.stdin.buffer))
    import resource
    memory = data.memory_bytes
    if memory is not ProcessLaunch.UNLIMITED_MEMORY:
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    # 명시하지 않은 다른 자원 제한(core dump 등)은 부모 OS 설정을 상속한다.
    process = subprocess.Popen(data.argv, cwd=data.cwd, env=data.env,
                               stdin=subprocess.PIPE, stdout=sys.stdout.buffer, stderr=sys.stderr.buffer)
    # 일반 argv 실행에는 stdin EOF를 전달한다. 등록 Tool의 JSON 프로토콜은 유지한다.
    payload = data.payload
    process.communicate(ProcessLaunch.NO_PAYLOAD if payload is ProcessLaunch.NO_PAYLOAD else json.dumps(payload, ensure_ascii=ProcessGate.ENSURE_ASCII,
        allow_nan=ProcessGate.ALLOW_NON_FINITE).encode(ProcessGate.JSON_ENCODING))
    return process.returncode


if __name__ == ProcessGate.ENTRYPOINT:
    sys.exit(main())
