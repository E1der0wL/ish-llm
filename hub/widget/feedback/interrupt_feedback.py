"""Immediate stop feedback, including preparation before a Run is observed."""

from llm.core.models import RunStatus, MessageStatus
from dataclasses import dataclass, replace

from ...model import ChatMessage, HubSnapshot


@dataclass(slots=True)
class PendingInterrupt:
    key: tuple[str, str]
    run_id: str
    previous_run_id: str = ""


class InterruptFeedback:
    def __init__(self, view):
        self.view = view
        self.pending: dict[tuple[str, str], PendingInterrupt] = {}

    def start(self, key: tuple[str, str]) -> PendingInterrupt | None:
        snapshot = getattr(self.view, "_last_snapshot", None)
        run = snapshot.run if snapshot and key == (snapshot.project_id, snapshot.selected_id) else None
        running = run and run.status in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED)
        if not running and key not in self.view.preparing.pending:
            return None
        # The submit receipt and first Run snapshot may still be in flight.
        # Remember the previous Run so its terminal snapshot cannot dismiss
        # feedback for the request that is currently being prepared.
        request = PendingInterrupt(key, run.id if running else "", run.id if run else "")
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
        self.pending = {key: request for key, request in self.pending.items()
                        if key[0] == snapshot.project_id and key[1] in sessions}
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
        if not run or run.status not in (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED) or run.id != request.run_id:
            self.pending.pop(key)

    def project(self, key: tuple[str, str], messages: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
        request = self.pending.get(key)
        if not request:
            return messages
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role == "assistant" and message.status in (MessageStatus.STREAMING, MessageStatus.COMMITTED, ChatMessage.PREPARING, MessageStatus.PAUSED):
                # Change only the header. Preserve text, reasoning, durable
                # status and the Markdown body cache; hide retry information.
                return (*messages[:index], replace(message, phase="interrupting", activity=""), *messages[index + 1:])
        # A Run can be active before its assistant message is published.
        return (*messages, ChatMessage("assistant", "", status=ChatMessage.PREPARING, phase="interrupting",
                                      id=f"interrupting:{request.run_id or key[1]}"))
