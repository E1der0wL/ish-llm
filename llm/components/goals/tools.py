"""목표 읽기와 승인 대상 변경 Tool. 실행 권한·영수증은 ToolExecutor가 소유한다."""

from llm.core.tool_contracts import ToolContract
from llm.components.tools import Tool, ToolRegistry
from llm.core.tool_contracts import ToolEffect, ToolIsolation
from .tool_values import GoalToolArguments, GoalToolContract


def goal_tools(data, *, write=GoalToolContract.READ_ONLY):
    identifier = GoalToolArguments.identifier_schema()
    version = GoalToolArguments.text_schema()

    def owner():
        from llm.services.runtime.tools import current_tool_call
        call = current_tool_call()
        if call is GoalToolContract.NO_CALL or call.project_id != data.project.id or not call.run_id:
            raise ValueError(GoalToolContract.MISSING_OWNER)
        return call

    async def listing(args):
        records = await data.alist(session_id=owner().session_id, **args)
        return {key: await data.asnapshot(key) for key in records}

    async def read(args):
        call = owner()
        records = await data.alist(session_id=call.session_id)
        selected = GoalToolArguments(args)
        if selected.identifier not in records:
            raise ValueError(GoalToolContract.OUTSIDE_SCOPE)
        return await data.asnapshot(selected.identifier)

    async def progress(args):
        selected = GoalToolArguments(args)
        await read(GoalToolArguments.selected(selected.identifier))
        return await data.aupdate(selected.identifier, selected.changes, expected_version=selected.expected_version)

    async def link(args):
        await read(GoalToolArguments.selected(GoalToolArguments(args).identifier))
        call = owner()
        return await data.alink_run(session_id=call.session_id, run_id=call.run_id, **args)

    def item(name, description, properties, required, handler, write=GoalToolContract.READ_ONLY):
        return Tool(name, description, GoalToolContract.parameters(properties, required), handler,
                    contract=ToolContract(
                        revision=GoalToolContract.REVISION, effect=ToolEffect.UNKNOWN if write else ToolEffect.READ_ONLY,
                        isolation=ToolIsolation.NONE, approval_required=write,
                        operation_key_required=GoalToolContract.OPERATION_KEY_REQUIRED, retry_fields=()))
    reads = (
        item(GoalToolContract.LIST_NAME, GoalToolContract.LIST_DESCRIPTION,
             GoalToolArguments.listing_schema(), [], listing),
        item(GoalToolContract.READ_NAME, GoalToolContract.READ_DESCRIPTION,
             {GoalToolArguments.IDENTIFIER: identifier}, [GoalToolArguments.IDENTIFIER], read),
    )
    writes = (
        item(GoalToolContract.PROGRESS_NAME, GoalToolContract.PROGRESS_DESCRIPTION,
             {GoalToolArguments.IDENTIFIER: identifier, GoalToolArguments.EXPECTED_VERSION: version,
              GoalToolArguments.CHANGES: GoalToolArguments.changes_schema()},
             [GoalToolArguments.IDENTIFIER, GoalToolArguments.EXPECTED_VERSION, GoalToolArguments.CHANGES],
             progress, GoalToolContract.WRITES),
        item(GoalToolContract.LINK_NAME, GoalToolContract.LINK_DESCRIPTION,
             {GoalToolArguments.IDENTIFIER: identifier, GoalToolArguments.EXPECTED_VERSION: version,
              GoalToolArguments.RELATION: GoalToolArguments.text_schema()},
             [GoalToolArguments.IDENTIFIER, GoalToolArguments.EXPECTED_VERSION, GoalToolArguments.RELATION],
             link, GoalToolContract.WRITES),
    )
    return ToolRegistry((*reads, *writes) if write else reads)
