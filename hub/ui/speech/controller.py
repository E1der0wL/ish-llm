"""Optional voice input uses normal Hub submission; published text alone is read."""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic

from llm.core.configuration import UNSET
from llm.core.models import MessageRole, MessageStatus
from hub.config.speech import SpeechConfig, SpeechStore
from hub.locales.keys import MessageKey
from .session import SpeechSession
from hub.backend.speech_resources import SpeechResources


class SpeechCommand(StrEnum):
    CONFIGURE = 'configure'
    START = 'start'
    FINISH = 'finish'
    CANCEL = 'cancel'
    QUIET = 'quiet'
    OUTPUT = 'output'


@dataclass(slots=True)
class SpokenOutput:
    message: object
    offset: int = 0
    draft_seen_at: float | None = None
    publication_seen: bool = False


class SpeechController:
    NEWLINE = '\n'
    STREAM_ID = '{run}:{message}:{offset}'
    NOTICE_HEIGHT = 3
    OUTPUT_DISABLED = 'Configure output and interaction.read_responses=true explicitly'
    PUBLICATION_READY = 'speech_publication_ready'
    def __init__(self, controller):
        self.controller, self.view = controller, controller.view
        self.store = SpeechStore(controller.config._workspace_anchor)
        self.session, self.config, self.snapshot = None, None, None
        self.diagnostics = None
        self.tasks, self.pending, self.outputs, self.seen = set(), {}, {}, set()
        self.scope, self.read_task, self.closed = None, None, False
        self.reading_message = None
        self._connection_lock = asyncio.Lock()

    def _launch(self, operation):
        async def run():
            try:
                await operation
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not self.closed:
                    self.controller._error(error)
        task = self.controller.app.create_background_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _connect(self):
        async with self._connection_lock:
            return await self._connect_unlocked()

    async def _connect_unlocked(self):
        if self.session is not None:
            return self.session
        config = await asyncio.to_thread(self.store.load)
        if config is None:
            raise ValueError(self.view.t(MessageKey.VOICE_CONFIG_REQUIRED))
        ready = asyncio.get_running_loop().create_future()
        def received(resources):
            if not ready.done():
                if resources is None:
                    ready.set_exception(RuntimeError(self.view.t(MessageKey.NOT_CONNECTED)))
                else:
                    ready.set_result(resources)
        self.controller._call(SpeechResources.OPERATION, completed=received)
        resources = await ready
        calls, diagnostics = resources.calls, resources.diagnostics
        session = await asyncio.to_thread(SpeechSession, config, calls)
        if self.closed:
            await session.aclose()
            raise asyncio.CancelledError
        self.config, self.session, self.diagnostics = config, session, diagnostics
        # Opening speech never reads previously stored conversation aloud.
        if self.snapshot is not None:
            self.seen = {message.id for message in self.snapshot.messages}
        return session

    def request_key(self, scope, text):
        pending = self.pending.get(scope)
        return pending[1] if pending is not None and pending[0] == text else UNSET

    def accepted(self, scope, text):
        if self.pending.get(scope, (None,))[0] == text:
            self.pending.pop(scope, None)

    def command(self, argument):
        if argument == SpeechCommand.CONFIGURE:
            self._launch(self._configure())
        elif argument == SpeechCommand.START:
            self._launch(self._listen())
        elif argument == SpeechCommand.FINISH:
            if self.session is not None:
                self._launch(self.session.finish_capture())
        elif argument == SpeechCommand.CANCEL:
            if self.session is not None:
                self.session.cancel_capture()
        elif argument == SpeechCommand.QUIET:
            self.stop_playback()
        elif argument == SpeechCommand.OUTPUT:
            self._launch(self._output())
        elif argument:
            self.view.notice = self.view.t(MessageKey.VOICE_COMMANDS)
        else:
            from prompt_toolkit.layout import HSplit, Window
            from prompt_toolkit.layout.controls import FormattedTextControl
            from hub.widget.controls import Button
            buttons = [Button(self.view.t(label), handler=lambda command=command: self._choose(command))
                       for label, command in ((MessageKey.VOICE_START, SpeechCommand.START),
                           (MessageKey.VOICE_FINISH, SpeechCommand.FINISH), (MessageKey.VOICE_CANCEL, SpeechCommand.CANCEL),
                           (MessageKey.VOICE_QUIET, SpeechCommand.QUIET), (MessageKey.VOICE_OUTPUT, SpeechCommand.OUTPUT),
                           (MessageKey.VOICE_CONFIGURE, SpeechCommand.CONFIGURE))]
            body = HSplit([Window(FormattedTextControl(self.view.t(MessageKey.VOICE_NOTICE)), height=SpeechController.NOTICE_HEIGHT), *buttons])
            self.view.open_dialog(self.view.t(MessageKey.VOICE_TITLE), body, lambda: None, buttons[0])

    def _choose(self, command):
        self.view.close_dialog()
        self.command(command)

    async def _output(self):
        await self._connect()
        if self.config.output is None or not self.config.behavior.read_responses:
            raise ValueError(SpeechController.OUTPUT_DISABLED)
        self.view.notice = self.view.t(MessageKey.VOICE_OUTPUT)

    async def _configure(self):
        from hub.ui.input.json_document import JsonDocument
        from hub.widget.controls import TextArea

        value = await asyncio.to_thread(self.store.get_config)
        if self.closed or not self.view.visible:
            return
        editor = TextArea(text=JsonDocument.render(value, allow_non_finite=False), multiline=True)
        def validate():
            try:
                candidate = JsonDocument.parse(editor.text)
                if candidate is not None:
                    SpeechConfig(**candidate)
                return True
            except (ValueError, TypeError) as error:
                self.view.notice = str(error)
                return False
        async def save(candidate):
            if candidate is not None:
                configuration = SpeechConfig(**candidate)
                # Selected adapter validates its own private options before save.
                await asyncio.to_thread(configuration.build_input)
                await asyncio.to_thread(configuration.build_output)
            async with self._connection_lock:
                self.stop_playback()
                if self.session is not None:
                    await self.session.aclose()
                await asyncio.to_thread(self.store.configure, candidate)
                self.session = self.config = None
                if not self.closed:
                    self.view.notice = self.view.t(MessageKey.SAVED)
        self.view.open_dialog(self.view.t(MessageKey.VOICE_CONFIGURE), editor,
            lambda: self._launch(save(JsonDocument.parse(editor.text))), editor, validate=validate)

    async def _listen(self):
        scope = self.current_scope()
        if not all(scope) or not self.view.connected:
            raise ValueError(self.view.t(MessageKey.SESSION_REQUIRED))
        session = await self._connect()
        if self.closed or not self.view.visible or scope != self.current_scope():
            return
        def partial(item):
            if not self.closed and scope == self.current_scope():
                self.view.notice = self.view.t(MessageKey.VOICE_PARTIAL, text=item.text)
                self.controller.app.invalidate()
        def final(item):
            if self.closed or scope != self.current_scope() or not self.view.visible:
                return
            prefix = self.view.composer.text
            text = prefix + (self.NEWLINE if prefix else '') + item.text
            self.pending[scope] = (text, item.stream_id)
            self.view.composer.text = text
            self.view._drafts[scope[1]] = text
            # Voice does not interpret yes/no as approval or choose a Graph target.
            # Those actions retain the same explicit interactive routing as text.
            if (self.config.behavior.submit == SpeechConfig.AUTOMATIC
                    and self.view._dialog is None and self.view.question.item is None
                    and not self.view.settings_open):
                self.controller.submit(scope[1], text)
            else:
                self.view.notice = self.view.t(MessageKey.VOICE_CONFIRM)
            self.controller.app.invalidate()
        self.view.notice = self.view.t(MessageKey.VOICE_LISTENING)
        from llm.providers.runtime import logging_scope
        with logging_scope(self.controller.config._workspace_anchor, project_id=scope[0], session_id=scope[1],
                           diagnostics=self.diagnostics):
            await session.listen(partial=partial, final=final, speaking=self.stop_playback)

    def current_scope(self):
        from hub.model import SessionIdentity
        return SessionIdentity.current(self.view).to_tuple() if self.view.sessions else (None, None)

    def observe(self, snapshot):
        self.snapshot = snapshot
        scope = (snapshot.project_id, snapshot.selected_id)
        if scope != self.scope:
            self.hide()
            self.scope = scope
            self.seen = {message.id for message in snapshot.messages}
        if (self.closed or self.config is None or not self.view.visible
                or not self.config.behavior.read_responses):
            return
        for message in snapshot.messages:
            if message.role != MessageRole.ASSISTANT or not message.run_id:
                continue
            if message.status in (MessageStatus.CANCELLED, MessageStatus.INTERRUPTED, MessageStatus.FAILED):
                self.seen.add(message.id)
                self.outputs.pop(message.id, None)
                if self.reading_message == message.id:
                    self.session.stop_playback()
                    if self.read_task is not None:
                        self.read_task.cancel()
                continue
            if message.id not in self.seen:
                self.seen.add(message.id)
                self.outputs[message.id] = SpokenOutput(message)
            elif message.id in self.outputs:
                previous = self.outputs[message.id]
                if not message.text.startswith(previous.message.text[:previous.offset]):
                    # Replaced/rolled-back text must never be spliced into old audio.
                    self.stop_playback()
                    break
                previous.message = message
            output = self.outputs.get(message.id)
            if output is not None and not output.publication_seen:
                if message.draft is not None and output.draft_seen_at is None:
                    output.draft_seen_at = monotonic()
                if message.text:
                    output.publication_seen = True
                    from llm.providers.runtime import logging_scope, diagnostic
                    with logging_scope(self.controller.config._workspace_anchor,
                                       project_id=scope[0], session_id=scope[1], run_id=message.run_id):
                        diagnostic(SpeechController.PUBLICATION_READY, message_id=message.id,
                                   observed_draft_wait_seconds=(None if output.draft_seen_at is None
                                       else monotonic() - output.draft_seen_at))
        self._start_reader()

    def _start_reader(self):
        if self.closed or not self.view.visible or self.read_task is not None or not self.outputs:
            return
        output = next(iter(self.outputs.values()))
        if (output.message.status in (MessageStatus.COMMITTED, MessageStatus.STREAMING)
                and len(output.message.text) - output.offset < self.config.behavior.text_chunk_chars):
            return
        self.read_task = self._launch(self._read())
        self.read_task.add_done_callback(lambda _: self._start_reader())

    async def _read(self):
        try:
            while self.outputs:
                identifier, output = next(iter(self.outputs.items()))
                message = output.message
                final = message.status not in (MessageStatus.COMMITTED, MessageStatus.STREAMING)
                available = len(message.text) - output.offset
                maximum = self.config.behavior.text_chunk_chars
                if available < maximum and not final:
                    return
                if available:
                    end = min(len(message.text), output.offset + maximum)
                    text = message.text[output.offset:end]
                    # Only published conversation text is audible. message.draft
                    # is unapproved observation and is deliberately never read.
                    self.reading_message = identifier
                    from llm.providers.runtime import logging_scope
                    with logging_scope(self.controller.config._workspace_anchor,
                                       project_id=self.scope[0], session_id=self.scope[1], run_id=message.run_id,
                                       diagnostics=self.diagnostics):
                        await self.session.speak(text, stream_id=self.STREAM_ID.format(run=message.run_id, message=identifier, offset=output.offset))
                    self.reading_message = None
                    output.offset = end
                elif final:
                    self.outputs.pop(identifier, None)
                else:
                    return
        except asyncio.CancelledError:
            if self.reading_message is not None:
                self.outputs.pop(self.reading_message, None)
            raise
        except Exception:
            # An output failure is not permission to automatically retry speech.
            self.outputs.clear()
            raise
        finally:
            self.reading_message = None
            self.read_task = None

    def stop_playback(self):
        self.outputs.clear()
        if self.session is not None:
            self.session.stop_playback()
        if self.read_task is not None:
            self.read_task.cancel()

    def hide(self):
        self.stop_playback()
        if self.session is not None:
            self.session.cancel_capture()

    def close(self):
        self.closed = True
        self.hide()
        for task in tuple(self.tasks):
            task.cancel()

    async def aclose(self):
        self.close()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self.session is not None:
            await self.session.aclose()
