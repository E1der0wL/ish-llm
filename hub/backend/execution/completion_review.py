"""Application completion evidence, enforced through the backend review hook."""

import json
from llm.components.review import CompletionReview, ReviewAction
from llm.core.model_protocol import ModelRole
from llm.core.schema import SchemaKeyword, SchemaType


class TaskCompletion:
    NAME = "hub_task_result"
    REVISION = "hub-task-evidence-v2"
    OUTCOME = "outcome"
    SUMMARY = "summary"
    EVIDENCE = "evidence"
    COMPLETED = "completed"
    INFORMATION = "information"
    BLOCKED = "blocked"
    OUTCOMES = (COMPLETED, INFORMATION, BLOCKED)
    INSTRUCTION = (
        "Before a final answer, use hub_task_result to review the user's completion conditions. "
        "For an action request, do the actual work with tools and report completed with the IDs "
        "of the tool calls that performed/verified it. Advice or a code example is not a file change. "
        "Use information only when the user requested explanation or review without changes. "
        "Use blocked when a concrete permission, environment or missing-information limit prevents "
        "completion; explain the limit, never claim success. Do not repeat effects for review. "
        "Respect approval, cancellation and all execution budgets."
    )
    MISSING = "Review the completion conditions with hub_task_result before answering. Perform requested work rather than only describing it; report a concrete blocker if it cannot be done."
    INVALID = "A completed action needs evidence from this request's actual tool results. Do not invent call IDs or cite earlier turns."
    INVALID_REPORT = "Provide a valid completion report with outcome, nonempty summary, and evidence call IDs."
    STALE = "Tool work continued after the completion report. Verify the latest result and record a fresh hub_task_result; do not repeat completed effects."
    ACCEPTED_FEEDBACK = ""
    LAST_REPORT = -1
    ENSURE_ASCII = False
    PROPERTIES = {
        OUTCOME: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.ENUM: list(OUTCOMES)},
        SUMMARY: {SchemaKeyword.TYPE: SchemaType.STRING, SchemaKeyword.MIN_LENGTH: 1},
        EVIDENCE: {SchemaKeyword.TYPE: SchemaType.ARRAY,
                   SchemaKeyword.ITEMS: {SchemaKeyword.TYPE: SchemaType.STRING},
                   SchemaKeyword.UNIQUE_ITEMS: True},
    }

    @classmethod
    def review(cls, observation):
        if not any(tool.name == cls.NAME for tool in observation.tools):
            # Explicit Agent tool selection can opt out of the Hub report tool.
            return CompletionReview(ReviewAction.ACCEPT, cls.ACCEPTED_FEEDBACK)
        # Loop builds prior turns as plain messages. Its original (uncompacted)
        # transcript retains the current execution's actual call/result pairs.
        calls = {call.id: call for m in observation.original_messages for call in m.calls}
        results = {m.result.call_id: m.result for m in observation.original_messages
                   if m.role == ModelRole.TOOL and m.result.call_id in calls}
        reports = [call for call in calls.values()
                   if call.name == cls.NAME and call.id in results]
        if not reports:
            return CompletionReview(ReviewAction.CONTINUE, cls.MISSING)
        report = reports[cls.LAST_REPORT].arguments
        if (set(report) != {cls.OUTCOME, cls.SUMMARY, cls.EVIDENCE}
                or report[cls.OUTCOME] not in cls.OUTCOMES
                or not isinstance(report[cls.SUMMARY], str) or not report[cls.SUMMARY].strip()
                or not isinstance(report[cls.EVIDENCE], list)
                or any(not isinstance(item, str) for item in report[cls.EVIDENCE])
                or len(set(report[cls.EVIDENCE])) != len(report[cls.EVIDENCE])):
            return CompletionReview(ReviewAction.CONTINUE, cls.INVALID_REPORT)
        evidence = report[cls.EVIDENCE]
        if ((report[cls.OUTCOME] == cls.COMPLETED and not evidence)
                or any(identifier not in results or calls[identifier].name == cls.NAME
                       for identifier in evidence)):
            return CompletionReview(ReviewAction.CONTINUE, cls.INVALID)
        # A report is an observation after the cited results, not a promise about
        # a later call in the same model response. Reuse receipts on continuation.
        result_order = [m.result.call_id for m in observation.original_messages
                        if m.role == ModelRole.TOOL and m.result.call_id in calls]
        if result_order[-1] != reports[cls.LAST_REPORT].id:
            return CompletionReview(ReviewAction.CONTINUE, cls.STALE)
        return CompletionReview(ReviewAction.ACCEPT, cls.ACCEPTED_FEEDBACK)

    @classmethod
    def tool(cls):
        from ..tools.resources import tool
        async def record(arguments):
            # The backend owns schema validation and persists this tool receipt.
            return json.dumps(arguments, ensure_ascii=cls.ENSURE_ASCII)
        return tool(cls.NAME, cls.INSTRUCTION, cls.PROPERTIES,
                    [cls.OUTCOME, cls.SUMMARY, cls.EVIDENCE], record)
