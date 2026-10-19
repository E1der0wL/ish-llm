"""Memory-owned derived Tool views; Data owns immutable-by-reference source files.

The selected policy controls processing. Tool effects/receipts and the original
Loop transcript are never changed. MCP results use the same completed Tool path.
"""

from contextlib import aclosing
from copy import deepcopy
import json

from llm.components.summarization import SummaryResult
from llm.services.infrastructure.storage import revision_token
from .compaction import CompactionStopped, summarize_request


async def prepare_tool_results(session, request):
    config, context, data = session.config, session.context, session.data
    mode = config.get("tool_result_mode")
    if mode is None:
        return
    names = {call.id: call.name for item in request.messages
             for call in item.message.calls}
    for index, message in enumerate(request.messages):
        value = message.message
        text = value.text
        if value.result is None or not isinstance(text, str) or len(text) <= config["tool_result_chars"]:
            continue
        # Rereading a slice is already explicit; never replace the reader's result
        # with another reference and create a recursive read/summary loop.
        if names.get(value.result.call_id) == "memory_read_source":
            continue
        options = {key: deepcopy(config[key]) for key in
            ("completion", "provider", "summary_chars", "model_input_chars", "max_summary_calls", "timeout_seconds", "max_output_chars") if key in config}
        if mode == "summary":
            options["prompt"] = config["tool_summary_prompt"]
        identity = revision_token({"scope": context.checkpoint_scope, "call_id": value.result.call_id,
            "text": text, "mode": mode, "options": options, "destination": config["tool_data"]})
        state = await data._async_call(data._prepare_tool_summary, context.session.id, identity,
            text, options, config["tool_data"], mode, value.result.call_id)
        pending = state["request"]
        if pending["status"] == "pending":
            claim = await data._async_call(data._claim_compaction, context.session.id, None,
                pending["id"], context.run.id, tool_id=identity)
            if claim:
                outcome = {}
                async def action(result, source):
                    summary = SummaryResult()
                    try:
                        async with aclosing(summarize_request(session, pending, text, "", summary,
                                                             scope=None, tool_id=identity)) as events:
                            async for event in events:
                                yield event
                    except CompactionStopped:
                        result.update(cancelled=True)
                        return
                    outcome.update(complete=summary.complete, text=summary.text)
                    result.update(request_id=identity, completed=summary.complete, offset=summary.offset,
                                  blocked=summary.blocked)
                try:
                    async with aclosing(session._step("Tool result summary", action)) as events:
                        async for event in events:
                            yield event
                    applied = {"summary": outcome["text"]} if outcome.get("complete") else None
                    status = "applied" if applied else "partial" if outcome else "failed"
                    await data._async_call(data._finish_compaction, context.session.id, None, pending["id"],
                        status, applied, None, run_id=context.run.id, tool_id=identity)
                except CompactionStopped:
                    pass
                except BaseException:
                    await data._async_call(data._finish_compaction, context.session.id, None, pending["id"],
                        "interrupted", None, "Explicit summary resume required", run_id=context.run.id, tool_id=identity)
                    raise
            state = await data._async_call(data._compaction_state, context.session.id, None, tool_id=identity)
        if "applied" in state:
            # Publication is durable before the model-only copy is changed.
            result = {"reference": {"id": identity, "tool": "memory_read_source",
                "revision": state["request"]["reference"]["revision"]},
                "original_chars": len(text), "source_call_id": value.result.call_id,
                "notice": "Derived reference data; original Tool result is unchanged.", **state["applied"]}
            request.messages[index] = message.with_text(json.dumps(result, ensure_ascii=False))
