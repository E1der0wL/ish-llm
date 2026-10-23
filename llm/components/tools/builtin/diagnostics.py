"""Linux 상태의 읽기 전용 관찰. 사건을 추측하거나 Run 상태의 별도 원본을 만들지 않는다."""

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
from llm.services.infrastructure.process_state import ProcessStat
from llm.core.configuration import UNSET
from .diagnostic_values import (SystemInspectArguments, ProcessListArguments, ProcessInspectArguments,
    DiskObservation, SystemObservation, UnavailableProcess, ProcessPage, FileDescriptorTarget,
    UnavailableFileDescriptor, ProcessDetails, ProcessInspection)


class DiagnosticTools:
    """호스트가 명시적으로 노출한 /proc 관찰. 결과는 서로 다른 시점의 best-effort 표본이다."""

    MEMORY_FILE = ProcessStat.ROOT / "meminfo"
    MEMORY_FIELDS = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    MEMORY_SEPARATOR = ":"
    MEMORY_KIB_BYTES = 1024
    PROC_CWD = "cwd"
    PROC_EXECUTABLE = "exe"
    PROC_COMMAND = "cmdline"
    PROC_DESCRIPTORS = "fd"
    STANDARD_DESCRIPTORS = ((ProcessDetails.stdin, "0"), (ProcessDetails.stdout, "1"), (ProcessDetails.stderr, "2"))
    COMMAND_SEPARATOR = b"\0"
    COMMAND_ENCODING = "utf-8"
    COMMAND_ERRORS = "replace"
    MIN_PID = 1
    INVALID_PID = "pid must be a positive integer"
    IDENTITY_CHANGED = "PID identity changed; list processes again"
    IDENTITY_RACED = "PID identity changed during observation"

    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def _stat(pid):
        return ProcessStat.read(pid)

    def declarations(self):
        return ((SystemInspectArguments, self.system), (ProcessListArguments, self.processes),
                (ProcessInspectArguments, self.inspect))

    def system(self, arguments):
        """작업 루트의 디스크와 커널/메모리 상태를 반환한다. 환경변수 전체를 수집하지 않는다."""
        usage = shutil.disk_usage(self.root)
        memory = {}
        for line in self.MEMORY_FILE.read_text().splitlines():
            key, value = line.split(self.MEMORY_SEPARATOR, 1)
            if key in self.MEMORY_FIELDS:
                memory[key] = int(value.split()[0]) * self.MEMORY_KIB_BYTES
        return SystemObservation(datetime.now(timezone.utc).isoformat(), os.uname().release,
            sys.version, os.cpu_count(), list(os.getloadavg()), memory,
            DiskObservation(str(self.root), usage.total, usage.used, usage.free)).to_dict()

    def processes(self, arguments):
        """보이는 PID 목록의 이름 검색·페이지 조회. 사라지거나 읽을 수 없는 PID는 표시한다."""
        arguments = ProcessListArguments.read(arguments)
        query = arguments.query.casefold()
        rows, unavailable = [], []
        limit = arguments.limit
        for pid in sorted(int(path.name) for path in ProcessStat.ROOT.iterdir() if path.name.isdecimal()):
            if pid <= arguments.after_pid:
                continue
            try:
                row = self._stat(pid)
            except (OSError, ValueError, IndexError) as error:
                unavailable.append(UnavailableProcess(pid, type(error).__name__))
                continue
            if query not in row.name.casefold():
                continue
            if limit is not None and len(rows) >= limit:
                return ProcessPage(rows, unavailable, rows[-1].pid).to_dict()
            rows.append(row)
        return ProcessPage(rows, unavailable).to_dict()

    def inspect(self, arguments):
        """PID 재사용이 감지되면 다른 프로세스 자료를 합쳐 반환하지 않는다."""
        arguments = ProcessInspectArguments.read(arguments)
        pid = arguments.pid
        if type(pid) is not int or pid < self.MIN_PID:
            raise ValueError(self.INVALID_PID)
        root = ProcessStat.ROOT / str(pid)
        initial = self._stat(pid)
        if arguments.expected_start_ticks is not UNSET and arguments.expected_start_ticks != initial.start_ticks:
            raise ValueError(self.IDENTITY_CHANGED)
        result = ProcessInspection(initial)
        result.observe(ProcessDetails.cwd, lambda: os.readlink(root / self.PROC_CWD))
        result.observe(ProcessDetails.executable, lambda: os.readlink(root / self.PROC_EXECUTABLE))
        result.observe(ProcessDetails.argv, lambda: [part.decode(self.COMMAND_ENCODING, errors=self.COMMAND_ERRORS)
            for part in (root / self.PROC_COMMAND).read_bytes().split(self.COMMAND_SEPARATOR) if part])
        for declaration, fd in self.STANDARD_DESCRIPTORS:
            result.observe(declaration, lambda fd=fd: os.readlink(root / self.PROC_DESCRIPTORS / fd))
        if arguments.include_files:
            def files():
                values = []
                for path in sorted((root / self.PROC_DESCRIPTORS).iterdir(), key=lambda p: int(p.name)):
                    try:
                        values.append(FileDescriptorTarget(int(path.name), os.readlink(path)))
                    except OSError as error:
                        values.append(UnavailableFileDescriptor(int(path.name), type(error).__name__))
                return values
            result.observe(ProcessDetails.files, files)
        if self._stat(pid).start_ticks != initial.start_ticks:
            raise ValueError(self.IDENTITY_RACED)
        return result.to_dict(observed_at=datetime.now(timezone.utc).isoformat())
