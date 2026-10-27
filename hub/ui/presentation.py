"""Pure localized display transformations, executed on the UI thread."""

from hub.model import ResponseActivity

from hub.locales.keys import MessageKey, MessagePattern

from llm.core.models import MessageStatus, RunStatus, MessageRole, ConversationStorage
from typing import NamedTuple
from dataclasses import dataclass, replace
from datetime import datetime

from ..model import ChatMessage, HubSnapshot, SessionSummary, QuestionPresentation, ReasoningMessage, TraceItem
from ..backend.execution.followup import split_followup
from .error_presentation import error_text, failure_text


class PresentationFormat:
    ABSENT = None
    EMPTY = ""
    FINAL = True
    FAILURE_SUFFIX = ":failure"
    WORKFLOW_SUFFIX = ":workflow"
    SUMMARY_SUFFIX = ":summary"
    FAILURE_DETAILS = "\n\n"
    TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    NOTICE_SEPARATOR = " · "
    RUN_ERROR = "{status}: {error}"


class PresentationKey(NamedTuple):
    project_id: str
    session_id: str
    language: int
    icons: object


class MessageProjection(NamedTuple):
    source: ChatMessage
    display: ChatMessage
    partial: object


@dataclass(frozen=True, slots=True)
class Presentation:
    sessions: tuple[SessionSummary, ...]
    messages: tuple[ChatMessage, ...]
    notice: str


def present_message(item: ChatMessage, t) -> ChatMessage:
    if item.workflow:
        graph = TraceItem(
            item.id + PresentationFormat.WORKFLOW_SUFFIX, TraceItem.WORKFLOW, item.workflow,
            workflow_states=item.workflow_states,
        )
        trace = item.trace or ((TraceItem.thought(item.id, item.text),) if item.text else ())
        item = replace(item, trace=(graph, *trace))
    if item.role == ReasoningMessage.ROLE and item.phase == ResponseActivity.SUMMARIZING:
        trace = item.trace or ((TraceItem.thought(item.id, item.text),) if item.text else ())
        item = replace(item, trace=(*trace, TraceItem(
            item.id + PresentationFormat.SUMMARY_SUFFIX, TraceItem.SUMMARY, PresentationFormat.EMPTY)))
    text = item.text
    if item.role == MessageRole.ASSISTANT:
        text = split_followup(
            text,
            final=item.status not in (MessageStatus.STREAMING, MessageStatus.COMMITTED),
        ).content
    if (
        item.role == MessageRole.ASSISTANT
        and not text
        and item.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED)
    ):
        return replace(
            item,
            text=PresentationFormat.EMPTY,
            phase=item.phase or ResponseActivity.PREPARING,
        )
    if item.role == MessageRole.ASSISTANT and item.status == MessageStatus.FAILED:
        text = t(MessageKey.RESPONSE_FAILED)
        if item.failure is not PresentationFormat.ABSENT:
            text += PresentationFormat.FAILURE_DETAILS + failure_text(item.failure, t)
        item = replace(
            item,
            role=MessageRole.SYSTEM,
            id=(
                item.id + PresentationFormat.FAILURE_SUFFIX
                if item.id
                else PresentationFormat.EMPTY
            ),
            tools=(),
            trace=(),
            phase=PresentationFormat.EMPTY,
            activity=PresentationFormat.EMPTY,
        )
    elif not text and not item.tools and not item.trace:
        text = (
            t(MessagePattern.status(item.status))
            if item.status
            in (
                MessageStatus.INTERRUPTED,
                MessageStatus.CANCELLED,
                MessageStatus.PAUSED,
                MessageStatus.COMPLETED,
            )
            else t(MessageKey.WAITING)
        )
    return replace(
        item,
        text=text,
        time=(
            datetime.fromisoformat(item.time)
            .astimezone()
            .strftime(PresentationFormat.TIME_FORMAT)
            if item.time
            else PresentationFormat.EMPTY
        ),
    )


class MessagePresentationCache:
    """Only retain projections for the currently selected conversation."""
    ABSENT = None
    INITIAL_COUNT = 0
    COUNT_STEP = 1

    def __init__(self):
        self.context = MessagePresentationCache.ABSENT
        self.items = {}
        self.transformed = MessagePresentationCache.INITIAL_COUNT

    def messages(self, snapshot, t):
        context = PresentationKey(
            snapshot.project_id, snapshot.selected_id, id(t), t.icons
        )
        previous = self.items if context == self.context else {}
        retained, messages = {}, []
        for index, item in enumerate(snapshot.messages):
            key = item.id or index
            cached = previous.get(key)
            if cached is MessagePresentationCache.ABSENT or cached.source != item:
                # A clock tick does not change Markdown/metadata parsing.
                if (
                    cached
                    and replace(cached.source, elapsed_seconds=item.elapsed_seconds)
                    == item
                ):
                    projected = replace(
                        cached.display, elapsed_seconds=item.elapsed_seconds
                    )
                    partial = (
                        replace(cached.partial, elapsed_seconds=item.elapsed_seconds)
                        if cached.partial
                        else MessagePresentationCache.ABSENT
                    )
                else:
                    projected, partial = (
                        present_message(item, t),
                        MessagePresentationCache.ABSENT,
                    )
                    if (
                        item.role == MessageRole.ASSISTANT
                        and item.status == MessageStatus.FAILED
                        and item.text.strip()
                    ):
                        # Keep model output separate; parse it only when changed.
                        text = split_followup(
                            item.text, final=PresentationFormat.FINAL
                        ).content
                        partial = replace(
                            item, text=text, failure=PresentationFormat.ABSENT
                        )
                cached = MessageProjection(item, projected, partial)
                self.transformed += MessagePresentationCache.COUNT_STEP
            retained[key] = cached
            if cached.partial is not MessagePresentationCache.ABSENT:
                messages.append(cached.partial)
            messages.append(cached.display)
        self.context, self.items = context, retained
        return tuple(messages)


def present(snapshot: HubSnapshot, t, cache=PresentationFormat.ABSENT) -> Presentation:
    sessions = tuple(
        replace(
            item,
            status=t(
                MessageKey.QUEUED, status=t.status(item.status), count=item.queued_count
            ),
        )
        for item in snapshot.sessions
    )
    messages = (cache or MessagePresentationCache()).messages(snapshot, t)
    if not sessions:
        return Presentation((), messages, t(MessageKey.SESSION_REQUIRED))
    notice = t(
        MessageKey.QUEUED, status=t.status(snapshot.status), count=snapshot.queued_count
    )
    run = snapshot.run
    if run:
        if run.error:
            notice = PresentationFormat.RUN_ERROR.format(
                status=t.status(run.status), error=run.error
            )
        elif run.status == RunStatus.PAUSED:
            notice = t(MessageKey.PAUSED)
    if snapshot.status in (
        SessionSummary.WAITING_INPUT,
        SessionSummary.WAITING_APPROVAL,
    ):
        status = t.status(snapshot.status)
        if snapshot.questions:
            status += (
                PresentationFormat.NOTICE_SEPARATOR
                + QuestionPresentation.observe(snapshot.questions[0]).request.title
            )
        notice = t(MessageKey.QUEUED, status=status, count=snapshot.queued_count)
    if snapshot.storage == ConversationStorage.MEMORY:
        notice += PresentationFormat.NOTICE_SEPARATOR + t(MessageKey.MEMORY)
    if snapshot.title_error:
        notice = t(MessageKey.TITLE_FAILED, error=error_text(snapshot.title_error))
    if snapshot.model_required:
        notice = t(MessageKey.MODEL_REQUIRED)
    return Presentation(sessions, messages, notice)
