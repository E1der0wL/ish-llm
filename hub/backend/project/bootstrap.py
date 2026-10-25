"""Hub의 최초 작업 공간 선택 정책. llm에는 일반 Project CRUD만 요구한다."""

from hub.backend.execution.engine_selection import HubEngineIdentity

import asyncio
import fcntl
import os
from hub.config.store import WorkspaceJSONStore
from hub.config.preset_records import PresetProject
from llm.providers.request_values import LiteLLMParameters
from llm.engines.loop.configuration import LoopParameters
from llm.services.schema import ProjectSchemaDescription

from llm.core.models import ProjectConfig
from .defaults import CONVERSATION_STORAGE, create_project


class ProjectStartup:
    LOCK_FILE = "startup.lock"
    OPEN_FLAGS = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    FILE_MODE = 0o600
    LOCK_FLAGS = fcntl.LOCK_EX | fcntl.LOCK_NB
    LOCK_POLL_SECONDS = 0.01
    CREATE_PARENTS = True
    EXISTING_DIRECTORY_OK = True
    TITLE = "Hub"
    UNSPECIFIED_ANCHOR = None

    @classmethod
    def directory(cls, workspace):
        return WorkspaceJSONStore.directory(workspace)

    @classmethod
    def open(cls, directory):
        directory.mkdir(parents=cls.CREATE_PARENTS, exist_ok=cls.EXISTING_DIRECTORY_OK)
        return os.open(directory / cls.LOCK_FILE, cls.OPEN_FLAGS, cls.FILE_MODE)


async def select_or_create(backend, config):
    """기존 활성 Project 중 가장 오래된 것을 선택하고, 없을 때만 Hub 템플릿으로 생성한다.

    Hub 프로세스끼리 최초 생성을 직렬화한다. 생성 직후 종료되더라도 다음 호출은
    저장된 Project를 조회하므로 별도의 default-project 포인터나 복구 상태가 없다.
    """
    directory = ProjectStartup.directory(config.workspace)
    descriptor = ProjectStartup.open(directory)
    try:
        while True:
            try:
                fcntl.flock(descriptor, ProjectStartup.LOCK_FLAGS)
                break
            except BlockingIOError:
                # 잠금 대기만 양보한다. 실행 timeout/retry 정책이 아니다.
                await asyncio.sleep(ProjectStartup.LOCK_POLL_SECONDS)
        projects = await backend.projects.alist()
        if projects:
            records = [(await project.aget_data(), project) for project in projects]
            return min(records, key=lambda item: (item[0].created_at, item[0].id))[1]
        from ...config.presets import PresetStore

        anchor = (
            config.workspace
            if config._workspace_anchor is ProjectStartup.UNSPECIFIED_ANCHOR
            else config._workspace_anchor
        )
        preset = await asyncio.to_thread(PresetStore(anchor).selected_project)
        project_config = ProjectConfig.from_dict(
            ProjectConfig.merge(PresetProject(preset).config, config.project_config)
            if preset
            else config.project_config
        )
        supplied = LiteLLMParameters.connection_options(
            model=config.model, api_base=config.api_base
        )
        if supplied:
            # Title generation resolves the current source settings at execution
            # time; copying them here would leave a stale, independent title model.
            parameters = project_config.parameter_targets.engine(HubEngineIdentity.LOOP)
            values = parameters.config
            LoopParameters(values).update_completion(supplied)
            project_config.set_engine_parameters(
                HubEngineIdentity.LOOP, parameters.with_config(values).to_dict()
            )
        return await create_project(
            backend,
            ProjectStartup.TITLE,
            config=project_config,
            components=(
                PresetProject(preset).components
                if preset
                else ProjectSchemaDescription.component_names(
                    backend.describe_project_config()
                )
            ),
            conversation_storage=(
                PresetProject(preset).conversation_storage
                if preset
                else CONVERSATION_STORAGE
            ),
            agent_id=config.agent_id,
            agent_definition=config.agent_definition,
        )
    finally:
        os.close(descriptor)
