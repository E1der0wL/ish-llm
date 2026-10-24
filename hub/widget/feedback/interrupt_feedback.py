"""Immediate stop feedback, including preparation before a Run is observed."""

from llm.core.models import RunStatus, MessageStatus, MessageRole
from dataclasses import dataclass, replace

from ...model import ChatMessage, HubSnapshot, SessionIdentity


@dataclass(slots=True)
class PendingInterrupt:
    key: tuple[str, str]
    run_id: str
    previous_run_id: str = ""


class InterruptFeedback:
    NO_REQUEST = None
    NO_RUN = None
    NO_RUN_ID = ""
    EMPTY_TEXT = ""
    NO_ACTIVITY = ""
    PHASE = "interrupting"
    IDENTIFIER = PHASE + ":{identifier}"
    WAITING_STATES = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED)
    PROJECTABLE = (
        MessageStatus.STREAMING,
        MessageStatus.COMMITTED,
        ChatMessage.PREPARING,
        MessageStatus.PAUSED,
    )

    def __init__(self, view):
        self.view = view
        self.pending: dict[tuple[str, str], PendingInterrupt] = {}

    def start(self, key: tuple[str, str]) -> PendingInterrupt | None:
        snapshot = HubSnapshot.last_observed(self.view)
        run = (
            snapshot.run
            if snapshot and key == (snapshot.project_id, snapshot.selected_id)
            else self.NO_RUN
        )
        running = run and run.status in self.WAITING_STATES
        if not running and key not in self.view.preparing.pending:
            return self.NO_REQUEST
        # The submit receipt and first Run snapshot may still be in flight.
        # Remember the previous Run so its terminal snapshot cannot dismiss
        # feedback for the request that is currently being prepared.
        request = PendingInterrupt(
            key,
            run.id if running else self.NO_RUN_ID,
            run.id if run else self.NO_RUN_ID,
        )
        self.pending[key] = request
        self.view.preparing.paint()
        return request

    def finish(self, request: PendingInterrupt | None, accepted: bool | None) -> None:
        # A successful command is still not a conversation projection. Keep
        # feedback until an authoritative snapshot observes the finished Run.
        if request and not accepted and self.pending.get(request.key) is request:
            self.pending.pop(request.key)
            self.view.preparing.paint()

    def observe(self, snapshot: HubSnapshot) -> None:
        sessions = {session.id for session in snapshot.sessions}
        self.pending = {
            key: request
            for key, request in self.pending.items()
            if SessionIdentity(key).project_id == snapshot.project_id
            and SessionIdentity(key).session_id in sessions
        }
        key = (snapshot.project_id, snapshot.selected_id)
        request = self.pending.get(key)
        run = snapshot.run
        if not request:
            return
        if not request.run_id:
            if not run or run.id == request.previous_run_id:
                return
            # Mutate the target in place: the command callback retains this
            # request's identity while the observed Run ID becomes available.
            request.run_id = run.id
        # Runtime active flags and durable status can straddle cancellation.
        # Wait for the target's terminal status, not just active=False.
        if not run or run.status not in self.WAITING_STATES or run.id != request.run_id:
            self.pending.pop(key)

    def project(
        self, key: tuple[str, str], messages: tuple[ChatMessage, ...]
    ) -> tuple[ChatMessage, ...]:
        request = self.pending.get(key)
        if not request:
            return messages
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if (
                message.role == MessageRole.ASSISTANT
                and message.status in self.PROJECTABLE
            ):
                # Change only the header. Preserve text, reasoning, durable
                # status and the Markdown body cache; hide retry information.
                return (
                    *messages[:index],
                    replace(message, phase=self.PHASE, activity=self.NO_ACTIVITY),
                    *messages[index + 1 :],
                )
        # A Run can be active before its assistant message is published.
        return (
            *messages,
            ChatMessage(
                MessageRole.ASSISTANT.value,
                self.EMPTY_TEXT,
                status=ChatMessage.PREPARING,
                phase=self.PHASE,
                id=self.IDENTIFIER.format(
                    identifier=request.run_id or SessionIdentity(key).session_id
                ),
            ),
        )
