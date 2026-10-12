"""Hub의 최초 작업 공간 선택 정책. llm에는 일반 Project CRUD만 요구한다."""

import asyncio
import fcntl
import os
from pathlib import Path

from llm.core.models import ProjectConfig
from .defaults import create_project


async def select_or_create(backend, config):
    """기존 활성 Project 중 가장 오래된 것을 선택하고, 없을 때만 Hub 템플릿으로 생성한다.

    Hub 프로세스끼리 최초 생성을 직렬화한다. 생성 직후 종료되더라도 다음 호출은
    저장된 Project를 조회하므로 별도의 default-project 포인터나 복구 상태가 없다.
    """
    directory = Path(config.workspace).expanduser() / ".hub"
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / "startup.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                # 잠금 대기만 양보한다. 실행 timeout/retry 정책이 아니다.
                await asyncio.sleep(0.01)
        projects = await backend.projects.alist()
        if projects:
            records = [(await project.aget_data(), project) for project in projects]
            return min(records, key=lambda item: (item[0].created_at, item[0].id))[1]
        project_config = ProjectConfig(config.project_config)
        supplied = {key: value for key, value in (("model", config.model), ("api_base", config.api_base)) if value is not None}
        if supplied:
            # Title generation resolves the current source settings at execution
            # time; copying them here would leave a stale, independent title model.
            engines = project_config.parameters.setdefault("engines", {})
            engines.setdefault("loop", {}).setdefault("config", {}).setdefault("completion", {}).update(supplied)
        return await create_project(backend, "Hub", config=project_config,
            components=list(backend.describe_project_config()["x-components"]), conversation_storage="file")
    finally:
        os.close(descriptor)
