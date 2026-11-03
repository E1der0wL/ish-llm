"""Local steering feedback, replaced by the backend's durable instruction receipt."""

from dataclasses import dataclass, replace
from uuid import uuid4

from llm.core.models import MessageRole
from llm.core.steering import InstructionStatus

from ...model import ChatMessage, HubSnapshot, InstructionDisplay
from ...locales.keys import MessageKey


@dataclass(slots=True)
class PendingInstruction:
    key: tuple[str, str]
    parent: str | None
    display: InstructionDisplay
    receipt: str | None = None


class PreparingInstructions:
    def __init__(self, view):
        self.view = view
        self.pending = {}

    def start(self, key, text, run_id):
        parent = None
        snapshot = HubSnapshot.last_observed(self.view)
        if snapshot and key == (snapshot.project_id, snapshot.selected_id):
            preceding = None
            for message in snapshot.messages:
                if message.role == MessageRole.USER:
                    preceding = message.id
                elif message.role == MessageRole.ASSISTANT and message.run_id == run_id:
                    parent = preceding
                    break
        token = 'instruction-draft:' + uuid4().hex
        self.pending[token] = PendingInstruction(
            key, parent, InstructionDisplay(token, text, ChatMessage.PREPARING))
        self.view.notice = self.view.t(MessageKey.INSTRUCTION_PREPARING)
        self.view.preparing.paint()
        return token

    def finish(self, token):
        self.pending.pop(token, None)
        if self.view.notice == self.view.t(MessageKey.INSTRUCTION_PREPARING):
            self.view.notice = ''
        self.view.preparing.paint()

    def accepted(self, token, receipt):
        pending = self.pending.get(token)
        if pending is None:
            return
        pending.receipt = receipt
        pending.display = replace(pending.display, id=receipt, status=InstructionStatus.PENDING.value)
        snapshot = HubSnapshot.last_observed(self.view)
        if snapshot is not None:
            self.observe(snapshot)
        self.view.preparing.paint()

    def observe(self, snapshot):
        if not self.pending:
            return
        key = (snapshot.project_id, snapshot.selected_id)
        known = {message.id for message in snapshot.messages}
        known.update(item.id for message in snapshot.messages for item in message.instructions)
        sessions = {session.id for session in snapshot.sessions}
        for token, pending in tuple(self.pending.items()):
            if (pending.key == key and pending.receipt in known
                    or pending.key[0] == snapshot.project_id and pending.key[1] not in sessions):
                self.pending.pop(token)

    def project(self, key, messages):
        active = [item for item in self.pending.values() if item.key == key]
        if not active:
            return messages
        known = {message.id for message in messages}
        receipts = {item.id for message in messages for item in message.instructions}
        result = list(messages)
        for pending in active:
            if pending.receipt in known or pending.receipt in receipts:
                continue
            for index, message in enumerate(result):
                if message.id == pending.parent and message.role == MessageRole.USER:
                    result[index] = replace(message, instructions=(*message.instructions, pending.display))
                    break
            else:
                # A windowed history may not include the parent. Never attach to
                # another user's request or claim local feedback was persisted.
                result.append(ChatMessage(MessageRole.USER.value, pending.display.text,
                    status=pending.display.status, id=pending.display.id,
                    author=self.view.user_profile.display_name))
        return tuple(result)
