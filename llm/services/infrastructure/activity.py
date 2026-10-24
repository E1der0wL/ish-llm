"""Project lifecycle의 after-commit 파생 JSONL 인덱스. 호출자는 workspace 잠금을 소유한다.

Run/Step 원본을 재해석하거나 복구하지 않는다. 쓰기는 best effort, 조회 오류는 명시적이다.
"""

from llm.core.models import ResourceKind

import json
import os
import stat
import warnings
from typing import Optional

from llm.core.contracts import ProjectActivityEvent, ResourceRef
from llm.core.paths import ProjectPaths
from llm.services.infrastructure.storage import append_bytes, reject_links
from llm.services.infrastructure.transactions import after_commit


class ActivityIndex:
    PARENT = ".."
    FILE = "activity.jsonl"
    INVALID_PATH = "Activity requires owned Project paths"
    INVALID_FILE = "Activity must be a regular file"
    WARNING = "llm could not write Project activity index"
    WARNING_DEPTH = 2
    MISSING_ROOT = "Project root must already exist"
    BINARY_READ = "rb"
    ZERO = 0
    BYTE = 1
    LINE_END = b"\n"
    EMPTY_BYTES = b""
    BLOCK_BYTES = 8192
    INCOMPLETE_TAIL = "Incomplete activity tail"
    ALLOW_NAN = False
    ENSURE_ASCII = False
    ENCODING = "utf-8"
    ABSENT = None
    INCOMPLETE_INDEX = "Incomplete Project activity index"
    NEWEST_FIRST = True
    INVALID_LIMIT = "Activity limit must be a nonnegative integer or None"
    INVALID_ORDER = "newest_first must be boolean"
    INVALID_ENTRY = "Invalid Project activity entry"


def _path(paths):
    if not isinstance(paths, ProjectPaths) or ActivityIndex.PARENT in paths.root.parts:
        raise ValueError(ActivityIndex.INVALID_PATH)
    path = reject_links(paths.logs / ActivityIndex.FILE)
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(ActivityIndex.INVALID_FILE)
    return path


def _warn():
    # warnings-as-errors 또는 사용자 warning handler도 원본 commit 결과를 바꾸면 안 된다.
    try:
        warnings.warn(ActivityIndex.WARNING, RuntimeWarning, stacklevel=ActivityIndex.WARNING_DEPTH)
    except Exception:
        pass


def _append(paths, value):
    try:
        path = _path(paths)
        if not paths.root.is_dir():
            raise ValueError(ActivityIndex.MISSING_ROOT)
        if path.exists():
            with path.open(ActivityIndex.BINARY_READ) as stream:
                stream.seek(ActivityIndex.ZERO, os.SEEK_END)
                if stream.tell():
                    stream.seek(-ActivityIndex.BYTE, os.SEEK_END)
                    if stream.read(ActivityIndex.BYTE) != ActivityIndex.LINE_END:
                        # 불완전한 tail에 새 이벤트를 붙이거나 자동 복구하지 않는다.
                        raise ValueError(ActivityIndex.INCOMPLETE_TAIL)
        append_bytes(
            path,
            json.dumps(
                value.to_dict(), ensure_ascii=ActivityIndex.ENSURE_ASCII, allow_nan=ActivityIndex.ALLOW_NAN
            ).encode(ActivityIndex.ENCODING)
            + ActivityIndex.LINE_END,
        )
    except Exception:
        _warn()


def activity_event(
    project,
    run,
    event: str,
    *,
    time: str,
    status: str,
    code: Optional[str] = ActivityIndex.ABSENT,
    step_id: Optional[str] = ActivityIndex.ABSENT,
) -> None:
    """저장 경계에서 예약한다. callback 등록·직렬화·쓰기 실패 모두 실행 결과와 격리한다."""
    try:
        source = ResourceRef(
            ResourceKind.STEP if step_id is not ActivityIndex.ABSENT else ResourceKind.RUN,
            step_id or run.id,
            project_id=project.id,
            session_id=run.session_id,
            run_id=run.id,
            step_id=step_id,
        )
        value = ProjectActivityEvent(time, event, source, status, code)
        paths = project.paths
        after_commit(lambda: _append(paths, value))
    except Exception:
        _warn()


def _recent_lines(stream):
    """뒤쪽부터 필요한 줄만 읽는다. 8 KiB는 I/O 블록 크기이며 결과 수 제한이 아니다."""
    stream.seek(ActivityIndex.ZERO, os.SEEK_END)
    position = stream.tell()
    if not position:
        return
    stream.seek(position - ActivityIndex.BYTE)
    if stream.read(ActivityIndex.BYTE) != ActivityIndex.LINE_END:
        raise ValueError(ActivityIndex.INCOMPLETE_INDEX)
    position -= ActivityIndex.BYTE
    pending = ActivityIndex.EMPTY_BYTES
    while position:
        start = max(ActivityIndex.ZERO, position - ActivityIndex.BLOCK_BYTES)
        stream.seek(start)
        lines = (stream.read(position - start) + pending).split(ActivityIndex.LINE_END)
        pending = lines[ActivityIndex.ZERO]
        for line in reversed(lines[ActivityIndex.BYTE :]):
            yield line
        position = start
    yield pending


def read_activity(
    paths: ProjectPaths,
    *,
    limit: Optional[int] = ActivityIndex.ABSENT,
    newest_first: bool = ActivityIndex.NEWEST_FIRST,
) -> tuple[ProjectActivityEvent, ...]:
    """최근 limit건의 분리된 snapshot. None은 전체, False는 선택된 최근 건을 오래된 순서로 표시.

    파일이 없으면 빈 tuple. 손상/읽기 오류는 조회에만 보고하며 파일을 수정하지 않는다.
    정렬 기준은 commit 후 append 순서이다. 여러 프로세스의 wall clock을 재정렬하지 않는다.
    """
    if limit is not ActivityIndex.ABSENT and (type(limit) is not int or limit < ActivityIndex.ZERO):
        raise ValueError(ActivityIndex.INVALID_LIMIT)
    if type(newest_first) is not bool:
        raise ValueError(ActivityIndex.INVALID_ORDER)
    path = _path(paths)
    if limit == ActivityIndex.ZERO or not path.exists():
        return ()
    rows = []
    with path.open(ActivityIndex.BINARY_READ) as stream:
        for line in _recent_lines(stream):
            try:
                value = ProjectActivityEvent.from_dict(json.loads(line))
            except Exception:
                # JSONDecodeError에는 원문 doc이 붙으므로 안전한 조회 오류만 외부로 노출한다.
                raise ValueError(ActivityIndex.INVALID_ENTRY) from ActivityIndex.ABSENT
            rows.append(value)
            if limit is not ActivityIndex.ABSENT and len(rows) >= limit:
                break
    return tuple(rows if newest_first else reversed(rows))
