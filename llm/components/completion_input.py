"""Model input provenance, complete exchanges and explicit repair validation."""

import inspect
from llm.core.contracts import Diagnostic
from llm.core.model_protocol import ModelMessage, ModelRole, copy_model
from .completion_values import CompletionMessage, CompletionRecovery, CompletionRepair


class CompletionInput:
    """Validate transformations without acquiring resources or writing evidence."""

    NO_REPAIR = None
    NO_PREPARATION = None
    NO_TOOL_RESULT = None
    FIRST_INDEX = 0
    NO_REMOVED_MESSAGES = 0
    NEXT_POSITION = 1
    IGNORED_RESULT_TEXT = ""
    INVALID_CURRENT_TEXT = "Text completion processing requires a string current instruction"
    INVALID_RECOVERY_INPUT = "Recovery requires the current user input"
    INVALID_REPAIR = "Completion recovery requires CompletionRepair or None"
    INVALID_REPLACEMENT = "Recovery can only replace declared reference bodies"
    REPAIR_MUST_REDUCE = "Context repair must reduce reference content"
    REPAIR_REMOVED_PROTECTED_INPUT = "Recovery budget processing removed current input or Tool structure"
    TOOL_SELECTION_CHANGED = "Completion processor cannot change Tool selection"
    INVALID_MESSAGE = "Completion messages require CompletionMessage values"
    INVALID_SOURCE = "Unknown or duplicate completion message source"
    ROLE_CHANGED = "Completion processor cannot change an original message role"
    CURRENT_MESSAGE_REMOVED = "Completion processor must preserve the current user message"
    CURRENT_BODY_CHANGED = "Completion processor must preserve the current instruction body"
    INSTRUCTIONS_CHANGED = "Completion processor must preserve applied instructions"
    ORDER_CHANGED = "Completion processor must preserve original message order"
    PARTIAL_TURN = "Completion processor must remove whole prior turns"
    MISSING_COMPACTED_PREFIX = "Compaction requires a removed transcript prefix"
    PARTIAL_TOOL_EXCHANGE = "Compaction requires complete Tool exchanges"
    COMPACTION_IDS_CHANGED = "Compaction source IDs do not match completed exchanges"
    MISSING_COMPACTION_SUMMARY = "Compaction must preserve a summary in the current input"
    ACTIVE_TRANSCRIPT_CHANGED = "Completion processor cannot alter the active tool transcript structure"
    ACTIVE_TOOL_CHANGED = "Completion processor cannot alter active tool calls or identifiers"

    @staticmethod
    def instruction_text(message):
        content = message.content
        if not isinstance(content, str):
            raise ValueError(CompletionInput.INVALID_CURRENT_TEXT)
        return content

    @staticmethod
    async def recover(request, diagnostic, attempt, *, current_index, decide, prepare=NO_PREPARATION):
        """Repair this prepared invocation, without touching persistent input/receipts.

        Current instructions (including embedded data without an explicit separate
        reference boundary) stay whole. Earlier bodies and Tool result bodies can
        be shortened; call IDs/arguments/order and all other fields stay exact.
        """
        original = copy_model(request)
        messages = original.messages
        if (
            not CompletionInput.FIRST_INDEX <= current_index < len(messages)
            or messages[current_index].role != ModelRole.USER
        ):
            raise ValueError(CompletionInput.INVALID_RECOVERY_INPUT)
        CompletionInput.instruction_text(messages[current_index])
        editable = tuple(
            i
            for i, message in enumerate(messages)
            if (isinstance(message.content, str) or message.result is not CompletionInput.NO_TOOL_RESULT)
            and message.role not in (ModelRole.SYSTEM, ModelRole.DEVELOPER)
            and (i < current_index or message.role == ModelRole.TOOL)
        )
        if not editable:
            return CompletionInput.NO_REPAIR
        selected = decide(
            CompletionRecovery(
                Diagnostic.from_dict(diagnostic.to_dict()), copy_model(original), attempt, editable
            )
        )
        if inspect.isawaitable(selected):
            selected = await selected
        if selected is CompletionInput.NO_REPAIR:
            return CompletionInput.NO_REPAIR
        if not isinstance(selected, CompletionRepair) or not isinstance(selected.replacements, dict):
            raise TypeError(CompletionInput.INVALID_REPAIR)
        candidate = copy_model(original)
        for index, content in selected.replacements.items():
            if (
                type(index) is not int
                or index not in editable
                or not isinstance(content, str)
                or not content.strip()
            ):
                raise ValueError(CompletionInput.INVALID_REPLACEMENT)
            candidate.messages[index] = candidate.messages[index].with_text(content)
        if sum(len(m.text) for m in candidate.messages) >= sum(len(m.text) for m in messages):
            raise ValueError(CompletionInput.REPAIR_MUST_REDUCE)
        tail = copy_model(candidate.messages[current_index:])
        instructions = [m for m in messages if m.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)]
        if prepare is not CompletionInput.NO_PREPARATION:
            candidate = await prepare(candidate)
        # Budget preparation may drop whole prior turns, never the protected tail.
        if (
            candidate.messages[-len(tail) :] != tail
            or [m for m in candidate.messages if m.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)]
            != instructions
            or candidate.parameters != original.parameters
            or candidate.tools != original.tools
            or candidate.tool_mode != original.tool_mode
        ):
            raise ValueError(CompletionInput.REPAIR_REMOVED_PROTECTED_INPUT)
        return candidate

    @staticmethod
    def validate(request, original, invocation, current_id):
        current = request.to_model()
        current.validate_exchanges()
        if current.tools != invocation.tools or current.tool_mode != invocation.tool_mode:
            raise ValueError(CompletionInput.TOOL_SELECTION_CHANGED)
        originals = {m.source_id: m.message for m in original if m.source_id is not CompletionMessage.NO_SOURCE}
        seen = set()
        for message in request.messages:
            if not isinstance(message, CompletionMessage) or not isinstance(message.message, ModelMessage):
                raise TypeError(CompletionInput.INVALID_MESSAGE)
            if message.source_id is not CompletionMessage.NO_SOURCE:
                if message.source_id not in originals or message.source_id in seen:
                    raise ValueError(CompletionInput.INVALID_SOURCE)
                seen.add(message.source_id)
                if message.message.role != originals[message.source_id].role:
                    raise ValueError(CompletionInput.ROLE_CHANGED)
        if current_id not in seen:
            raise ValueError(CompletionInput.CURRENT_MESSAGE_REMOVED)
        current = next(m.message for m in request.messages if m.source_id == current_id)
        before = originals[current_id]
        # Existing processors can append explicitly configured reference context;
        # they cannot replace the original instruction with a summary or an ID.
        if not CompletionInput.instruction_text(current).startswith(CompletionInput.instruction_text(before)):
            raise ValueError(CompletionInput.CURRENT_BODY_CHANGED)
        protected = [m.message for m in original if m.message.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)]
        supplied = [
            m.message for m in request.messages if m.message.role in (ModelRole.SYSTEM, ModelRole.DEVELOPER)
        ]
        candidates = iter(supplied)
        if any(not any(value == candidate for candidate in candidates) for value in protected):
            raise ValueError(CompletionInput.INSTRUCTIONS_CHANGED)
        order = [m.source_id for m in original if m.source_id in seen]
        if order != [m.source_id for m in request.messages if m.source_id is not CompletionMessage.NO_SOURCE]:
            raise ValueError(CompletionInput.ORDER_CHANGED)
        group = []
        for message in original:
            if message.message.role == ModelRole.USER and not message.continuation:
                kept = [m.source_id in seen for m in group]
                if any(kept) and not all(kept):
                    raise ValueError(CompletionInput.PARTIAL_TURN)
                group = []
            if message.source_id == current_id:
                break
            if message.source_id is not CompletionMessage.NO_SOURCE and message.message.role not in (
                ModelRole.SYSTEM,
                ModelRole.DEVELOPER,
            ):
                group.append(message)
        # 현재 요청 이후의 Tool 호출/결과는 쌍과 순서를 유지한다. 결과 본문의 압축만 허용한다.
        index = next(i for i, m in enumerate(original) if m.source_id == current_id)
        tail = original[index + CompletionInput.NEXT_POSITION :]
        request_index = next(i for i, m in enumerate(request.messages) if m.source_id == current_id)
        actual = request.messages[request_index + CompletionInput.NEXT_POSITION :]
        if request.compacted_tool_calls:
            removed = len(tail) - len(actual)
            if removed <= CompletionInput.NO_REMOVED_MESSAGES:
                raise ValueError(CompletionInput.MISSING_COMPACTED_PREFIX)
            prefix, ids, pending = tail[:removed], [], set()
            for message in prefix:
                value = message.message
                if value.role == ModelRole.ASSISTANT and value.calls and not pending:
                    pending = {c.id for c in value.calls}
                    ids.extend(c.id for c in value.calls)
                elif value.result is not CompletionInput.NO_TOOL_RESULT and value.result.call_id in pending:
                    pending.remove(value.result.call_id)
                else:
                    raise ValueError(CompletionInput.PARTIAL_TOOL_EXCHANGE)
            if pending or tuple(ids) != request.compacted_tool_calls:
                raise ValueError(CompletionInput.COMPACTION_IDS_CHANGED)
            if request.messages[request_index].message.content == original[index].message.content:
                raise ValueError(CompletionInput.MISSING_COMPACTION_SUMMARY)
            tail = tail[removed:]
        if len(tail) != len(actual):
            raise ValueError(CompletionInput.ACTIVE_TRANSCRIPT_CHANGED)
        for before, after in zip(tail, actual):
            left, right = copy_model(before.message), copy_model(after.message)
            if left.result is not CompletionInput.NO_TOOL_RESULT:
                left = left.with_text(CompletionInput.IGNORED_RESULT_TEXT)
                right = right.with_text(CompletionInput.IGNORED_RESULT_TEXT)
            if before.source_id != after.source_id or before.result_ref != after.result_ref or left != right:
                raise ValueError(CompletionInput.ACTIVE_TOOL_CHANGED)
