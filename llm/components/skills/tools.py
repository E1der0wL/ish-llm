"""Run 시작 시 선택된 Project의 Skill 스냅샷을 읽는 Tool. 실행·권한은 소유하지 않는다."""

from copy import deepcopy

from llm.core.tool_contracts import ToolContract
from llm.components.tools import Tool, ToolRegistry
from llm.services.infrastructure.storage import revision_token
from llm.core.tool_contracts import ToolEffect, ToolIsolation
from .views import (SkillListRequest, SkillReadRequest, SkillResourceRequest,
                    SkillPage, SkillReadView, SkillFileView)
from .records import SkillRecord as Record, SkillSnapshot


class SkillTool:
    APPROVAL_REQUIRED = False
    OPERATION_KEY_REQUIRED = False
    UNAVAILABLE = "Skill is unavailable in this Run"
    UNAVAILABLE_NAMED = "Skill is unavailable in this Run: {identifier}"
    STALE = "Skill revision changed; list the available skills again"


def skill_tools(records: dict, *, read_resource=None, read_file=None) -> ToolRegistry:
    """목록은 요약만, 본문은 선택한 ID만 반환한다. 지침의 실행을 보장하지 않는다."""
    records = deepcopy(records)
    snapshots = {name: SkillSnapshot(name, value) for name, value in records.items()}
    contract = ToolContract(
        revision=revision_token(records), effect=ToolEffect.READ_ONLY,
        isolation=ToolIsolation.NONE, approval_required=SkillTool.APPROVAL_REQUIRED,
        operation_key_required=SkillTool.OPERATION_KEY_REQUIRED, retry_fields=())

    async def listing(arguments):
        request = SkillListRequest.from_dict(arguments)
        return SkillPage.select(snapshots.values(), request).to_dict()

    async def read(arguments):
        request = SkillReadRequest.from_dict(arguments)
        if request.identifier not in snapshots:
            raise ValueError(SkillTool.UNAVAILABLE_NAMED.format(identifier=request.identifier))
        snapshot = snapshots[request.identifier]
        if request.expected_revision is not None and request.expected_revision != snapshot.revision:
            raise ValueError(SkillTool.STALE)
        return SkillReadView.from_snapshot(snapshot).to_dict()

    async def resource(arguments):
        request = SkillResourceRequest.from_dict(arguments)
        if request.identifier not in snapshots:
            raise ValueError(SkillTool.UNAVAILABLE)
        snapshot = snapshots[request.identifier]
        if request.path is not None:
            path = Record.resource_path(request.path)
            evidence = snapshot.file(path)
            if read_file is None:
                raise ValueError(Record.UNAVAILABLE_FILE)
            content = await read_file(snapshot.id, path, expected_revision=snapshot.revision)
            return SkillFileView.describe(path, content, evidence)
        return await read_resource(snapshot.id, request.index, expected_revision=snapshot.revision)

    tools = [
        Tool(SkillListRequest.NAME, SkillListRequest.DESCRIPTION,
             SkillListRequest.schema(), listing, contract=contract),
        Tool(SkillReadRequest.NAME, SkillReadRequest.DESCRIPTION,
             SkillReadRequest.schema(), read, contract=contract),
    ]
    if read_resource is not None:
        tools.append(Tool(SkillResourceRequest.NAME, SkillResourceRequest.DESCRIPTION,
                          SkillResourceRequest.schema(),
                          resource, contract=contract))
    return ToolRegistry(tuple(tools))
