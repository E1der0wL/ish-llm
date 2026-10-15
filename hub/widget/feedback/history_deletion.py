"""Clear only the affected transcript while durable turn deletion completes."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PendingDeletion:
    key: tuple[str, str]
    task: int
    position: object


class HistoryDeletion:
    def __init__(self, view):
        self.view = view
        self.pending: dict[tuple[str, str], PendingDeletion] = {}

    def start(self, key, total):
        view = self.view
        request = PendingDeletion(key, view.progress.start(view.t("history_deleting"), total=total or None),
                                  view.output.reading_position())
        self.pending[key] = request
        if self.current(key):
            # Also cancel an older render already in flight, and its tag objects.
            view.output.set_messages(())
        view.preparing.paint()
        return request

    def current(self, key):
        return key == (getattr(self.view, "project_id", ""), self.view.sessions[self.view.selected].id)

    def update(self, request, completed, total):
        if self.pending.get(request.key) is request:
            self.view.progress.update(request.task, completed=completed, total=total)

    def finish(self, request):
        if self.pending.get(request.key) is not request:
            return
        self.pending.pop(request.key)
        self.view.progress.finish(request.task)
        if self.current(request.key):
            # Restored history is a new baseline, not a newly streamed answer.
            # Only deltas arriving after this snapshot should animate.
            session = self.view.sessions[self.view.selected]
            messages = self.view.preparing.project(request.key, session.messages)
            self.view.output.set_messages(messages, request.position)
        self.view.preparing.paint()

    def project(self, key, messages):
        return () if key in self.pending else messages
