"""Memory-owned derived Tool views; Data owns immutable-by-reference source files.

The selected policy controls processing. Tool effects/receipts and the original
Loop transcript are never changed. MCP results use the same completed Tool path.
"""

from llm.core.model_protocol import ModelRole

from contextlib import aclosing
import json

from llm.components.summarization import SummaryResult
from llm.services.infrastructure.storage import revision_token
from .compaction import Compactions, CompactionStopped, summarize_request
from .compaction_values import CompactionOptions
from .configuration import MemoryProcessingConfig
from .tool_values import MemoryToolDefinition
from .compaction_records import CompactionState
from .tool_result_values import (ToolSummaryExecution, ToolSummaryIdentity, ToolSummaryOutcome,
                                 ToolSummaryObservation, ToolSummaryView)


async def prepare_tool_results(session, request):
    config, context, data = session.config, session.context, session.data
    selected = MemoryProcessingConfig(config)
    mode = selected.tool_result_mode
    if mode is MemoryProcessingConfig.ABSENT:
        return
    names = {call.id: call.name for item in request.messages
             for call in item.message.calls}
    for index, message in enumerate(request.messages):
        value = message.message
        text = value.text
        if value.role != ModelRole.TOOL or not isinstance(text, str) or len(text) <= selected.tool_result_chars:
            continue
        # Rereading a slice is already explicit; never replace the reader's result
        # with another reference and create a recursive read/summary loop.
        if names.get(value.result.call_id) == MemoryToolDefinition.Name.READ_SOURCE:
            continue
        options = CompactionOptions.from_tool_processing(selected, mode)
        identity = revision_token(ToolSummaryIdentity.create(scope=context.checkpoint_scope, call_id=value.result.call_id,
            text=text, mode=mode, options=options, destination=selected.tool_data_record))
        state = await data._async_call(data._prepare_tool_summary, context.session.id, identity,
            text, options, selected.tool_data_record, mode, value.result.call_id)
        pending = CompactionState(state).request
        if pending.status == Compactions.PENDING:
            claim = await data._async_call(data._claim_compaction, context.session.id, ToolSummaryExecution.SCOPE,
                pending.identifier, context.run.id, tool_id=identity)
            if claim:
                outcome = ToolSummaryOutcome()
                async def action(result, source):
                    summary = SummaryResult()
                    try:
                        async with aclosing(summarize_request(session, pending.to_record(), text, ToolSummaryExecution.EMPTY_SUMMARY, summary,
                                                             scope=ToolSummaryExecution.SCOPE, tool_id=identity)) as events:
                            async for event in events:
                                yield event
                    except CompactionStopped:
                        result.update(ToolSummaryObservation.cancelled())
                        return
                    outcome.record(summary)
                    result.update(ToolSummaryObservation.progress(identity, summary))
                try:
                    async with aclosing(session._step(ToolSummaryExecution.STEP, action)) as events:
                        async for event in events:
                            yield event
                    applied = outcome.applied
                    status = Compactions.APPLIED if applied else Compactions.PARTIAL if outcome else Compactions.FAILED
                    await data._async_call(data._finish_compaction, context.session.id, ToolSummaryExecution.SCOPE, pending.identifier,
                        status, applied, ToolSummaryExecution.NO_ERROR, run_id=context.run.id, tool_id=identity)
                except CompactionStopped:
                    pass
                except BaseException:
                    await data._async_call(data._finish_compaction, context.session.id, ToolSummaryExecution.SCOPE, pending.identifier,
                        Compactions.INTERRUPTED, ToolSummaryExecution.NO_APPLIED, ToolSummaryExecution.RESUME_REQUIRED, run_id=context.run.id, tool_id=identity)
                    raise
            state = await data._async_call(data._compaction_state, context.session.id, ToolSummaryExecution.SCOPE, tool_id=identity)
        if CompactionState(state).has_applied:
            # Publication is durable before the model-only copy is changed.
            result = ToolSummaryView.create(CompactionState(state), identifier=identity,
                                            text=text, call_id=value.result.call_id)
            request.messages[index] = message.with_text(json.dumps(result, ensure_ascii=ToolSummaryExecution.ENSURE_ASCII))
