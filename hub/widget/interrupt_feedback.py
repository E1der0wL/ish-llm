"""Immediate stop feedback, scoped to the Run observed when the key was pressed."""

from dataclasses import dataclass, replace

from ..model import ChatMessage, HubSnapshot


@dataclass(frozen=True, slots=True)
class PendingInterrupt:
    key: tuple[str, str]
    run_id: str


class InterruptFeedback:
    def __init__(self, view):
        self.view = view
        self.pending: dict[tuple[str, str], PendingInterrupt] = {}

    def start(self, key: tuple[str, str]) -> PendingInterrupt | None:
        snapshot = getattr(self.view, "_last_snapshot", None)
        if (not snapshot or key != (snapshot.project_id, snapshot.selected_id)
                or not snapshot.run or not snapshot.run.active or snapshot.run.status not in ("pending", "running")):
            return None
        request = PendingInterrupt(key, snapshot.run.id)
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
        if request and (not run or not run.active or run.status not in ("pending", "running") or run.id != request.run_id):
            self.pending.pop(key)

    def project(self, key: tuple[str, str], messages: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
        request = self.pending.get(key)
        if not request:
            return messages
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.role == "assistant" and message.status in ("streaming", "committed", "preparing"):
                # Change only the header. Preserve text, reasoning, durable
                # status and the Markdown body cache; hide retry information.
                return (*messages[:index], replace(message, phase="interrupting", activity=""), *messages[index + 1:])
        # A Run can be active before its assistant message is published.
        return (*messages, ChatMessage("assistant", "", status="preparing", phase="interrupting",
                                      id=f"interrupting:{request.run_id}"))
