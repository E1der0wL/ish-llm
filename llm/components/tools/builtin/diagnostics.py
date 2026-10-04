"""Linux 상태의 읽기 전용 관찰. 사건을 추측하거나 Run 상태의 별도 원본을 만들지 않는다."""

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys


class DiagnosticTools:
    """호스트가 명시적으로 노출한 /proc 관찰. 결과는 서로 다른 시점의 best-effort 표본이다."""

    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def _stat(pid):
        raw = (Path("/proc") / str(pid) / "stat").read_text()
        # comm에는 공백과 괄호가 들어갈 수 있다. 마지막 ')' 뒤부터 필드 3이다.
        left, right = raw.index("("), raw.rindex(")")
        fields = raw[right + 2:].split()
        return {"pid": int(raw[:left].strip()), "name": raw[left + 1:right], "state": fields[0],
                "ppid": int(fields[1]), "process_group": int(fields[2]), "session": int(fields[3]),
                "tty_number": int(fields[4]), "foreground_process_group": int(fields[5]),
                "start_ticks": int(fields[19])}

    def system(self, arguments):
        """작업 루트의 디스크와 커널/메모리 상태를 반환한다. 환경변수 전체를 수집하지 않는다."""
        usage = shutil.disk_usage(self.root)
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
                memory[key] = int(value.split()[0]) * 1024
        return {"observed_at": datetime.now(timezone.utc).isoformat(), "kernel": os.uname().release,
                "python": sys.version, "cpu_count": os.cpu_count(), "load_average": list(os.getloadavg()),
                "memory_bytes": memory, "disk": {"path": str(self.root),
                "total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}}

    def processes(self, arguments):
        """보이는 PID 목록의 이름 검색·페이지 조회. 사라지거나 읽을 수 없는 PID는 표시한다."""
        query = arguments.get("query", "").casefold()
        rows, unavailable = [], []
        limit = arguments.get("limit")
        for pid in sorted(int(path.name) for path in Path("/proc").iterdir() if path.name.isdecimal()):
            if pid <= arguments.get("after_pid", 0):
                continue
            try:
                row = self._stat(pid)
            except (OSError, ValueError, IndexError) as error:
                unavailable.append({"pid": pid, "reason": type(error).__name__})
                continue
            if query not in row["name"].casefold():
                continue
            if limit is not None and len(rows) >= limit:
                return {"processes": rows, "unavailable": unavailable, "next_pid": rows[-1]["pid"]}
            rows.append(row)
        return {"processes": rows, "unavailable": unavailable, "next_pid": None}

    def inspect(self, arguments):
        """PID 재사용이 감지되면 다른 프로세스 자료를 합쳐 반환하지 않는다."""
        pid = arguments["pid"]
        if type(pid) is not int or pid < 1:
            raise ValueError("pid must be a positive integer")
        root = Path("/proc") / str(pid)
        result = self._stat(pid)
        if "expected_start_ticks" in arguments and arguments["expected_start_ticks"] != result["start_ticks"]:
            raise ValueError("PID identity changed; list processes again")
        unavailable = {}

        def observe(name, function):
            try:
                result[name] = function()
            except OSError as error:
                unavailable[name] = type(error).__name__

        observe("cwd", lambda: os.readlink(root / "cwd"))
        observe("executable", lambda: os.readlink(root / "exe"))
        observe("argv", lambda: [part.decode("utf-8", errors="replace")
                                 for part in (root / "cmdline").read_bytes().split(b"\0") if part])
        for name, fd in (("stdin", "0"), ("stdout", "1"), ("stderr", "2")):
            observe(name, lambda fd=fd: os.readlink(root / "fd" / fd))
        if arguments.get("include_files", False):
            def files():
                values = []
                for path in sorted((root / "fd").iterdir(), key=lambda p: int(p.name)):
                    try:
                        values.append({"fd": int(path.name), "target": os.readlink(path)})
                    except OSError as error:
                        values.append({"fd": int(path.name), "unavailable": type(error).__name__})
                return values
            observe("files", files)
        if self._stat(pid)["start_ticks"] != result["start_ticks"]:
            raise ValueError("PID identity changed during observation")
        result.update(observed_at=datetime.now(timezone.utc).isoformat(), unavailable=unavailable)
        return result
