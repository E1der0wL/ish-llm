"""Application completion evidence, enforced through the backend review hook."""

import json
from llm.components.review import CompletionReview, ReviewAction
from llm.core.model_protocol import ModelRole
from llm.core.schema import SchemaKeyword, SchemaType
from .followup import split_followup


class ConversationalInput:
    """Exact standalone greetings, never a substring classifier for work requests."""

    END_PUNCTUATION = "!?.,~！？，。"
    PHRASES = frozenset(("안녕", "안녕하세요", "안녕하십니까", "반가워", "반갑습니다",
                         "고마워", "고맙습니다", "감사합니다", "hi", "hello", "hey",
                         "thanks", "thank you", "good morning", "good evening"))
    INSTRUCTION = (
        "The current request is only a greeting or thanks. Reply briefly in normal answer "
        "content. Do not plan work, discover resources, or call hub_task_result for this exchange. "
        "Do not start unrelated prior tasks. A later work request still requires actual work."
    )

    @classmethod
    def matches(cls, text):
        return text.strip().casefold().rstrip(cls.END_PUNCTUATION).strip() in cls.PHRASES


class TaskCompletion:
    NAME = "hub_task_result"
    REVISION = "hub-task-evidence-v3"
    OUTCOME = "outcome"
    SUMMARY = "summary"
    EVIDENCE = "evidence"
    COMPLETED = "completed"
    INFORMATION = "information"
    BLOCKED = "blocked"
    OUTCOMES = (COMPLETED, INFORMATION, BLOCKED)
    BACKGROUND = (
        "Hub will add, edit or remove internal Memory/Goal records and prepare refinement proposals "
        "after your response in the background. Do not delay this answer to perform that bookkeeping. "
        "Use those capabilities now only if they are needed to fulfill the current request. "
        "Actions requiring approval, external effects and applying refinement "
        "must remain in the foreground with their normal authorization."
    )
    ROUTING = (
        "First decide only whether this request needs external work or a conversational answer. "
        "For greetings, questions, explanations and chat-only drafts, answer directly without "
        "completion reports, goals, refinement, skills, topic selection or tool-use verification. "
        "Do not evaluate every available capability before answering. If tools are needed to "
        "answer accurately or perform requested work, choose only the relevant tools. For explicit "
        "file or execution requests do the actual work, not instructions for the user to do it. "
        "If you call a tool declaring _hub_risk, assess that action only; assessment is never approval. "
        "Goals are for substantial multi-step work; refinement is only for evidenced reusable "
        "improvements. Use hub_task_result only when explicit completion evidence is useful for "
        "the requested work; it is not a required ritual. Do not repeat completed effects. Existing goals never authorize "
        "unrequested work. Always provide a nonempty answer in normal answer content."
    )
    INSTRUCTION = (
        "Use this optional report when the task needs explicit completion evidence. "
        "For an action request, do the actual work with tools and report completed with the IDs "
        "of the tool calls that performed/verified it. Advice or a code example is not a file change. "
        "For explanation-only requests without Tools, answer directly without a report. "
        "Standalone greetings and thanks need only a brief answer, without a report or unrelated tools. "
        "Use blocked when a concrete permission, environment or missing-information limit prevents "
        "completion; explain the limit, never claim success. Do not repeat effects for review. "
        "Respect approval, cancellation and all execution budgets."
        " Always provide a nonempty final answer in normal answer content; "
        "reasoning, tool reports and next-message suggestions are not a final answer."
    )
    INVALID = "A completed action needs evidence from this request's actual tool results. Do not invent call IDs or cite earlier turns."
    INVALID_REPORT = "Provide a valid completion report with outcome, nonempty summary, and evidence call IDs."
    STALE = "Tool work continued after the completion report. Verify the latest result and record a fresh hub_task_result; do not repeat completed effects."
    EMPTY_ANSWER = (
        "The final answer content is empty. Write a concise answer to the user in normal "
        "answer content, outside reasoning and hub-next metadata. Use the existing results; "
        "do not repeat tools, effects or the completed report."
    )
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
        if not split_followup(observation.response.message.text).content.strip():
            return CompletionReview(ReviewAction.CONTINUE, cls.EMPTY_ANSWER)
        calls = {call.id: call for m in observation.original_messages for call in m.calls}
        if not calls:
            return CompletionReview(ReviewAction.ACCEPT, cls.ACCEPTED_FEEDBACK)
        if not any(tool.name == cls.NAME for tool in observation.tools):
            # Explicit Agent tool selection can opt out of the Hub report tool.
            return CompletionReview(ReviewAction.ACCEPT, cls.ACCEPTED_FEEDBACK)
        # Loop builds prior turns as plain messages. Its original (uncompacted)
        # transcript retains the current execution's actual call/result pairs.
        results = {m.result.call_id: m.result for m in observation.original_messages
                   if m.role == ModelRole.TOOL and m.result.call_id in calls}
        reports = [call for call in calls.values()
                   if call.name == cls.NAME and call.id in results]
        if not reports:
            return CompletionReview(ReviewAction.ACCEPT, cls.ACCEPTED_FEEDBACK)
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
