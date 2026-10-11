"""One display character per ready frame, without delaying backend execution."""

import asyncio
from dataclasses import replace
import unicodedata
from typing import TYPE_CHECKING

from ..model import ChatMessage

if TYPE_CHECKING:
    from prompt_toolkit.application import Application
    from .conversation import ConversationControl


def next_character(text: str, start: int) -> int:
    end = start + 1
    # Keep combining marks, emoji modifiers and joined emoji with their base.
    while end < len(text):
        char = text[end]
        if (unicodedata.combining(char) or char in ('\ufe0e', '\ufe0f', '\u200d')
                or text[end - 1] == '\u200d' or '\U0001f3fb' <= char <= '\U0001f3ff'
                or end == start + 1 and '\U0001f1e6' <= text[start] <= '\U0001f1ff'
                   and '\U0001f1e6' <= char <= '\U0001f1ff'):
            end += 1
        else:
            break
    return end


class CharacterStream:
    def __init__(self) -> None:
        self.ids: set[str] | None = None
        self.identifier: str | None = None
        self.text = ''
        self.shown = 0
        self.task: asyncio.Task | None = None

    @property
    def pending(self) -> bool:
        return self.shown < len(self.text)

    def reset(self, messages: tuple[ChatMessage, ...] = ()) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
        self.task = None
        self.ids = {m.id for m in messages}
        self.identifier, self.text, self.shown = None, '', 0

    def observe(self, messages: tuple[ChatMessage, ...]) -> None:
        candidate = next((m for m in reversed(messages) if m.role == 'assistant' and m.id), None)
        ids = {m.id for m in messages}
        if candidate is None or candidate.status in ('failed', 'interrupted', 'cancelled', 'paused'):
            self.reset(messages)
            return
        if candidate.id != self.identifier:
            animate = self.ids is not None and candidate.id not in self.ids
            self.identifier, self.text = candidate.id, candidate.text
            self.shown = 0 if animate else len(self.text)
        elif candidate.text.startswith(self.text):
            self.text = candidate.text
        else:
            self.text, self.shown = candidate.text, len(candidate.text)
        self.ids = ids

    def advance(self) -> bool:
        if not self.pending:
            return False
        self.shown = next_character(self.text, self.shown)
        return True

    def project(self, messages: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
        if not self.pending:
            return messages
        return tuple(replace(m, text=self.text[:self.shown], status='streaming', phase=m.phase or m.status)
                     if m.id == self.identifier else m for m in messages)

    def schedule(self, control: 'ConversationControl', app: 'Application') -> None:
        if self.pending and app.is_running and (self.task is None or self.task.done()):
            self.task = app.create_background_task(self._animate(control, app))

    async def _animate(self, control: 'ConversationControl', app: 'Application') -> None:
        rendered = app.render_counter - 1
        while self.pending:
            await asyncio.sleep(.01)
            if not control.animation_active():
                self.shown = len(self.text)
            else:
                if app.render_counter == rendered or control._cache_key != control._requested_key:
                    continue
                rendered = app.render_counter
                self.advance()
            if control.animation_follow() and control.top_line + control._height >= len(control._lines) - 1:
                control.follow_tail = True
            control._message_revision += 1
            app.invalidate()
